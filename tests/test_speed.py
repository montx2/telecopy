"""Velocidade: sem tetos artificiais, flood que recupera e premium wait inofensivo."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import config
import copier as copier_module
import fasttransfer as ft
from config import (
    MAX_CONNECTIONS_PER_FILE,
    MAX_TOTAL_CONNECTIONS_CAP,
    WORKERS_HARD_CAP,
    RunConfig,
    turbo_settings,
)
from copier import Copier, DailyLimitReached
from database import Repository
from fasttransfer import PART_SIZE, FastTransferPool, fast_download, fast_upload
from telethon.errors import FloodPremiumWaitError, FloodWaitError
from tests.fake_telegram import FakeClient, channel
from tests.test_ordem import tl_video
from utils import DailyLimit


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TEMP_DIR", tmp_path / "tmp")
    monkeypatch.setattr(copier_module, "TEMP_DIR", tmp_path / "tmp")
    monkeypatch.setattr(copier_module, "daily_limit", DailyLimit(tmp_path / "daily.json"))
    repo = Repository(tmp_path / "test.db")
    try:
        yield SimpleNamespace(root=tmp_path, repo=repo)
    finally:
        repo.close()


@pytest.fixture
def no_sleep(monkeypatch):
    async def sleep(_delay):
        return None

    monkeypatch.setattr("fasttransfer.asyncio.sleep", sleep)
    monkeypatch.setattr("copier.asyncio.sleep", sleep)


def payload(size: int) -> bytes:
    block = bytes(range(256))
    return (block * (size // len(block) + 1))[:size]


# ── FLOOD_PREMIUM_WAIT não pode custar velocidade ─────────────────────────────

def test_premium_wait_espera_sem_reduzir_conexoes_nem_cair_no_fallback(env, no_sleep) -> None:
    size = 2 * PART_SIZE + 77
    data = payload(size)
    msg = tl_video(1, size=size)
    client = FakeClient([msg])
    client.file_data[msg.media.document.id] = data
    client.part_errors = {
        ("GetFileRequest", PART_SIZE): [FloodPremiumWaitError(request=None, capture=7)],
    }
    pool = FastTransferPool(client, max_total_connections=8, connections_per_file=4)
    path = env.root / "out.bin"

    async def scenario():
        try:
            await fast_download(pool, msg, path)
        finally:
            await pool.close()

    asyncio.run(scenario())
    assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == hashlib.sha256(data).hexdigest()
    # Esperou o teto da conta, mas manteve todo o paralelismo e o caminho rápido.
    assert pool.capacity == 8
    assert pool.halvings == 0
    assert client.downloads == []


def test_premium_wait_no_upload_mantem_capacidade(env, no_sleep) -> None:
    data = payload(PART_SIZE + 512)
    src = env.root / "in.bin"
    src.write_bytes(data)
    client = FakeClient([])
    client.part_errors = {
        ("SaveFilePartRequest", 0): [FloodPremiumWaitError(request=None, capture=5)],
    }
    pool = FastTransferPool(client, max_total_connections=6, connections_per_file=3)

    async def scenario():
        try:
            return await fast_upload(pool, src, "in.bin")
        finally:
            await pool.close()

    handle = asyncio.run(scenario())
    assert handle is not None
    assert pool.capacity == 6 and pool.halvings == 0


def test_floodwait_comum_ainda_protege_e_depois_recupera(env, no_sleep) -> None:
    size = PART_SIZE + 10
    data = payload(size)
    msg = tl_video(1, size=size)
    client = FakeClient([msg])
    client.file_data[msg.media.document.id] = data
    client.part_errors = {("GetFileRequest", 0): [FloodWaitError(request=None, capture=4)]}
    pool = FastTransferPool(client, max_total_connections=8, connections_per_file=4)

    async def scenario():
        try:
            await fast_download(pool, msg, env.root / "out.bin")
        finally:
            await pool.close()

    asyncio.run(scenario())
    assert pool.capacity == 4 and pool.halvings == 1

    # Depois de envios estáveis a capacidade volta sozinha ao valor inicial.
    for _ in range(ft.RECOVERY_SUCCESSES):
        pool.note_success()
    assert pool.capacity == 5 and pool.recoveries == 1
    for _ in range(ft.RECOVERY_SUCCESSES * 8):
        pool.note_success()
    assert pool.capacity == pool.initial_capacity == 8


def test_timeouts_isolados_nao_cortam_conexoes() -> None:
    pool = FastTransferPool(FakeClient([]), max_total_connections=8)
    for _ in range(ft.TIMEOUTS_BEFORE_PENALTY - 1):
        pool.note_timeout()
    assert pool.capacity == 8
    pool.note_timeout()
    assert pool.capacity == 4


# ── Tetos que travavam a máquina ──────────────────────────────────────────────

def test_limites_de_paralelismo_foram_abertos() -> None:
    cfg = RunConfig(delay=0, workers=12, connections_per_file=16, max_total_connections=128)
    assert cfg.delay == 0.0
    assert cfg.workers == 12
    assert cfg.connections_per_file == 16
    assert cfg.max_total_connections == 128
    assert RunConfig(connections_per_file=999).connections_per_file == MAX_CONNECTIONS_PER_FILE
    assert RunConfig(max_total_connections=9999).max_total_connections == MAX_TOTAL_CONNECTIONS_CAP
    assert RunConfig(workers=999).workers == WORKERS_HARD_CAP
    # O pool aceita mais de 8 conexões por arquivo (antes era teto fixo).
    pool = FastTransferPool(FakeClient([]), connections_per_file=16)
    assert pool.connections_per_file == 16


def test_pre_upload_nao_e_limitado_a_metade_dos_workers(env, no_sleep) -> None:
    client = FakeClient([])
    copier = Copier(client, repo=env.repo,
                    config=RunConfig(workers=6, use_dedup=False), rich_ok=False)
    captured = {}
    original = asyncio.Semaphore

    def spy(value):
        captured["gate"] = value
        return original(value)

    async def scenario():
        with patch("copier.asyncio.Semaphore", spy):
            await copier._copy_loop([], None, None, 1, 2, None, {},
                                    copier_module.TransferStats(),
                                    copier_module.ProgressDisplay(0, "", False))

    asyncio.run(scenario())
    assert captured["gate"] >= 6


def test_turbo_respeita_hardware_e_preserva_invariantes() -> None:
    values = turbo_settings()
    cfg = RunConfig(**values)
    assert cfg.fast_transfer is True
    assert 2 <= cfg.workers <= WORKERS_HARD_CAP
    assert 4 <= cfg.connections_per_file <= MAX_CONNECTIONS_PER_FILE
    assert cfg.max_total_connections >= cfg.connections_per_file
    assert cfg.delay <= 0.2
    assert cfg.daily_limit == 0
    # Velocidade nunca custa ordem, dedup nem retomada.
    assert cfg.strict_order is True and cfg.resume is True
    assert cfg.delivery == "upload"


# ── Parede diária local ───────────────────────────────────────────────────────

def test_teto_diario_zero_nao_interrompe_a_copia(env) -> None:
    counter = DailyLimit(env.root / "daily.json")
    counter.add(5000)
    assert counter.status(0)[0] == "ok"
    assert counter.status(1000)[0] == "limit"
    copier = Copier(FakeClient([]), repo=env.repo,
                    config=RunConfig(daily_limit=0), rich_ok=False)
    with patch("copier.daily_limit", counter):
        copier._check_limit()          # sem teto: não levanta
        copier.cfg.daily_limit = 1000
        with pytest.raises(DailyLimitReached):
            copier._check_limit()


# ── Fallback também tem que ser rápido ────────────────────────────────────────

def test_fallback_de_download_usa_partes_grandes(env, no_sleep) -> None:
    msg = tl_video(1, size=4096)
    client = FakeClient([msg])
    seen = {}

    async def download_file(location, file, **kwargs):
        seen.update(kwargs)
        Path(file).write_bytes(b"x" * 4096)
        return str(file)

    client.download_file = download_file
    copier = Copier(client, repo=env.repo, config=RunConfig(), rich_ok=False)
    asyncio.run(copier._download_media(msg, str(env.root / "f.bin"), None))
    assert seen["part_size_kb"] == PART_SIZE // 1024
    assert client.downloads == []


def test_fallback_de_upload_usa_partes_grandes(env, no_sleep) -> None:
    msg = tl_video(1, size=4096)
    client = FakeClient([msg])
    seen = {}
    original = client.upload_file

    async def upload_file(path, **kwargs):
        seen.update(kwargs)
        return await original(path, **kwargs)

    client.upload_file = upload_file
    path = env.root / "up.bin"
    path.write_bytes(b"y" * 4096)
    copier = Copier(client, repo=env.repo, config=RunConfig(fast_transfer=False), rich_ok=False)
    asyncio.run(copier._upload_file({"msg": msg, "path": str(path), "media_type": "video"}))
    assert seen["part_size_kb"] == PART_SIZE // 1024


def test_premium_wait_nao_aborta_a_copia_mas_flood_comum_aborta() -> None:
    # Teto de velocidade da conta não é falha estrutural: espera e segue.
    assert Copier._is_fatal_send_error(FloodPremiumWaitError(request=None, capture=9)) is False
    assert Copier._is_fatal_send_error(FloodWaitError(request=None, capture=9)) is True


def test_teto_diario_customizado_ainda_interrompe_sem_marcar_falha() -> None:
    # A detecção não pode depender do texto com o limite padrão embutido.
    assert Copier._is_fatal_send_error(DailyLimitReached("teto 250 atingido")) is True

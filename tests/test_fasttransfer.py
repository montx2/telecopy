"""Etapa B: transporte multi-conexão, fallback, flood e remontagem por offset."""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import config
import copier as copier_module
from config import RunConfig
from copier import Copier
from database import Repository
from fasttransfer import (
    PART_SIZE,
    FastTransferError,
    FastTransferPool,
    fast_download,
    fast_upload,
)
from telethon.errors import (
    FileMigrateError,
    FilePartsInvalidError,
    FileReferenceExpiredError,
    FloodWaitError,
)
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


def test_download_remonta_fora_de_ordem_com_ultimo_bloco_parcial(env, no_sleep) -> None:
    size = 2 * PART_SIZE + 123
    data = payload(size)
    msg = tl_video(1, size=size)
    client = FakeClient([msg])
    client.file_data[msg.media.document.id] = data
    # blocos terminam na ordem inversa ao offset
    client.part_delays = {
        ("GetFileRequest", 0): 0.03,
        ("GetFileRequest", PART_SIZE): 0.02,
        ("GetFileRequest", 2 * PART_SIZE): 0.01,
    }
    pool = FastTransferPool(client, max_total_connections=4, connections_per_file=4)
    progress = []
    path = env.root / "out.bin"

    async def scenario():
        await fast_download(pool, msg, path, progress_callback=lambda c, t: progress.append((c, t)))
        await pool.close()

    asyncio.run(scenario())
    assert path.read_bytes() == data
    assert hashlib.sha256(path.read_bytes()).hexdigest() == hashlib.sha256(data).hexdigest()
    assert progress[-1] == (size, size)
    assert [c for c, _ in progress] == sorted(c for c, _ in progress)
    offsets = [r.offset for r in client.part_calls if type(r).__name__ == "GetFileRequest"]
    assert sorted(offsets) == [0, PART_SIZE, 2 * PART_SIZE]
    last = [r for r in client.part_calls if getattr(r, "offset", None) == 2 * PART_SIZE][0]
    assert last.limit == PART_SIZE  # pedido alinhado; resposta parcial no EOF
    assert all(sender.disconnected for sender in client.created_senders)


def test_download_floodwait_no_meio_de_parte_reduz_conexoes(env, no_sleep) -> None:
    size = PART_SIZE + 77
    data = payload(size)
    msg = tl_video(1, size=size)
    client = FakeClient([msg])
    client.file_data[msg.media.document.id] = data
    client.part_errors[("GetFileRequest", PART_SIZE)] = [FloodWaitError(request=None, capture=1)]
    pool = FastTransferPool(client, max_total_connections=4, connections_per_file=4)
    path = env.root / "flood.bin"

    async def scenario():
        await fast_download(pool, msg, path)
        await pool.close()

    asyncio.run(scenario())
    assert path.read_bytes() == data
    assert pool.halvings >= 1 and pool.capacity == 2


def test_download_file_migrate_e_refresh_de_referencia(env, no_sleep) -> None:
    size = 4096
    data = payload(size)
    msg = tl_video(1, size=size)
    client = FakeClient([msg])
    client.file_data[msg.media.document.id] = data
    client.part_errors[("GetFileRequest", 0)] = [
        FileMigrateError(request=None, capture=3),
        FileReferenceExpiredError(request=None),
    ]
    pool = FastTransferPool(client, max_total_connections=4, connections_per_file=2)
    refreshes = []

    async def refresh():
        refreshes.append(1)
        return msg

    async def scenario():
        await fast_download(pool, msg, env.root / "migrate.bin", refresh=refresh)
        await pool.close()

    asyncio.run(scenario())
    assert (env.root / "migrate.bin").read_bytes() == data
    assert refreshes == [1]
    assert {s.dc_id for s in client.created_senders} >= {2, 3}


def test_upload_pequeno_retorna_inputfile_com_md5(env, no_sleep) -> None:
    size = PART_SIZE + 999
    data = payload(size)
    path = env.root / "small.bin"
    path.write_bytes(data)
    client = FakeClient([])
    client.part_delays = {("SaveFilePartRequest", 0): 0.02, ("SaveFilePartRequest", 1): 0.01}
    pool = FastTransferPool(client, max_total_connections=4, connections_per_file=4)

    async def scenario():
        handle = await fast_upload(pool, path, "small.bin")
        await pool.close()
        return handle

    handle = asyncio.run(scenario())
    assert type(handle).__name__ == "InputFile"
    assert handle.md5_checksum == hashlib.md5(data).hexdigest()
    stored = client.part_store[handle.id]
    assert b"".join(stored[i] for i in sorted(stored)) == data
    assert all(type(r).__name__ == "SaveFilePartRequest" for r in client.part_calls)


def test_upload_grande_retorna_inputfilebig(env, no_sleep) -> None:
    size = 10 * 1024 * 1024 + PART_SIZE + 3
    data = payload(size)
    path = env.root / "big.bin"
    path.write_bytes(data)
    client = FakeClient([])
    pool = FastTransferPool(client, max_total_connections=3, connections_per_file=3)

    async def scenario():
        handle = await fast_upload(pool, path, "big.bin")
        await pool.close()
        return handle

    handle = asyncio.run(scenario())
    assert type(handle).__name__ == "InputFileBig"
    stored = client.part_store[handle.id]
    assert b"".join(stored[i] for i in sorted(stored)) == data
    assert all(type(r).__name__ == "SaveBigFilePartRequest" for r in client.part_calls)
    assert client.peak_parts <= 3


def test_upload_floodwait_e_limite_de_partes(env, no_sleep) -> None:
    path = env.root / "flood-up.bin"
    path.write_bytes(payload(2 * PART_SIZE + 1))
    client = FakeClient([])
    client.part_errors[("SaveFilePartRequest", 1)] = [FloodWaitError(request=None, capture=1)]
    pool = FastTransferPool(client, max_total_connections=4, connections_per_file=4)

    async def scenario():
        handle = await fast_upload(pool, path, "f.bin")
        await pool.close()
        return handle

    handle = asyncio.run(scenario())
    assert handle.parts == 3
    assert pool.halvings >= 1

    client2 = FakeClient([])
    client2.part_errors[("SaveFilePartRequest", 0)] = [FilePartsInvalidError(request=None)]
    pool2 = FastTransferPool(client2, max_total_connections=2, connections_per_file=2)

    async def failing():
        try:
            await fast_upload(pool2, path, "f.bin")
        finally:
            await pool2.close()

    with pytest.raises(FastTransferError):
        asyncio.run(failing())


def test_pool_orcamento_global_e_fechamento(env) -> None:
    client = FakeClient([])
    pool = FastTransferPool(client, max_total_connections=2, connections_per_file=8)

    async def scenario():
        a = await pool.acquire(2)
        b = await pool.acquire(2)
        waiting = asyncio.create_task(pool.acquire(2))
        await asyncio.sleep(0)
        assert not waiting.done()
        await pool.release(a)
        c = await asyncio.wait_for(waiting, 1)
        await pool.release(b)
        await pool.release(c)
        pool.penalize("teste")
        assert pool.capacity == 1
        await pool.close()

    asyncio.run(scenario())
    assert all(sender.disconnected for sender in client.created_senders)


def test_copier_fallback_download_quando_rapido_falha(env, no_sleep) -> None:
    msg = tl_video(1, size=4096)
    client = FakeClient([msg])
    source, dest = channel(-1, "S"), channel(-2, "D")
    client.register_entity(source)
    client.register_entity(dest)
    client.part_errors[("GetFileRequest", 0)] = [OSError("rede caiu")] * 10
    copier = Copier(client, repo=env.repo, config=RunConfig(use_dedup=False), rich_ok=False)
    copier._transfer_pool = FastTransferPool(client, max_total_connections=2, connections_per_file=2)
    path = env.root / "fallback.bin"

    async def scenario():
        result = await copier._download_media(msg, str(path), lambda c, t: None,
                                              source=source, entry={})
        await copier._transfer_pool.close()
        return result

    result = asyncio.run(scenario())
    assert result == str(path) and path.exists()
    assert client.downloads == [1]


def test_copier_fallback_upload_quando_rapido_falha(env, no_sleep) -> None:
    msg = tl_video(1, size=4096)
    client = FakeClient([msg])
    path = env.root / "up.bin"
    path.write_bytes(payload(4096))
    client.part_errors[("SaveFilePartRequest", 0)] = [OSError("rede caiu")] * 10
    copier = Copier(client, repo=env.repo, config=RunConfig(use_dedup=False), rich_ok=False)
    copier._transfer_pool = FastTransferPool(client, max_total_connections=2, connections_per_file=2)
    entry = {"msg": msg, "media_type": "video", "path": str(path)}

    async def scenario():
        handle = await copier._upload_file(entry)
        await copier._transfer_pool.close()
        return handle

    handle = asyncio.run(scenario())
    assert handle.path == str(path)
    assert client.upload_attempts == ["video_1.mp4"]

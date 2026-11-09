"""Regressões do painel: os callbacks de produção transportam bytes, não percentuais."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import copier as copier_module
from config import RunConfig
from copier import Copier, ProgressDisplay, TransferStats
from database import Repository
from tests.fake_telegram import FakeClient, FakeMessage
from tests.test_ordem import tl_video

MIB = 1024 * 1024


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(copier_module, "TEMP_DIR", tmp_path / "tmp")
    repo = Repository(tmp_path / "progresso.db")
    try:
        yield SimpleNamespace(root=tmp_path, repo=repo)
    finally:
        repo.close()


def arquivo_esparso(path, tamanho: int) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.truncate(tamanho)
    return str(path)


def test_agregado_bytes_de_tamanhos_distintos_e_callback_atrasado() -> None:
    progress = ProgressDisplay(0, "", rich_ok=False)
    down = progress.add_transfer("↓", phase="download")
    up = progress.add_transfer("↑", phase="upload")
    progress.update_transfer(down, 250 * MIB, 250 * MIB)
    progress.update_transfer(up, 40 * MIB, 80 * MIB)
    progress.update_transfer(down, 20 * MIB, 250 * MIB)
    progress.update_transfer(up, 80 * MIB, 80 * MIB)
    progress.update_transfer(up, 79 * MIB, 80 * MIB)
    progress.remove_transfer(down)
    progress.remove_transfer(up)
    progress.update_transfer(down, 250 * MIB, 250 * MIB)
    assert progress.transfer_totals == {"download": 250 * MIB, "upload": 80 * MIB}
    assert "250.0" in progress.aggregate_text and "80.0" in progress.aggregate_text


def test_rich_receba_percentual_calculado_internamente() -> None:
    class Prog:
        def __init__(self):
            self.calls = []

        def update(self, task, **kwargs):
            self.calls.append((task, kwargs))

    progress = ProgressDisplay(0, "", rich_ok=False)
    progress._prog = Prog()
    progress._aggregate_task = "aggregate"
    progress._transfer_last["arquivo"] = ("download", 0)
    progress.update_transfer("arquivo", 250 * MIB, 500 * MIB)
    assert progress.transfer_totals["download"] == 250 * MIB
    atualizacao = next(kwargs for task, kwargs in progress._prog.calls if task == "arquivo")
    assert atualizacao["completed"] == 50
    assert atualizacao["total"] == 100


def test_callback_real_de_download_alimenta_agregado_em_bytes(env) -> None:
    msg = tl_video(1, size=250 * MIB)
    client = FakeClient([msg])
    copier = Copier(client, repo=env.repo, config=RunConfig(fast_transfer=False), rich_ok=False)
    progress = ProgressDisplay(1, "", rich_ok=False)
    path = env.root / "tmp" / "video.bin"

    async def download(msg, filename, callback, **kwargs):
        arquivo_esparso(path, 250 * MIB)
        callback(233 * MIB, 250 * MIB)
        callback(250 * MIB, 250 * MIB)
        return filename

    with patch.object(copier, "_download_media", download):
        asyncio.run(copier._prepare_plan(
            0, [msg], [], progress, None, None,
            strong=False, force_upload=True, checkpoint_pair=None,
        ))
    assert progress.transfer_totals["download"] == 250 * MIB


def test_callback_real_de_preupload_alimenta_agregado_em_bytes(env) -> None:
    msg = tl_video(1, size=80 * MIB)
    copier = Copier(FakeClient([msg]), repo=env.repo, config=RunConfig(), rich_ok=False)
    progress = ProgressDisplay(1, "", rich_ok=False)
    path = arquivo_esparso(env.root / "pre.bin", 80 * MIB)

    async def upload(entry, callback):
        callback(60 * MIB, 80 * MIB)
        return SimpleNamespace(path=path)

    with patch.object(copier, "_upload_file", upload):
        asyncio.run(copier._preupload_entry(
            {"msg": msg, "path": path, "media_type": "video", "size": 80 * MIB},
            None, progress,
        ))
    assert progress.transfer_totals["upload"] == 60 * MIB


def test_callback_real_do_sender_alimenta_agregado_em_bytes(env) -> None:
    msg = tl_video(1, size=100 * MIB)
    client = FakeClient([msg])
    path = arquivo_esparso(env.root / "send.bin", 100 * MIB)

    async def send_file(*args, progress_callback=None, **kwargs):
        progress_callback(75 * MIB, 100 * MIB)
        return FakeMessage(id=900)

    client.send_file = send_file
    copier = Copier(client, repo=env.repo, config=RunConfig(), rich_ok=False)
    progress = ProgressDisplay(1, "", rich_ok=False)
    entry = {"msg": msg, "path": path, "media_type": "video", "size": 100 * MIB,
             "_source_id": -1, "_dest_id": -2}
    asyncio.run(copier._send_media(entry, -2, None, progress, TransferStats(), None))
    assert progress.transfer_totals["upload"] == 75 * MIB



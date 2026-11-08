"""Pipeline A+C: pré-upload nos workers, ordem, janela, tmp e progresso."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import config
import copier as copier_module
from config import RunConfig
from copier import Copier, ProgressDisplay, TransferWindow
from database import Repository, Status
from i18n import get_lang, set_lang
from telethon.errors import FilePartMissingError
from tests.fake_telegram import FakeClient, album_messages, channel, text_message
from tests.test_ordem import tl_video
from utils import DailyLimit


@pytest.fixture
def env(tmp_path, monkeypatch):
    previous = get_lang()
    set_lang("pt")
    temp = tmp_path / "tmp"
    monkeypatch.setattr(config, "TEMP_DIR", temp)
    monkeypatch.setattr(copier_module, "TEMP_DIR", temp)
    monkeypatch.setattr(copier_module, "daily_limit", DailyLimit(tmp_path / "daily.json"))
    repo = Repository(tmp_path / "test.db")
    try:
        yield SimpleNamespace(repo=repo, temp=temp, root=tmp_path)
    finally:
        repo.close()
        set_lang(previous)


def make(env, messages, *, client=None, **options):
    source = channel(-1001, "Origem")
    dest = channel(-2002, "Destino")
    client = client if client is not None else FakeClient(messages)
    client.register_entity(source)
    client.register_entity(dest)
    defaults = {"workers": 3, "delay": 0.1, "use_dedup": False,
                "delivery": "upload", "max_retries": 0}
    defaults.update(options)
    cfg = RunConfig(**defaults)
    copier = Copier(client, repo=env.repo, config=cfg, rich_ok=False)
    return SimpleNamespace(copier=copier, client=client, source=source, dest=dest, cfg=cfg)


def test_uploads_fora_de_ordem_nao_antecipam_envio_nem_checkpoint(env) -> None:
    messages = [tl_video(i, f"msg {i}", size=4096) for i in range(1, 5)]
    client = FakeClient(messages)
    client.upload_delays = {"video_1.mp4": 0.06, "video_2.mp4": 0.02,
                            "video_3.mp4": 0.01, "video_4.mp4": 0.0}
    setup = make(env, messages, client=client)

    original_upload = client.upload_file

    async def upload(path, file_name=None, progress_callback=None, **kwargs):
        msg_id = int(file_name.split("_")[1].split(".")[0])
        route = env.repo.checkpoint_route(setup.source.id, setup.dest.id, msg_id)
        assert route.get("status") != Status.DONE.value
        assert not env.repo.is_processed(setup.source.id, msg_id, setup.dest.id)
        return await original_upload(path, file_name, progress_callback, **kwargs)

    client.upload_file = upload
    result = asyncio.run(asyncio.wait_for(setup.copier.run(setup.source, setup.dest), 5))

    assert result["done"] == 4 and result["errors"] == 0
    assert [s["caption"] for s in client.sent] == [f"msg {i}" for i in range(1, 5)]
    assert len(client.upload_attempts) == 4
    assert all(env.repo.checkpoint_route(setup.source.id, setup.dest.id, i)["status"] == "done"
               for i in range(1, 5))
    assert not list(env.temp.glob("*"))


def test_lookahead_limita_admissao_e_nao_cria_deadlock(env) -> None:
    messages = [tl_video(i, f"msg {i}", size=4096) for i in range(1, 7)]

    class TrackedClient(FakeClient):
        def __init__(self, items):
            super().__init__(items)
            self.events = []
            self.active = 0
            self.peak = 0

        async def download_media(self, message, file=None, progress_callback=None, **kwargs):
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.events.append(("download", message.id))
            try:
                if message.id == 1:
                    await asyncio.sleep(0.05)
                return await super().download_media(message, file, progress_callback, **kwargs)
            finally:
                self.active -= 1

        async def __call__(self, request):
            if type(request).__name__ == "SendMediaRequest":
                self.events.append(("send", request.message))
            return await super().__call__(request)

    client = TrackedClient(messages)
    setup = make(env, messages, client=client, workers=4)
    setup.cfg.lookahead = 2
    result = asyncio.run(asyncio.wait_for(setup.copier.run(setup.source, setup.dest), 5))

    assert result["done"] == 6
    assert client.peak <= 2
    # O índice 2 (msg 3) só pode ser admitido depois de o sender concluir o índice 0.
    assert client.events.index(("download", 3)) > client.events.index(("send", "msg 1"))
    assert [s["caption"] for s in client.sent] == [f"msg {i}" for i in range(1, 7)]


def test_item_maior_que_orcamento_passa_sozinho(env) -> None:
    messages = [tl_video(i, size=200 * 1024 * 1024) for i in range(1, 4)]
    setup = make(env, messages, workers=3)
    setup.cfg.max_tmp_gb = 0.000001
    setup.cfg.lookahead = 6
    result = asyncio.run(asyncio.wait_for(setup.copier.run(setup.source, setup.dest), 5))
    assert result["done"] == 3 and result["errors"] == 0
    assert not list(env.temp.glob("*"))


def test_cancelamento_no_meio_do_pipeline_limpa_tmp(env) -> None:
    messages = [tl_video(i, size=4096) for i in range(1, 5)]
    blocked = asyncio.Event()

    class BlockingClient(FakeClient):
        async def download_media(self, message, file=None, progress_callback=None, **kwargs):
            path = await super().download_media(message, file, progress_callback, **kwargs)
            if message.id == 2:
                blocked.set()
                await asyncio.Event().wait()
            return path

    client = BlockingClient(messages)
    setup = make(env, messages, client=client, workers=3)

    async def scenario():
        task = asyncio.create_task(setup.copier.run(setup.source, setup.dest))
        await asyncio.wait_for(blocked.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(asyncio.wait_for(scenario(), 5))
    assert not list(env.temp.glob("*"))
    assert env.repo.checkpoint_route(setup.source.id, setup.dest.id, 2) == {}


def test_album_prepara_upload_media_no_worker_antes_do_envio(env) -> None:
    messages = album_messages(10, 3)
    client = FakeClient(messages)
    setup = make(env, messages, client=client, workers=2)
    result = asyncio.run(asyncio.wait_for(setup.copier.run(setup.source, setup.dest), 5))

    assert result["done"] == 3 and result["albums"] == 1
    assert client.upload_media_calls == 3
    names = [type(req).__name__ for req in client.requests]
    assert names.count("UploadMediaRequest") == 3
    assert names.index("SendMultiMediaRequest") > max(
        i for i, name in enumerate(names) if name == "UploadMediaRequest"
    )
    assert [s["caption"] for s in client.sent] == ["foto 0", "", ""]
    assert not list(env.temp.glob("*"))


def test_preupload_expirado_reenvia_upload_uma_unica_vez(env) -> None:
    messages = [tl_video(1, size=4096)]
    client = FakeClient(messages)
    client.send_errors["SendMediaRequest"] = [FilePartMissingError(request=None)]
    setup = make(env, messages, client=client)
    result = asyncio.run(asyncio.wait_for(setup.copier.run(setup.source, setup.dest), 5))

    assert result["done"] == 1 and result["errors"] == 0
    assert len(client.upload_attempts) == 2
    assert len(client.sent) == 1


def test_preupload_expirado_duas_vezes_naoentra_em_loop(env) -> None:
    messages = [tl_video(1, size=4096)]
    client = FakeClient(messages)
    client.send_errors["SendMediaRequest"] = [
        FilePartMissingError(request=None), FilePartMissingError(request=None)
    ]
    setup = make(env, messages, client=client, placeholder=False, on_failure="abort")
    with patch.object(setup.copier, "_prompt_failure", return_value="a"):
        result = asyncio.run(asyncio.wait_for(setup.copier.run(setup.source, setup.dest), 5))

    assert result["done"] == 0 and result["errors"] == 1
    assert len(client.upload_attempts) == 2
    assert not client.sent
    assert not list(env.temp.glob("*"))


def test_progresso_agregado_soma_deltas_sem_duplo_contagem() -> None:
    progress = ProgressDisplay(0, "", rich_ok=False)
    a = progress.add_transfer("↓ a", phase="download")
    b = progress.add_transfer("↓ b", phase="download")
    up = progress.add_transfer("↑ a", phase="upload")
    progress.update_transfer(a, 40, 100)
    progress.update_transfer(b, 30, 100)
    progress.update_transfer(a, 20, 100)  # callback atrasado não subtrai nem duplica
    progress.update_transfer(a, 100, 100)
    progress.update_transfer(up, 50, 100)
    progress.remove_transfer(a)
    progress.update_transfer(b, 100, 100)
    progress.update_transfer(up, 100, 100)
    progress.remove_transfer(b)
    progress.remove_transfer(up)

    assert progress.transfer_totals == {"download": 200, "upload": 100}


def test_janela_estima_album_como_um_item() -> None:
    album = album_messages(1, 3)
    window = TransferWindow([album, text_message(2, "x")], lookahead=2, budget_bytes=10**9)
    assert window.estimate(album) == sum(4096 for _ in album)

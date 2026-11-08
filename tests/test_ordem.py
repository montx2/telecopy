"""WP1/WP2 — entrega por referência e ordem garantida por tópico (offline)."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="telecopy_order_")

import config  # noqa: E402

config.DATA_DIR = Path(_TMP)
config.TEMP_DIR = config.DATA_DIR / "tmp"
config.DB_FILE = config.DATA_DIR / "order.db"
config.STATS_FILE = config.DATA_DIR / "stats.json"
config.DAILY_FILE = config.DATA_DIR / "daily.json"
config.init_dirs()

from config import RunConfig  # noqa: E402
from copier import Copier  # noqa: E402
from database import Repository  # noqa: E402
from i18n import get_lang, set_lang  # noqa: E402
from telethon.errors import FileReferenceExpiredError  # noqa: E402
from telethon.tl import types as tl  # noqa: E402
from utils import DailyLimit  # noqa: E402
from tests.fake_telegram import FakeClient, FakeMessage, album_messages, channel  # noqa: E402


@contextmanager
def fast_sleep():
    delays = []

    async def sleep(delay):
        delays.append(delay)

    with patch("copier.asyncio.sleep", new=sleep):
        yield delays


@pytest.fixture
def env(tmp_path, monkeypatch):
    previous = get_lang()
    set_lang("pt")
    temp = tmp_path / "tmp"
    monkeypatch.setattr(config, "TEMP_DIR", temp)
    monkeypatch.setattr(config, "DB_FILE", tmp_path / "test.db")
    monkeypatch.setattr(config, "STATS_FILE", tmp_path / "stats.json")
    monkeypatch.setattr("copier.TEMP_DIR", temp)
    limit = DailyLimit(tmp_path / "daily.json")
    monkeypatch.setattr("copier.daily_limit", limit)
    repo = Repository(tmp_path / "test.db")
    try:
        yield SimpleNamespace(repo=repo, limit=limit, temp=temp, root=tmp_path)
    finally:
        repo.close()
        set_lang(previous)


def tl_video(msg_id: int, caption: str = "", size: int = 183_500_000, **kwargs) -> FakeMessage:
    doc = tl.Document(
        id=1000 + msg_id, access_hash=2000 + msg_id, file_reference=b"ref-" + str(msg_id).encode(),
        date=datetime.now(timezone.utc), mime_type="video/mp4", size=size, dc_id=2,
        attributes=[
            tl.DocumentAttributeVideo(duration=12, w=1920, h=1080, supports_streaming=True),
            tl.DocumentAttributeFilename(file_name=f"video-{msg_id}.mp4"),
        ],
    )
    return FakeMessage(id=msg_id, message=caption, media=tl.MessageMediaDocument(document=doc), **kwargs)


def make_copier(env, messages, *, client=None, **options):
    source = channel(-1001, "Origem")
    dest = channel(-2002, "Destino")
    client = client if client is not None else FakeClient(messages)
    client.register_entity(source)
    client.register_entity(dest)
    defaults = {"workers": 1, "delay": 0.1, "use_dedup": False}
    defaults.update(options)
    copier = Copier(client, repo=env.repo, config=RunConfig(**defaults), rich_ok=False)
    return SimpleNamespace(copier=copier, client=client, source=source, dest=dest)


def test_video_por_referencia_sem_download(env) -> None:
    message = tl_video(1, "vídeo")
    setup = make_copier(env, [message])
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert result["done"] == 1
    assert result["server_copies"] == 1
    assert result["bytes_saved"] == 183_500_000
    assert setup.client.downloads == []
    assert setup.client.sent[0]["input_media"] is not None
    assert env.repo.checkpoint_route(setup.source.id, setup.dest.id, 1)["dest_msg_id"]


def test_noforwards_com_forward_mode_cai_para_referencia(env) -> None:
    message = tl_video(1)
    setup = make_copier(env, [message], forward_mode=True)
    setup.source.noforwards = True
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert result["done"] == 1
    assert setup.client.forwarded == []
    assert setup.client.downloads == []
    assert result["server_copies"] == 1


def test_server_copy_false_usa_caminho_antigo(env) -> None:
    message = tl_video(1)
    setup = make_copier(env, [message], server_copy=False)
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert result["done"] == 1
    assert result["server_copies"] == 0
    assert setup.client.downloads == [1]
    assert setup.client.sent[0]["kind"] == "file"
    assert setup.client.sent[0]["path"]


def test_album_por_referencia_preserva_ordem_das_partes(env) -> None:
    messages = [tl_video(i, f"parte {i}", grouped_id=77) for i in (1, 2, 3)]
    setup = make_copier(env, messages)
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert result["done"] == 3 and result["albums"] == 1
    assert result["server_copies"] == 3
    assert setup.client.downloads == []
    assert [s["input_media"].id.id for s in setup.client.sent] == [1001, 1002, 1003]
    assert all(s["album"] for s in setup.client.sent)
    routes = [env.repo.checkpoint_route(setup.source.id, setup.dest.id, i)["dest_msg_id"] for i in (1, 2, 3)]
    assert routes == sorted(routes) and len(set(routes)) == 3


def test_referencia_expirada_refresh_succede(env) -> None:
    message = tl_video(1)
    setup = make_copier(env, [message])
    setup.client.reference_errors[1001] = [FileReferenceExpiredError(request=None)]
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert result["done"] == 1 and result["server_copies"] == 1
    assert setup.client.reference_attempts == [1001, 1001]
    assert setup.client.downloads == []


def test_referencia_expirada_persistente_fallback_upload(env) -> None:
    message = tl_video(1)
    setup = make_copier(env, [message])
    setup.client.reference_errors[1001] = [
        FileReferenceExpiredError(request=None), FileReferenceExpiredError(request=None)
    ]
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert result["done"] == 1
    assert result["server_copies"] == 0
    assert setup.client.downloads == [1]
    assert setup.client.sent[0]["kind"] == "file"
    assert result["bytes"] > 0


def test_referencia_em_forum_usa_topico(env) -> None:
    message = tl_video(1)
    setup = make_copier(env, [message])
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest, dest_topic=911))
    assert result["done"] == 1 and result["server_copies"] == 1
    assert setup.client.sent[0]["top_msg_id"] == 911
    assert setup.client.sent[0]["reply_to"] == 911
    assert setup.client.downloads == []


def test_album_parcial_reserva_marcador_na_posicao(env) -> None:
    messages = album_messages(1, 4, grouped_id=99)
    client = FakeClient(messages, fail_ids={2})
    setup = make_copier(env, messages, client=client, server_copy=False)
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert result["placeholders"] == 1
    routes = [env.repo.checkpoint_route(setup.source.id, setup.dest.id, i)["dest_msg_id"] for i in (1, 2, 3, 4)]
    assert all(routes) and routes == sorted(routes)
    kinds = [item["kind"] for item in setup.client.sent]
    assert kinds[0] == "file" and kinds[1] == "text"
    assert setup.client.sent[1]["text"] and "mensagem 2" in setup.client.sent[1]["text"]
    assert env.repo.get_placeholder(setup.source.id, setup.dest.id, 2)["state"] == "open"


def test_config_delivery_server_copy_roundtrip(env) -> None:
    from prefs import Preferences

    cfg = RunConfig(delivery="upload", server_copy=False)
    path = env.root / "prefs.json"
    prefs = Preferences(path)
    prefs.save_config(cfg)
    loaded = Preferences(path)
    assert loaded.config.delivery == "upload"
    assert loaded.config.server_copy is False
    assert loaded.get("delivery") == "upload"
    assert loaded.get("server_copy") is False
    assert RunConfig.from_dict({"delivery": "inválido"}).delivery == "reference"

"""WP1/WP2 — entrega por referência e ordem garantida por tópico (offline)."""

from __future__ import annotations

import asyncio
import random
import sys
import tempfile
import copier as cm
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
from copier import Copier, DailyLimitReached  # noqa: E402
from database import Repository, Status  # noqa: E402
from i18n import get_lang, set_lang  # noqa: E402
from telethon.errors import FileReferenceExpiredError  # noqa: E402
from telethon.tl import types as tl  # noqa: E402
from utils import DailyLimit  # noqa: E402
from tests.fake_telegram import FakeClient, FakeMessage, album_messages, channel, text_message  # noqa: E402


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
    # These legacy-route tests opt in explicitly; RunConfig's production defaults
    # are covered by tests/test_motor_upload.py.
    defaults = {"workers": 1, "delay": 0.1, "use_dedup": False,
                "delivery": "reference", "server_copy": True,
                "placeholder": True, "on_failure": "placeholder"}
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


class TransientTextClient(FakeClient):
    def __init__(self, messages, fail_once=None, fail_always=None):
        super().__init__(messages)
        self.fail_once = set(fail_once or ())
        self.fail_always = set(fail_always or ())
        self.attempts = []

    def _maybe_fail(self, text):
        self.attempts.append(text)
        parts = text.split(maxsplit=1)
        if len(parts) == 2 and parts[0] == "msg" and parts[1].isdigit():
            msg_id = int(parts[1])
            if msg_id in self.fail_always:
                raise OSError("falha permanente simulada")
            if msg_id in self.fail_once:
                self.fail_once.discard(msg_id)
                raise OSError("falha transitória simulada")

    async def send_message(self, entity, text, reply_to=None, **kwargs):
        self._maybe_fail(text)
        return await super().send_message(entity, text, reply_to=reply_to, **kwargs)

    async def __call__(self, request):
        if type(request).__name__ == "SendMessageRequest":
            self._maybe_fail(request.message)
        return await super().__call__(request)


def test_falha_transitoria_nao_avanca(env) -> None:
    messages = [text_message(i, f"msg {i}") for i in range(1, 6)]
    client = TransientTextClient(messages, fail_once={2})
    setup = make_copier(env, messages, client=client, max_retries=2)
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert result["done"] == 5 and result["errors"] == 0
    assert [s["text"] for s in client.sent] == [f"msg {i}" for i in range(1, 6)]
    first = client.attempts.index("msg 2")
    second = client.attempts.index("msg 2", first + 1)
    assert "msg 3" not in client.attempts[:second]


def test_ordem_com_200_mensagens_e_falhas_aleatorias(env) -> None:
    messages = [text_message(i, f"msg {i}") for i in range(1, 201)]
    failed = set(random.Random(42).sample(range(1, 201), 20))
    client = TransientTextClient(messages, fail_always=failed)
    setup = make_copier(env, messages, client=client, max_retries=0)
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert result["done"] == 180 and result["placeholders"] == 20
    with env.repo._db() as conn:
        rows = conn.execute(
            "SELECT msg_id, dest_msg_id FROM checkpoints WHERE source_id=? AND dest_id=? ORDER BY msg_id",
            (setup.source.id, setup.dest.id),
        ).fetchall()
    ids = [row["dest_msg_id"] for row in rows]
    assert len(ids) == 200 and all(ids) and ids == sorted(ids) and len(set(ids)) == 200
    for msg_id in failed:
        assert env.repo.get_placeholder(setup.source.id, setup.dest.id, msg_id)["state"] == "open"


def test_retomada_apos_interrupcao_continua_na_ordem(env) -> None:
    class InterruptClient(FakeClient):
        async def send_message(self, entity, text, reply_to=None, **kwargs):
            if text == "msg 6":
                raise DailyLimitReached("limite simulado")
            return await super().send_message(entity, text, reply_to=reply_to, **kwargs)

    messages = [text_message(i, f"msg {i}") for i in range(1, 11)]
    client = InterruptClient(messages)
    setup = make_copier(env, messages, client=client)
    with fast_sleep():
        first = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert first["interrupted"] and first["done"] == 5
    client2 = FakeClient(messages)
    client2.register_entity(setup.source)
    client2.register_entity(setup.dest)
    setup.copier.client = client2
    with fast_sleep():
        second = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert second["done"] == 5
    combined = [s["text"] for s in client.sent] + [s["text"] for s in client2.sent]
    assert combined == [f"msg {i}" for i in range(1, 11)]


def test_detecta_violacao_de_ordem_existente(env) -> None:
    messages = [text_message(1, "msg 1"), text_message(2, "msg 2")]
    setup = make_copier(env, messages)
    env.repo.upsert_checkpoint(setup.source.id, setup.dest.id, 1, Status.ERROR)
    env.repo.upsert_checkpoint(setup.source.id, setup.dest.id, 2, Status.DONE, dest_msg_id=100)
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert result["errors"] == 1
    assert "Violação de ordem" in result["first_error"]
    assert setup.client.sent == []


def test_forum_ordem_independente_por_topico(env) -> None:
    def topic_msg(i, topic):
        return text_message(i, f"msg {i}", reply_to=SimpleNamespace(
            reply_to_msg_id=topic, reply_to_top_id=topic, forum_topic=True))

    messages = [topic_msg(1, 11), topic_msg(2, 22), topic_msg(3, 11), topic_msg(4, 22)]
    client = TransientTextClient(messages, fail_always={3})
    setup = make_copier(env, messages, client=client, max_retries=0)
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest, topic_map={11: 911, 22: 922}))
    assert result["done"] == 3 and result["placeholders"] == 1
    by_topic = {}
    for row in env.repo.order_rows(setup.source.id, setup.dest.id):
        by_topic.setdefault(row["dest_topic"], []).append((row["msg_id"], row["dest_msg_id"]))
    assert [m for m, _ in by_topic[911]] == [1, 3]
    assert [m for m, _ in by_topic[922]] == [2, 4]
    for rows in by_topic.values():
        dest_ids = [d for _, d in rows]
        assert dest_ids == sorted(dest_ids)
    assert all(s["top_msg_id"] in (911, 922) for s in client.sent)


def test_espelho_dez_mensagens_em_ordem(env) -> None:
    class MirrorClient(FakeClient):
        async def run_until_disconnected(self):
            for message in self.messages:
                for handler in list(self.handlers):
                    await handler(SimpleNamespace(message=message))

    messages = [text_message(i, f"msg {i}") for i in range(1, 11)]
    client = MirrorClient(messages)
    setup = make_copier(env, messages, client=client)
    with fast_sleep():
        asyncio.run(setup.copier.mirror(setup.source, setup.dest))
    assert [s["text"] for s in client.sent] == [f"msg {i}" for i in range(1, 11)]


def test_abort_prompt_retoma_no_mesmo_ponto(env) -> None:
    messages = [text_message(i, f"msg {i}") for i in range(1, 4)]
    client = TransientTextClient(messages, fail_once={2})
    setup = make_copier(env, messages, client=client, max_retries=0,
                        on_failure="abort")
    with patch.object(cm.ui, "ask", return_value="r"), \
         patch.object(cm.ui, "menu", lambda *a, **k: None), \
         patch.object(cm.ui, "warn", lambda *a, **k: None), \
         patch.object(cm.ui, "blank", lambda *a, **k: None), \
         fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert result["done"] == 3 and result["errors"] == 0 and result["placeholders"] == 0
    assert [s["text"] for s in client.sent] == ["msg 1", "msg 2", "msg 3"]
    first = client.attempts.index("msg 2")
    second = client.attempts.index("msg 2", first + 1)
    assert "msg 3" not in client.attempts[:second]


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
    assert RunConfig.from_dict({"delivery": "inválido"}).delivery == "upload"

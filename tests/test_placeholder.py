"""Reserva de posição e recuperação por edição — testes offline.

Os diretórios são redirecionados antes dos imports da aplicação, como nas
suítes existentes. Cada teste recebe banco, contador diário e tmp próprios.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="telecopy_placeholder_"))

import config  # noqa: E402

config.DATA_DIR = _TMP
config.TEMP_DIR = _TMP / "tmp"
config.LOGS_DIR = _TMP / "logs"
config.VAULT_DIR = _TMP / "vault"
config.EXPORT_DIR = _TMP / "exports"
config.BACKUP_DIR = _TMP / "backups"
config.DB_FILE = _TMP / "placeholder.db"
config.STATS_FILE = _TMP / "stats.json"
config.DAILY_FILE = _TMP / "daily.json"
config.LOG_FILE = config.LOGS_DIR / "telecopy.log"
config.VAULT_FILE = config.VAULT_DIR / "credentials.enc"
config.init_dirs()

from config import RunConfig  # noqa: E402
from copier import Copier, ProgressDisplay  # noqa: E402
from database import Repository, Status  # noqa: E402
from i18n import get_lang, set_lang  # noqa: E402
from utils import DailyLimit  # noqa: E402
from tests.fake_telegram import (  # noqa: E402
    FakeClient,
    FakeMessage,
    album_messages,
    channel,
    document_message,
    photo_message,
)


class RetryClient(FakeClient):
    """Falhas de download por mensagem, sem rede e sem alterar o fake comum."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.download_attempts = {}
        self.download_errors = {}
        self.persistent_errors = {}
        self.refreshes = []

    async def download_media(self, message, file=None, **kwargs):
        count = self.download_attempts.get(message.id, 0) + 1
        self.download_attempts[message.id] = count
        errors = self.download_errors.get(message.id, [])
        if errors:
            raise errors.pop(0)
        if message.id in self.persistent_errors:
            raise self.persistent_errors[message.id]
        return await super().download_media(message, file=file, **kwargs)

    async def get_messages(self, entity, ids=None, **kwargs):
        if isinstance(ids, int):
            self.refreshes.append(ids)
        return await super().get_messages(entity, ids=ids, **kwargs)


@contextmanager
def fast_sleep():
    delays = []

    async def sleep(delay):
        delays.append(delay)

    with patch("copier.asyncio.sleep", new=sleep):
        yield delays


@pytest.fixture
def env(tmp_path, monkeypatch):
    previous_lang = get_lang()
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
        set_lang(previous_lang)


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


def test_prepare_valueerror_transiente_recupera_na_segunda(env) -> None:
    message = document_message(2, "aula.pdf")
    client = RetryClient([message])
    client.download_errors[2] = [ValueError("Request was unsuccessful 2 time(s)")]
    setup = make_copier(env, [message], client=client)
    with fast_sleep() as delays:
        payload = asyncio.run(setup.copier._prepare(
            0, message, ProgressDisplay(0, "", False), source=setup.source, strong=True
        ))
    assert payload["plan"][0]["path"]
    assert client.download_attempts[2] == 2
    assert client.refreshes == [2]
    assert delays == [2]
    setup.copier._cleanup(payload["plan"])
    assert not list(env.temp.glob("*"))


def test_prepare_valueerror_persistente_preserva_motivo(env) -> None:
    message = document_message(2, "aula.pdf")
    reason = "Request was unsuccessful 2 time(s)"
    client = RetryClient([message])
    client.persistent_errors[2] = ValueError(reason)
    setup = make_copier(env, [message], client=client)
    with fast_sleep() as delays:
        payload = asyncio.run(setup.copier._prepare(
            0, message, ProgressDisplay(0, "", False), source=setup.source, strong=True
        ))
    entry = payload["plan"][0]
    assert entry["failed"]
    assert reason in entry["reason"]
    assert client.download_attempts[2] == 5
    assert delays == [2, 4, 8, 16]
    assert not list(env.temp.glob("*"))


def test_prepare_valueerror_no_caminho_normal_nao_escapa(env) -> None:
    message = document_message(2, "aula.pdf")
    client = RetryClient([message])
    client.persistent_errors[2] = ValueError("Request was unsuccessful 2 time(s)")
    setup = make_copier(env, [message], client=client)
    with fast_sleep() as delays:
        payload = asyncio.run(setup.copier._prepare(
            0, message, ProgressDisplay(0, "", False), source=setup.source
        ))
    assert payload["plan"][0]["failed"]
    assert "Request was unsuccessful" in payload["plan"][0]["reason"]
    assert client.download_attempts[2] == 2
    assert delays == [1.5]


def test_falha_no_item_dois_reserva_posicao(env) -> None:
    messages = [document_message(i, f"aula{i}.pdf") for i in (1, 2, 3)]
    setup = make_copier(env, messages, client=FakeClient(messages, fail_ids={2}))
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert result["done"] == 2 and result["errors"] == 1
    assert result["placeholders"] == 1 and not result["complete"]
    assert [item["kind"] for item in setup.client.sent] == ["file", "text", "file"]
    marker = setup.client.sent[1]
    assert "mensagem 2" in marker["text"]
    row = env.repo.get_placeholder(setup.source.id, setup.dest.id, 2)
    assert row["dest_msg_id"] == marker["msg_id"]
    assert row["state"] == "open"
    route = env.repo.checkpoint_route(setup.source.id, setup.dest.id, 2)
    assert route["status"] == Status.ERROR.value
    assert route["dest_msg_id"] == marker["msg_id"]
    assert len(env.repo.failures()) == 1
    assert env.repo.dedup_count() == 0
    assert env.limit.count == 3
    assert not list(env.temp.glob("*"))


def test_retomada_nao_cria_segundo_marcador(env) -> None:
    messages = [document_message(i, f"aula{i}.pdf") for i in (1, 2, 3)]
    setup = make_copier(env, messages, client=FakeClient(messages, fail_ids={2}))
    with fast_sleep():
        asyncio.run(setup.copier.run(setup.source, setup.dest))
        before = len(setup.client.sent)
        count = env.limit.count
        second = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert second["placeholders"] == 0
    assert len(setup.client.sent) == before
    assert env.repo.placeholder_stats()["open"] == 1
    assert env.limit.count == count


def test_legenda_longa_guarda_texto_completo(env) -> None:
    from telethon.tl.types import MessageEntityBold

    text = "A" * 1500 + "📚" + "B" * 500
    message = photo_message(1, text, entities=[MessageEntityBold(offset=0, length=10)])
    setup = make_copier(env, [message], client=FakeClient([message], fail_ids={1}),
                        signature="@curso")
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert result["placeholders"] == 1
    marker = setup.client.sent[0]
    assert marker["kind"] == "text"
    assert text not in marker["text"]
    assert "mensagem 1" in marker["text"]
    row = env.repo.get_placeholder(setup.source.id, setup.dest.id, 1)
    assert row["caption"] == text + "\n\n@curso"
    entities = json.loads(row["entities"])
    assert entities[0]["type"] == "MessageEntityBold"
    assert entities[0]["length"] == 10


def test_forum_reserva_no_topico_mapeado(env) -> None:
    reply = SimpleNamespace(reply_to_msg_id=11, reply_to_top_id=11, forum_topic=True)
    message = document_message(10, "aula.pdf", reply_to=reply)
    source = channel(-1100, "Fórum origem", forum=True, broadcast=False)
    dest = channel(-2200, "Fórum destino", forum=True, broadcast=False)
    client = FakeClient([message], fail_ids={10})
    client.register_entity(source)
    client.register_entity(dest)
    copier = Copier(client, repo=env.repo,
                    config=RunConfig(workers=1, delay=0.1, use_dedup=False), rich_ok=False)
    topic_map = {1: 1, 11: 911}
    with fast_sleep():
        asyncio.run(copier.run(source, dest, topic_map=topic_map))
    row = env.repo.get_placeholder(source.id, dest.id, 10)
    assert row["dest_topic"] == 911
    marker = client.sent[0]
    assert marker["reply_to"] == 911 and marker["top_msg_id"] == 911
    assert topic_map == {1: 1, 11: 911}
    assert env.repo.checkpoint_route(source.id, dest.id, 10)["dest_topic"] == 911


def test_placeholder_false_mantem_caminho_legado(env) -> None:
    messages = [document_message(i, f"aula{i}.pdf") for i in (1, 2, 3)]
    setup = make_copier(env, messages, client=FakeClient(messages, fail_ids={2}),
                        placeholder=False)
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert result["placeholders"] == 0
    assert [item["kind"] for item in setup.client.sent] == ["file", "file"]
    assert env.repo.placeholder_stats() == {"open": 0, "recovered": 0, "unrecoverable": 0}
    assert env.repo.checkpoint_route(setup.source.id, setup.dest.id, 2)["status"] == "error"
    assert len(env.repo.failures()) == 1
    assert env.limit.count == 2
    assert not hasattr(setup.client, "edits")


def test_valueerror_persistente_vira_reserva_legivel(env) -> None:
    message = document_message(2, "aula.pdf")
    reason = "Request was unsuccessful 2 time(s)"
    client = RetryClient([message])
    client.persistent_errors[2] = ValueError(reason)
    setup = make_copier(env, [message], client=client)
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    row = env.repo.get_placeholder(setup.source.id, setup.dest.id, 2)
    assert result["placeholders"] == 1
    assert reason in row["reason"]
    assert reason in result["first_error"]


def test_unsupported_nasce_nao_recuperavel(env) -> None:
    media = type("MessageMediaDice", (), {})()
    message = FakeMessage(id=1, media=media)
    setup = make_copier(env, [message])
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    row = env.repo.get_placeholder(setup.source.id, setup.dest.id, 1)
    assert row["state"] == "unrecoverable"
    assert result["unsupported"] == 1 and result["placeholders"] == 1
    assert "indisponível" in setup.client.sent[0]["text"]
    assert env.repo.checkpoint_route(setup.source.id, setup.dest.id, 1)["status"] == "error"
    assert env.repo.dedup_count() == 0


def test_album_com_uma_falha_cria_um_marcador(env) -> None:
    messages = album_messages(10, 3)
    setup = make_copier(env, messages, client=FakeClient(messages, fail_ids={11}))
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert result["placeholders"] == 1 and result["errors"] == 1
    assert env.repo.placeholder_stats()["open"] == 1
    assert sum(item["kind"] == "text" for item in setup.client.sent) == 1


def test_banco_v4_abre_sem_perder_historico(env) -> None:
    path = env.root / "v4.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE checkpoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL, dest_id INTEGER NOT NULL, msg_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            timestamp REAL NOT NULL DEFAULT 0, content_hash TEXT,
            bytes_done INTEGER NOT NULL DEFAULT 0, media_type TEXT NOT NULL DEFAULT '',
            UNIQUE (source_id, dest_id, msg_id)
        );
        CREATE TABLE failures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL, dest_id INTEGER NOT NULL, msg_id INTEGER NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 1, reason TEXT,
            created_at REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0,
            UNIQUE (source_id, dest_id, msg_id)
        );
        CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT);
    """)
    conn.execute("INSERT INTO checkpoints (source_id,dest_id,msg_id,status) VALUES (-1,-2,7,'error')")
    conn.execute("INSERT INTO failures (source_id,dest_id,msg_id,reason) VALUES (-1,-2,7,'antigo')")
    conn.execute("INSERT INTO kv VALUES ('forum_topic_map:-1:-2','{\"1\":1}')")
    conn.commit()
    conn.close()
    repo = Repository(path)
    try:
        assert repo.checkpoint_route(-1, -2, 7)["status"] == "error"
        assert repo.failures()[0]["reason"] == "antigo"
        assert repo.get_kv("forum_topic_map:-1:-2") == '{"1":1}'
        assert repo.placeholder_stats() == {"open": 0, "recovered": 0, "unrecoverable": 0}
        repo._init_schema()
        assert repo.failures()[0]["msg_id"] == 7
    finally:
        repo.close()


def test_config_e_prefs_persistem_placeholder(env) -> None:
    from prefs import Preferences

    cfg = RunConfig(placeholder=False)
    assert cfg.to_dict()["placeholder"] is False
    assert RunConfig.from_dict(cfg.to_dict()).placeholder is False
    path = env.root / "prefs.json"
    prefs = Preferences(path)
    prefs.set("unknown_key", "keep")
    prefs.save_config(cfg)
    loaded = Preferences(path)
    assert loaded.get("placeholder") is False
    assert loaded.config.placeholder is False
    assert loaded.get("unknown_key") == "keep"
    loaded.set("placeholder", True)
    assert Preferences(path).config.placeholder is True


def test_limite_atingido_nao_cria_reserva_nem_falha(env) -> None:
    env.limit.add(config.DAILY_LIMIT)
    messages = [document_message(1, "aula.pdf")]
    setup = make_copier(env, messages, client=FakeClient(messages, fail_ids={1}))
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert result["interrupted"] and result["errors"] == 0
    assert result["placeholders"] == 0
    assert not setup.client.sent
    assert not env.repo.failures()
    assert env.repo.placeholder_stats()["open"] == 0


def test_falha_do_marcador_degrada_para_fila(env) -> None:
    class BrokenMarkerClient(FakeClient):
        async def send_message(self, *args, **kwargs):
            raise OSError("marcador indisponível")

    messages = [document_message(1, "aula.pdf")]
    setup = make_copier(env, messages, client=BrokenMarkerClient(messages, fail_ids={1}))
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert result["errors"] == 1 and result["placeholders"] == 0
    assert len(env.repo.failures()) == 1
    assert env.repo.placeholder_stats()["open"] == 0


def test_falha_de_envio_sem_id_confirmado_reserva(env) -> None:
    class BrokenSendClient(FakeClient):
        async def send_file(self, *args, **kwargs):
            raise OSError("upload falhou")

    messages = [document_message(1, "aula.pdf")]
    setup = make_copier(env, messages, client=BrokenSendClient(messages))
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert result["errors"] == 1 and result["placeholders"] == 1
    row = env.repo.get_placeholder(setup.source.id, setup.dest.id, 1)
    assert row["state"] == "open"
    assert setup.client.sent[0]["kind"] == "text"
    assert env.repo.dedup_count() == 0


def test_excecao_de_preparacao_preserva_motivo_na_reserva(env) -> None:
    messages = [document_message(1, "aula.pdf")]
    setup = make_copier(env, messages)

    async def broken_prepare(*args, **kwargs):
        raise RuntimeError("motivo específico da preparação")

    with patch.object(setup.copier, "_prepare", new=broken_prepare), fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    row = env.repo.get_placeholder(setup.source.id, setup.dest.id, 1)
    assert result["placeholders"] == 1
    assert row["reason"] == "motivo específico da preparação"

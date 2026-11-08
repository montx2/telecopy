"""Reserva de posição e recuperação por edição — testes offline.

Os diretórios são redirecionados antes dos imports da aplicação, como nas
suítes existentes. Cada teste recebe banco, contador diário e tmp próprios.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
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
    text_message,
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
def answers(*values):
    queue = list(values)

    def fake_input(*_args, **_kwargs):
        if not queue:
            raise EOFError
        return queue.pop(0)

    with patch("builtins.input", new=fake_input):
        yield


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
    # The placeholder suite tests the opt-in legacy mode; production defaults
    # are covered separately by test_motor_upload.py.
    defaults = {"workers": 1, "delay": 0.1, "use_dedup": False,
                "delivery": "upload", "server_copy": False,
                "placeholder": True, "on_failure": "placeholder"}
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
                    config=RunConfig(workers=1, delay=0.1, use_dedup=False, delivery="upload",
                                     server_copy=False, placeholder=True,
                                     on_failure="placeholder"), rich_ok=False)
    topic_map = {1: 1, 11: 911}
    with fast_sleep():
        asyncio.run(copier.run(source, dest, topic_map=topic_map))
    row = env.repo.get_placeholder(source.id, dest.id, 10)
    assert row["dest_topic"] == 911
    marker = client.sent[0]
    assert marker["reply_to"] == 911 and marker["top_msg_id"] == 911
    assert topic_map == {1: 1, 11: 911}
    assert env.repo.checkpoint_route(source.id, dest.id, 10)["dest_topic"] == 911


def test_placeholder_false_aborta_sem_abrir_buraco(env) -> None:
    messages = [document_message(i, f"aula{i}.pdf") for i in (1, 2, 3)]
    setup = make_copier(env, messages, client=FakeClient(messages, fail_ids={2}),
                        placeholder=False)
    with patch.object(setup.copier, "_prompt_failure", return_value="a"), fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert result["interrupted"] and result["placeholders"] == 0
    assert [item["kind"] for item in setup.client.sent] == ["file"]
    assert env.repo.placeholder_stats() == {"open": 0, "recovered": 0, "unrecoverable": 0}
    assert env.repo.checkpoint_route(setup.source.id, setup.dest.id, 2)["status"] == "error"
    assert env.repo.checkpoint_route(setup.source.id, setup.dest.id, 3) == {}
    assert len(env.repo.failures()) == 1
    assert env.limit.count == 1
    assert setup.client.edits == []


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


def test_falha_do_marcador_interrompe_sem_avancar(env) -> None:
    class BrokenMarkerClient(FakeClient):
        async def send_message(self, *args, **kwargs):
            raise OSError("marcador indisponível")

    messages = [document_message(1, "aula.pdf")]
    setup = make_copier(env, messages, client=BrokenMarkerClient(messages, fail_ids={1}))
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert result["errors"] == 1 and result["placeholders"] == 0
    assert result["interrupted"] and result["done"] == 0
    assert len(env.repo.failures()) == 1
    assert env.repo.placeholder_stats()["open"] == 0


def test_falha_de_envio_sem_id_confirmado_reserva(env) -> None:
    class BrokenSendClient(FakeClient):
        async def send_file(self, *args, **kwargs):
            raise OSError("upload falhou")

        async def __call__(self, request):
            # O caminho com pré-upload monta SendMediaRequest diretamente;
            # a falha simulada continua cobrindo a mesma fronteira de envio.
            if type(request).__name__ == "SendMediaRequest":
                raise OSError("upload falhou")
            return await super().__call__(request)

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


def _create_marker(env, messages=None, *, fail_ids=None, client=None, **options):
    messages = messages or [document_message(2, "aula.pdf")]
    fail_ids = {2} if fail_ids is None else fail_ids
    client = client if client is not None else FakeClient(messages, fail_ids=fail_ids)
    setup = make_copier(env, messages, client=client, **options)
    with fast_sleep():
        asyncio.run(setup.copier.run(setup.source, setup.dest))
    return setup


def test_retry_com_marcador_usa_edicao(env) -> None:
    messages = [document_message(i, f"aula{i}.pdf") for i in (1, 2, 3)]
    setup = _create_marker(env, messages, fail_ids={2}, use_dedup=True)
    marker = setup.client.sent[1]["msg_id"]
    before_sent = len(setup.client.sent)
    before_daily = env.limit.count
    setup.client.fail_ids.clear()
    with fast_sleep():
        result = asyncio.run(setup.copier.retry_messages(setup.source, setup.dest, [2]))
    assert result["done"] == 1 and result["errors"] == 0
    assert len(setup.client.sent) == before_sent
    assert len(setup.client.edits) == 1
    assert setup.client.edits[0]["msg_id"] == marker
    assert setup.client.edits[0]["path"]
    row = env.repo.get_placeholder(setup.source.id, setup.dest.id, 2)
    assert row["state"] == "recovered"
    route = env.repo.checkpoint_route(setup.source.id, setup.dest.id, 2)
    assert route["status"] == "done" and route["dest_msg_id"] == marker
    assert env.repo.dedup_count() == 3
    assert not env.repo.failures()
    assert env.limit.count == before_daily
    assert not list(env.temp.glob("*"))


def test_origem_sem_mensagem_edita_aviso_final(env) -> None:
    setup = _create_marker(env)
    marker = setup.client.sent[0]["msg_id"]
    before_daily = env.limit.count
    setup.client.messages = []
    with fast_sleep():
        result = asyncio.run(setup.copier.recover_placeholders(setup.source, setup.dest))
    assert result == {"recovered": 0, "unrecoverable": 1, "failed": 0}
    assert len(setup.client.edits) == 1
    edit = setup.client.edits[0]
    assert edit["msg_id"] == marker and edit["path"] is None
    assert "indisponível" in edit["text"]
    assert env.repo.get_placeholder(setup.source.id, setup.dest.id, 2)["state"] == "unrecoverable"
    assert env.repo.checkpoint_route(setup.source.id, setup.dest.id, 2)["status"] == "error"
    assert env.limit.count == before_daily


def test_legenda_longa_recuperada_com_continuacoes(env) -> None:
    from telethon.tl.types import MessageEntityBold

    text = "A" * 1500 + "📚" + "B" * 500
    message = photo_message(1, text, entities=[MessageEntityBold(offset=0, length=10)])
    setup = _create_marker(env, [message], fail_ids={1}, signature="@curso")
    row = env.repo.get_placeholder(setup.source.id, setup.dest.id, 1)
    stored = row["caption"]
    setup.client.fail_ids.clear()
    before_daily = env.limit.count
    before_sent = len(setup.client.sent)
    slots = env.repo.placeholder_parts(setup.source.id, setup.dest.id, 1)
    assert slots, "continuações devem ser pré-reservadas na criação do marcador"
    with fast_sleep():
        result = asyncio.run(setup.copier.recover_placeholders(setup.source, setup.dest))
    assert result["recovered"] == 1
    assert len(setup.client.sent) == before_sent, "recuperação não cria mensagem nova"
    assert env.limit.count == before_daily, "edições não consomem limite diário"
    assert setup.client.edits[0]["path"]
    assert "".join(edit["text"] for edit in setup.client.edits) == stored
    assert setup.client.edits[0]["formatting_entities"][0].length == 10
    assert len(setup.client.edits) == 1 + len(slots)
    assert env.repo.get_placeholder(setup.source.id, setup.dest.id, 1)["state"] == "recovered"
    assert not list(env.temp.glob("*"))


def test_album_recupera_item_avulso_sem_grupo(env) -> None:
    messages = album_messages(10, 3)
    setup = _create_marker(env, messages, fail_ids={11})
    before_sent = len(setup.client.sent)
    setup.client.fail_ids.clear()
    with fast_sleep():
        result = asyncio.run(setup.copier.recover_placeholders(setup.source, setup.dest))
    assert result["recovered"] == 1
    assert len(setup.client.edits) == 1
    assert setup.client.edits[0]["path"]
    assert len(setup.client.sent) == before_sent
    assert not setup.client.sent[-1]["album"]
    assert env.repo.get_placeholder(setup.source.id, setup.dest.id, 11)["state"] == "recovered"


def test_forum_recupera_marcador_sem_mexer_no_mapa(env) -> None:
    reply = SimpleNamespace(reply_to_msg_id=11, reply_to_top_id=11, forum_topic=True)
    message = document_message(10, "aula.pdf", reply_to=reply)
    source = channel(-1100, "Fórum origem", forum=True, broadcast=False)
    dest = channel(-2200, "Fórum destino", forum=True, broadcast=False)
    client = FakeClient([message], fail_ids={10})
    client.register_entity(source)
    client.register_entity(dest)
    copier = Copier(client, repo=env.repo,
                    config=RunConfig(workers=1, delay=0.1, use_dedup=False, delivery="upload",
                                     server_copy=False, placeholder=True,
                                     on_failure="placeholder"), rich_ok=False)
    topic_map = {1: 1, 11: 911}
    with fast_sleep():
        asyncio.run(copier.run(source, dest, topic_map=topic_map))
        client.fail_ids.clear()
        result = asyncio.run(copier.recover_placeholders(source, dest))
    assert result["recovered"] == 1
    assert topic_map == {1: 1, 11: 911}
    route = env.repo.checkpoint_route(source.id, dest.id, 10)
    assert route["status"] == "done" and route["dest_topic"] == 911
    assert len(client.edits) == 1


def test_valueerror_transiente_na_recuperação(env) -> None:
    message = document_message(2, "aula.pdf")
    client = RetryClient([message], fail_ids={2})
    setup = _create_marker(env, [message], client=client)
    client.fail_ids.clear()
    client.download_errors[2] = [ValueError("Request was unsuccessful 2 time(s)")]
    with fast_sleep():
        result = asyncio.run(setup.copier.recover_placeholders(setup.source, setup.dest))
    assert result["recovered"] == 1
    assert env.repo.get_placeholder(setup.source.id, setup.dest.id, 2)["state"] == "recovered"
    assert client.download_attempts[2] == 4


def test_valueerror_persistente_na_recuperacao_vira_aviso(env) -> None:
    message = document_message(2, "aula.pdf")
    client = RetryClient([message], fail_ids={2})
    setup = _create_marker(env, [message], client=client)
    client.fail_ids.clear()
    reason = "Request was unsuccessful 2 time(s)"
    client.persistent_errors[2] = ValueError(reason)
    with fast_sleep():
        result = asyncio.run(setup.copier.recover_placeholders(setup.source, setup.dest))
    assert result["unrecoverable"] == 1
    row = env.repo.get_placeholder(setup.source.id, setup.dest.id, 2)
    assert row["state"] == "unrecoverable"
    assert reason in row["reason"]
    assert reason in setup.client.edits[0]["text"]
    assert not list(env.temp.glob("*"))


@pytest.mark.parametrize(
    "error_name,expected_state,expected_key",
    [
        ("MessageNotModifiedError", "open", "failed"),
        ("MessageEditTimeExpiredError", "unrecoverable", "unrecoverable"),
        ("MessageAuthorRequiredError", "unrecoverable", "unrecoverable"),
        ("MessageIdInvalidError", "unrecoverable", "unrecoverable"),
        ("ChatWriteForbiddenError", "unrecoverable", "unrecoverable"),
    ],
)
def test_erros_de_edicao_nao_derrubam_lote(env, error_name, expected_state, expected_key) -> None:
    from telethon import errors

    setup = _create_marker(env)
    marker = setup.client.sent[0]["msg_id"]
    setup.client.fail_ids.clear()
    setup.client.edit_failures[marker] = getattr(errors, error_name)(request=None)
    with fast_sleep():
        result = asyncio.run(setup.copier.recover_placeholders(setup.source, setup.dest))
    assert result[expected_key] == 1
    assert env.repo.get_placeholder(setup.source.id, setup.dest.id, 2)["state"] == expected_state
    assert env.repo.checkpoint_route(setup.source.id, setup.dest.id, 2)["status"] == "error"


def test_floodwait_na_edicao_mantem_reserva_aberta(env) -> None:
    from telethon.errors import FloodWaitError

    setup = _create_marker(env)
    marker = setup.client.sent[0]["msg_id"]
    setup.client.fail_ids.clear()
    setup.client.edit_failures[marker] = FloodWaitError(request=None, capture=1)
    with fast_sleep() as delays:
        result = asyncio.run(setup.copier.recover_placeholders(setup.source, setup.dest))
    assert result["failed"] == 1
    assert env.repo.get_placeholder(setup.source.id, setup.dest.id, 2)["state"] == "open"
    assert 4 in delays


def test_retry_repetido_apos_recuperacao_nao_duplica(env) -> None:
    setup = _create_marker(env)
    setup.client.fail_ids.clear()
    with fast_sleep():
        asyncio.run(setup.copier.retry_messages(setup.source, setup.dest, [2]))
        edits = len(setup.client.edits)
        sent = len(setup.client.sent)
        second = asyncio.run(setup.copier.retry_messages(setup.source, setup.dest, [2]))
    assert second["skipped"] == 1
    assert len(setup.client.edits) == edits
    assert len(setup.client.sent) == sent


def test_recuperacao_de_texto_sem_midia(env) -> None:
    class FailFirstTextClient(FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.text_calls = 0

        async def send_message(self, *args, **kwargs):
            self.text_calls += 1
            if self.text_calls == 1:
                raise OSError("envio de texto falhou")
            return await super().send_message(*args, **kwargs)

    message = text_message(5, "texto original")
    setup = make_copier(env, [message], client=FailFirstTextClient([message]), max_retries=0)
    with fast_sleep():
        first = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert first["placeholders"] == 1
    with fast_sleep():
        result = asyncio.run(setup.copier.recover_placeholders(setup.source, setup.dest))
    assert result["recovered"] == 1
    assert setup.client.edits[-1]["text"] == "texto original"
    assert setup.client.edits[-1]["path"] is None


def test_settings_h_persiste_toggle(env, monkeypatch) -> None:
    import core
    from prefs import Preferences

    prefs = Preferences(env.root / "prefs.json")
    monkeypatch.setattr(core, "prefs", prefs)
    monkeypatch.setattr(core, "db", env.repo)
    app = core.App()
    app.config = RunConfig(delay=0.1, workers=1, placeholder=True)
    with answers("h", "0"), fast_sleep():
        asyncio.run(app._menu_settings())
    assert app.config.placeholder is False
    assert prefs.config.placeholder is False
    assert Preferences(env.root / "prefs.json").get("placeholder") is False


def test_tools_recover_ui_edita_marcador(env, monkeypatch) -> None:
    import core
    from prefs import Preferences

    setup = _create_marker(env)
    setup.client.fail_ids.clear()
    monkeypatch.setattr(core, "db", env.repo)
    monkeypatch.setattr(core, "prefs", Preferences(env.root / "prefs.json"))
    app = core.App()
    app.client = setup.client
    app.config = setup.copier.cfg
    with answers("s", ""), fast_sleep():
        asyncio.run(app._tool_recover())
    assert env.repo.get_placeholder(setup.source.id, setup.dest.id, 2)["state"] == "recovered"
    assert len(setup.client.edits) == 1


def test_reset_all_apaga_reservas_mas_preserva_preferencias(env) -> None:
    env.repo.add_placeholder(-1, -2, 7, 1007, caption="x", reason="falha")
    env.repo.set_kv("forum_topic_map:-1:-2", '{"1":1}')
    env.repo.set_kv("preferencia_interna", "manter")
    env.repo.reset_all()
    assert env.repo.placeholder_stats() == {"open": 0, "recovered": 0, "unrecoverable": 0}
    assert env.repo.get_kv("forum_topic_map:-1:-2") is None
    assert env.repo.get_kv("preferencia_interna") == "manter"


def test_i18n_placeholder_pt_en() -> None:
    from i18n import set_lang, t

    keys = [
        "placeholder.pending", "placeholder.unrecoverable", "settings.placeholder.toggle",
        "tools.recover", "tools.recover.title", "tools.recover.empty",
        "tools.recover.confirm", "tools.recover.done", "copy.placeholders",
    ]
    previous = get_lang()
    try:
        for lang in ("pt", "en"):
            set_lang(lang)
            for key in keys:
                assert t(key) != key
            assert "7" in t("placeholder.pending", id=7, reason="teste")
            assert "7" in t("placeholder.unrecoverable", id=7, reason="teste")
            assert "3" in t("copy.placeholders", count=3)
    finally:
        set_lang(previous)


def test_cli_flags_e_instancia_unica_isolados(env) -> None:
    app_dir = env.root / "app"
    app_dir.mkdir()
    for path in ROOT.glob("*.py"):
        shutil.copy2(path, app_dir / path.name)
    env_vars = dict(os.environ)
    env_vars["PYTHONDONTWRITEBYTECODE"] = "1"
    for flag in ("--version", "--check", "--reset"):
        proc = subprocess.run([sys.executable, "main.py", flag], cwd=app_dir,
                              env=env_vars, capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
    if os.name == "nt":
        return
    import fcntl

    lock_path = app_dir / "data" / "telecopy.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        proc = subprocess.run([sys.executable, "main.py"], cwd=app_dir,
                              env=env_vars, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 2

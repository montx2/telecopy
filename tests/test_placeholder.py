"""Reserva de posição e recuperação por edição — testes offline.

Os diretórios são redirecionados antes dos imports da aplicação, como nas
suítes existentes. Cada teste recebe banco, contador diário e tmp próprios.
"""

from __future__ import annotations

import asyncio
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
from database import Repository  # noqa: E402
from utils import DailyLimit  # noqa: E402
from tests.fake_telegram import (  # noqa: E402
    FakeClient,
    channel,
    document_message,
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

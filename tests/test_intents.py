"""Janela envio→checkpoint: intenção durável e reconciliação pelo destino."""
from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

import copier as copier_module
from config import RunConfig
from copier import Copier
from database import Repository
from tests.fake_telegram import FakeClient, channel, text_message
from utils import DailyLimit


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(copier_module, "TEMP_DIR", tmp_path / "tmp")
    monkeypatch.setattr(copier_module, "daily_limit", DailyLimit(tmp_path / "daily.json"))
    repo = Repository(tmp_path / "intents.db")
    try:
        yield SimpleNamespace(root=tmp_path, repo=repo)
    finally:
        repo.close()


class ClienteQueAposaSemConfirmar(FakeClient):
    """O efeito remoto acontece; o processo morre antes de devolver o ID."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.crash_once = True
        self._prompt_abort = True

    async def send_message(self, entity, text, reply_to=None, **kwargs):
        result = await super().send_message(entity, text, reply_to=reply_to, **kwargs)
        if self.crash_once:
            self.crash_once = False
            raise ConnectionError("queda simulada depois do efeito remoto")
        return result


def montar(env, client):
    source, dest = channel(-1, "Origem"), channel(-2, "Destino")
    client.register_entity(source), client.register_entity(dest)
    copier = Copier(client, repo=env.repo, config=RunConfig(
        workers=1, delay=0, use_dedup=False, max_retries=0,
    ), rich_ok=False)
    return copier, source, dest


def test_queda_entre_envio_e_checkpoint_nao_duplica_na_retomada(env) -> None:
    msg = text_message(1, "aula única")
    client = ClienteQueAposaSemConfirmar([msg])
    copier, source, dest = montar(env, client)
    copier._prompt_failure = lambda reason: "a"
    result = asyncio.run(copier.run(source, dest))
    assert result["errors"] >= 1
    assert len(client.sent) == 1
    assert not env.repo.is_done(source.id, dest.id, msg.id)

    resumed = Copier(client, repo=env.repo, config=copier.cfg, rich_ok=False)
    result = asyncio.run(resumed.run(source, dest))
    assert result["errors"] == 0
    assert [r["text"] for r in client.sent] == ["aula única"]
    assert env.repo.is_done(source.id, dest.id, msg.id)


def test_intencao_persiste_antes_do_envio_e_confirma_depois(env) -> None:
    msg = text_message(1, "texto")
    client = FakeClient([msg])
    copier, source, dest = montar(env, client)
    observed = []
    original = client.send_message

    async def spy(*args, **kwargs):
        rows = env.repo.pending_intents(source.id, dest.id)
        observed.append([(r["msg_id"], r["state"], r["random_id"]) for r in rows])
        return await original(*args, **kwargs)

    client.send_message = spy
    asyncio.run(copier.run(source, dest))
    assert observed and observed[0][0][0] == 1
    assert observed[0][0][1] == "sending"
    # send_message de alto nível não expõe random_id; caminhos raw persistem.
    row = env.repo.intent_for(source.id, dest.id, 1, 0)
    assert row["state"] == "confirmed" and row["dest_msg_id"]


def test_efeito_remoto_ausente_permite_retomar_sem_bloqueio(env) -> None:
    msg = text_message(1, "não chegou")
    client = FakeClient([msg])
    copier, source, dest = montar(env, client)
    env.repo.begin_intent(source.id, dest.id, 1, 0, fingerprint="ausente", random_id=123)
    # Nenhum registro no destino: a intenção pode ser reenviada com segurança.
    result = asyncio.run(copier.run(source, dest))
    assert result["done"] == 1
    assert len(client.sent) == 1


def test_destino_ambiguo_bloqueia_em_vez_de_adivinhar(env) -> None:
    msg = text_message(1, "repetida")
    client = FakeClient([msg])
    copier, source, dest = montar(env, client)
    asyncio.run(client.send_message(dest, "repetida"))
    asyncio.run(client.send_message(dest, "repetida"))
    fingerprint = copier._intent_fingerprint(kind="text", text="repetida", entities=None,
                                             dest_topic=None, part_index=0)
    env.repo.begin_intent(source.id, dest.id, 1, 0, fingerprint=fingerprint,
                          random_id=123, kind="text")
    result = asyncio.run(copier.run(source, dest))
    assert result["errors"] >= 1 and result["complete"] is False
    assert len(client.sent) == 2
    assert not env.repo.is_done(source.id, dest.id, 1)


def test_migracao_send_intents_preserva_dados_e_permite_rollback(tmp_path) -> None:
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE checkpoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT, source_id INTEGER, dest_id INTEGER,
            msg_id INTEGER, status TEXT, attempts INTEGER DEFAULT 0, timestamp REAL DEFAULT 0,
            content_hash TEXT, bytes_done INTEGER DEFAULT 0, media_type TEXT DEFAULT '',
            UNIQUE(source_id,dest_id,msg_id)
        );
        INSERT INTO checkpoints(source_id,dest_id,msg_id,status) VALUES(1,2,3,'done');
        PRAGMA user_version=0;
    """)
    conn.commit()
    conn.close()
    repo = Repository(path)
    try:
        assert repo.schema_version() >= 1
        assert repo.is_done(1, 2, 3)
        repo.begin_intent(1, 2, 4, 0, fingerprint="x", random_id=9)
        assert repo.intent_for(1, 2, 4, 0)["state"] == "sending"
    finally:
        repo.close()
    backups = list(tmp_path.glob("legacy.db.bak-v*"))
    assert backups, "migração precisa preservar backup antes de alterar schema"
    repo = Repository(path)
    try:
        repo.rollback_schema(0)
        assert repo.schema_version() == 0
        assert repo.is_done(1, 2, 3)
    finally:
        repo.close()

"""Verificação não pode declarar completo sem olhar a origem nem truncar o destino."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import copier as copier_module
from config import RunConfig
from copier import Copier
from database import Repository, Status
from tests.fake_telegram import FakeClient, channel, text_message
from utils import DailyLimit
from verificacao import verificar_par


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(copier_module, "TEMP_DIR", tmp_path / "tmp")
    monkeypatch.setattr(copier_module, "daily_limit", DailyLimit(tmp_path / "daily.json"))
    repo = Repository(tmp_path / "verify.db")
    try:
        yield SimpleNamespace(root=tmp_path, repo=repo)
    finally:
        repo.close()


def test_verificacao_inclui_mensagens_da_origem_sem_checkpoint(env) -> None:
    source, dest = channel(-1, "S"), channel(-2, "D")
    client = FakeClient([text_message(1, "a"), text_message(2, "b")])
    client.register_entity(source), client.register_entity(dest)
    sent = asyncio.run(client.send_message(dest, "a"))
    env.repo.upsert_checkpoint(source.id, dest.id, 1, Status.DONE, dest_msg_id=sent.id)
    result = asyncio.run(verificar_par(client, env.repo, source, dest))
    assert result.verificados == 2
    assert result.ausentes == [2]
    assert result.como_dict()["complete"] is False


def test_verificacao_nao_trunca_destino_em_500(env) -> None:
    source, dest = channel(-1, "S"), channel(-2, "D")
    client = FakeClient([text_message(1, "primeira")])
    client.register_entity(source), client.register_entity(dest)

    async def enviar():
        first = await client.send_message(dest, "primeira")
        for i in range(600):
            await client.send_message(dest, f"ruído {i}")
        return first

    first = asyncio.run(enviar())
    env.repo.upsert_checkpoint(source.id, dest.id, 1, Status.DONE, dest_msg_id=first.id)
    result = asyncio.run(verificar_par(client, env.repo, source, dest))
    assert result.ok == 1 and result.ausentes == []
    assert result.como_dict()["complete"] is True


def test_verificacao_detecta_conteudo_divergente_do_confirmado(env) -> None:
    source, dest = channel(-1, "S"), channel(-2, "D")
    client = FakeClient([text_message(1, "esperado")])
    client.register_entity(source), client.register_entity(dest)
    sent = asyncio.run(client.send_message(dest, "outro conteúdo"))
    env.repo.upsert_checkpoint(source.id, dest.id, 1, Status.DONE, dest_msg_id=sent.id)
    copier = Copier(client, repo=env.repo, config=RunConfig(), rich_ok=False)
    fp = copier._intent_fingerprint(kind="text", text="esperado", entities=None,
                                    dest_topic=None, part_index=0)
    env.repo.begin_intent(source.id, dest.id, 1, 0, fingerprint=fp, kind="text")
    env.repo.confirm_intent(source.id, dest.id, 1, 0, sent.id)
    result = asyncio.run(verificar_par(client, env.repo, source, dest))
    assert result.divergentes == [1]
    assert result.como_dict()["complete"] is False


def test_verificacao_parcial_e_marcada_quando_limite_explicito(env) -> None:
    source, dest = channel(-1, "S"), channel(-2, "D")
    client = FakeClient([text_message(1, "a"), text_message(2, "b")])
    client.register_entity(source), client.register_entity(dest)
    result = asyncio.run(verificar_par(client, env.repo, source, dest, limite=1))
    assert result.parcial is True
    assert result.como_dict()["complete"] is False


def test_verificacao_rehearsal_limita_origem(env) -> None:
    source, dest = channel(-1, "S"), channel(-2, "D")
    client = FakeClient([text_message(i, f"m{i}") for i in range(1, 6)])
    client.register_entity(source), client.register_entity(dest)

    async def enviar():
        return [await client.send_message(dest, f"m{i}") for i in range(1, 3)]

    ids = asyncio.run(enviar())
    for i, remote in enumerate(ids, start=1):
        env.repo.upsert_checkpoint(source.id, dest.id, i, Status.DONE, dest_msg_id=remote.id)
    result = asyncio.run(verificar_par(client, env.repo, source, dest, max_messages=2))
    assert result.verificados == 2 and result.ok == 2
    assert result.como_dict()["complete"] is True

"""Janelas envio→checkpoint nos caminhos forward, referência, nativo e reserva."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import copier as copier_module
from config import RunConfig
from copier import Copier
from database import Repository
from tests.fake_telegram import FakeClient, FakeMessage, channel, document_message, text_message
from utils import DailyLimit


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(copier_module, "TEMP_DIR", tmp_path / "tmp")
    monkeypatch.setattr(copier_module, "daily_limit", DailyLimit(tmp_path / "daily.json"))
    repo = Repository(tmp_path / "janelas.db")
    try:
        yield SimpleNamespace(root=tmp_path, repo=repo)
    finally:
        repo.close()


def montar(env, client, **cfg):
    source, dest = channel(-1, "S"), channel(-2, "D")
    client.register_entity(source), client.register_entity(dest)
    base = dict(workers=1, delay=0, use_dedup=False, max_retries=0, fast_transfer=False)
    base.update(cfg)
    copier = Copier(client, repo=env.repo, config=RunConfig(**base), rich_ok=False)
    copier._prompt_failure = lambda reason: "a"
    return copier, source, dest


class ForwardCrash(FakeClient):
    crash_once = True

    async def forward_messages(self, entity, messages, from_peer=None, **kwargs):
        result = await super().forward_messages(entity, messages, from_peer=from_peer, **kwargs)
        if self.crash_once:
            self.crash_once = False
            raise ConnectionError("queda após forward")
        return result


def test_forward_crash_nao_duplica_na_retomada(env) -> None:
    client = ForwardCrash([text_message(1, "aula")])
    copier, source, dest = montar(env, client, delivery="forward")
    result = asyncio.run(copier.run(source, dest))
    assert result["errors"] >= 1 and len(client.forwarded) == 1
    resumed = Copier(client, repo=env.repo, config=copier.cfg, rich_ok=False)
    result = asyncio.run(resumed.run(source, dest))
    assert result["errors"] == 0 and result["done"] == 1
    assert len(client.forwarded) == 1
    assert env.repo.is_done(source.id, dest.id, 1)


class SendFileCrash(FakeClient):
    crash_once = True

    async def send_file(self, entity, file=None, **kwargs):
        result = await super().send_file(entity, file=file, **kwargs)
        if self.crash_once:
            self.crash_once = False
            raise ConnectionError("queda após send_file")
        return result


def test_referencia_crash_nao_duplica_na_retomada(env) -> None:
    client = SendFileCrash([document_message(1, "a.pdf")])
    copier, source, dest = montar(env, client, delivery="reference", server_copy=True)
    result = asyncio.run(copier.run(source, dest))
    assert result["errors"] >= 1 and len(client.sent) == 1
    resumed = Copier(client, repo=env.repo, config=copier.cfg, rich_ok=False)
    result = asyncio.run(resumed.run(source, dest))
    assert result["errors"] == 0 and result["done"] == 1
    assert len(client.sent) == 1


def test_nativo_crash_nao_duplica_na_retomada(env) -> None:
    media = type("MessageMediaContact", (), {})()
    media.phone_number = "+550000000000"
    media.first_name = "A"
    media.last_name = "B"
    media.vcard = ""
    msg = FakeMessage(id=1, message="contato", media=media)
    client = SendFileCrash([msg])
    copier, source, dest = montar(env, client)
    result = asyncio.run(copier.run(source, dest))
    assert result["errors"] >= 1 and len(client.sent) == 1
    resumed = Copier(client, repo=env.repo, config=copier.cfg, rich_ok=False)
    result = asyncio.run(resumed.run(source, dest))
    assert result["errors"] == 0 and result["done"] == 1
    assert len(client.sent) == 1


class PlaceholderCrash(FakeClient):
    crash_once = True

    async def send_message(self, entity, text, reply_to=None, **kwargs):
        result = await super().send_message(entity, text, reply_to=reply_to, **kwargs)
        if self.crash_once:
            self.crash_once = False
            raise ConnectionError("queda após reservar posição")
        return result


def test_reserva_crash_nao_duplica_marcador(env) -> None:
    client = PlaceholderCrash([document_message(1, "a.pdf")], fail_ids={1})
    copier, source, dest = montar(env, client, placeholder=True, on_failure="placeholder")
    result = asyncio.run(copier.run(source, dest))
    assert result["errors"] >= 1 and len(client.sent) == 1
    resumed = Copier(client, repo=env.repo, config=copier.cfg, rich_ok=False)
    result = asyncio.run(resumed.run(source, dest))
    assert len(client.sent) == 1
    assert env.repo.get_placeholder(source.id, dest.id, 1) is not None

"""Regressão D3/D4 — o modo encaminhar obedece ao mesmo contrato WP2.

Antes desta correção o `_forward_loop` era o único caminho sem retries do
contrato, sem barreira posicional e que gravava DONE sem ID confirmado.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import copier as cm
from tests import test_ordem as _ordem
from tests.fake_telegram import FakeClient, text_message

env = _ordem.env
fast_sleep = _ordem.fast_sleep
make_copier = _ordem.make_copier


class ForwardFails(FakeClient):
    """Falha no forward do msg_id indicado; `permanent` decide se recupera."""

    def __init__(self, messages, target=2, permanent=False):
        super().__init__(messages)
        self.target = target
        self.permanent = permanent
        self.failed = False

    async def forward_messages(self, entity, messages, from_peer=None, **kwargs):
        ids = messages if isinstance(messages, list) else [messages]
        if self.target in ids and (self.permanent or not self.failed):
            self.failed = True
            raise OSError("falha no forward")
        return await super().forward_messages(entity, messages, from_peer=from_peer, **kwargs)


def _routes(repo, source_id, dest_id, ids):
    return [repo.checkpoint_route(source_id, dest_id, i)["dest_msg_id"] for i in ids]


def test_forward_transitorio_retenta_sem_avancar(env) -> None:
    messages = [text_message(i, f"msg {i}") for i in range(1, 5)]
    client = ForwardFails(messages)
    setup = make_copier(env, messages, client=client, forward_mode=True, max_retries=2)
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    forwarded = [i for call in client.forwarded for i in call["ids"]]
    assert forwarded == [1, 2, 3, 4], f"ordem de encaminhamento: {forwarded}"
    assert result["done"] == 4 and result["errors"] == 0
    routes = _routes(env.repo, setup.source.id, setup.dest.id, range(1, 5))
    assert all(routes) and routes == sorted(routes) and len(set(routes)) == 4


def test_forward_falha_permanente_reserva_posicao(env) -> None:
    messages = [text_message(i, f"msg {i}") for i in range(1, 5)]
    client = ForwardFails(messages, permanent=True)
    setup = make_copier(env, messages, client=client, forward_mode=True, max_retries=0)
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert result["placeholders"] == 1 and result["done"] == 3
    marker = env.repo.get_placeholder(setup.source.id, setup.dest.id, 2)
    assert marker and marker["state"] == "open"
    routes = _routes(env.repo, setup.source.id, setup.dest.id, range(1, 5))
    assert all(routes) and routes == sorted(routes) and len(set(routes)) == 4, (
        f"posição não reservada na ordem: {routes}")
    forwarded = [i for call in client.forwarded for i in call["ids"]]
    assert forwarded == [1, 3, 4]


def test_forward_sem_id_confirmado_nao_vira_done(env) -> None:
    class Unconfirmed(FakeClient):
        async def forward_messages(self, entity, messages, from_peer=None, **kwargs):
            ids = messages if isinstance(messages, list) else [messages]
            return [SimpleNamespace(id=None) for _ in ids]

    messages = [text_message(1, "msg 1")]
    client = Unconfirmed(messages)
    setup = make_copier(env, messages, client=client, forward_mode=True, max_retries=0)
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    route = env.repo.checkpoint_route(setup.source.id, setup.dest.id, 1)
    assert result["done"] == 0
    assert route["status"] != "done", f"DONE sem ID confirmado: {route}"


def test_forward_ids_nao_crescentes_sao_violacao(env) -> None:
    class Decreasing(FakeClient):
        async def forward_messages(self, entity, messages, from_peer=None, **kwargs):
            return [FakeMessageLite(10), FakeMessageLite(9)]

    class FakeMessageLite:
        def __init__(self, id):
            self.id = id

    messages = [text_message(1, "a", grouped_id=7), text_message(2, "b", grouped_id=7)]
    client = Decreasing(messages)
    setup = make_copier(env, messages, client=client, forward_mode=True, max_retries=0)
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert result["done"] == 0
    assert "ordem" in (result["first_error"] or "").lower()


def test_forward_abort_nao_avanca(env) -> None:
    messages = [text_message(i, f"msg {i}") for i in range(1, 5)]
    client = ForwardFails(messages, permanent=True)
    setup = make_copier(env, messages, client=client, forward_mode=True,
                        max_retries=0, on_failure="abort")
    with patch.object(cm.ui, "ask", return_value="a"), \
         patch.object(cm.ui, "menu", lambda *a, **k: None), \
         patch.object(cm.ui, "warn", lambda *a, **k: None), \
         patch.object(cm.ui, "blank", lambda *a, **k: None), \
         fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    forwarded = [i for call in client.forwarded for i in call["ids"]]
    assert forwarded == [1], f"avançou após aborto: {forwarded}"
    assert result["done"] == 1 and result["errors"] >= 1


def test_delivery_forward_e_honrado_pelo_motor(env) -> None:
    messages = [text_message(1, "msg 1")]
    setup = make_copier(env, messages, delivery="forward")
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert setup.client.forwarded, "delivery='forward' ignorado pelo motor"
    assert result["done"] == 1


def test_forward_ambiguo_aborta_sem_reservar(env) -> None:
    """Resposta sem ID utilizável: pode ter sido aceita. Detecta e aborta."""
    class Unconfirmed(FakeClient):
        def __init__(self, messages):
            super().__init__(messages)
            self.calls = 0

        async def forward_messages(self, entity, messages, from_peer=None, **kwargs):
            self.calls += 1
            ids = messages if isinstance(messages, list) else [messages]
            return [SimpleNamespace(id=None) for _ in ids]

    messages = [text_message(i, f"msg {i}") for i in range(1, 4)]
    client = Unconfirmed(messages)
    setup = make_copier(env, messages, client=client, forward_mode=True, max_retries=2)
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert result["done"] == 0
    assert result["placeholders"] == 0, "reservou posição sobre estado ambíguo"
    assert result["interrupted"], "estado ambíguo deve abortar"
    assert client.calls == 1, f"reenviou sobre estado ambíguo: {client.calls} tentativas"

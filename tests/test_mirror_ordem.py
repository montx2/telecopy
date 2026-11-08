"""Regressão D8 — o espelho obedece ao contrato WP2.

A auditoria inicial não cobriu o modo espelho: uma falha (transitória ou
permanente) deixava a posição sem checkpoint nem marcador e o handler seguia
para a mensagem seguinte, abrindo buraco na ordem do destino.
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


class MirrorClient(FakeClient):
    """Alimenta o handler do espelho como o Telegram faria, em ordem."""

    def __init__(self, messages, fail_target=None, permanent=False):
        super().__init__(messages)
        self.fail_target = fail_target
        self.permanent = permanent
        self.failed = False
        self.attempts = []

    async def send_message(self, entity, text, reply_to=None, **kwargs):
        self.attempts.append(text)
        if text == self.fail_target and (self.permanent or not self.failed):
            self.failed = True
            raise OSError("rede caiu no espelho")
        return await super().send_message(entity, text, reply_to=reply_to, **kwargs)

    async def run_until_disconnected(self):
        for message in self.messages:
            for handler in list(self.handlers):
                await handler(SimpleNamespace(message=message))


def _routes(repo, source_id, dest_id, ids):
    return [repo.checkpoint_route(source_id, dest_id, i)["dest_msg_id"] for i in ids]


def test_espelho_transitorio_retenta_sem_abrir_buraco(env) -> None:
    messages = [text_message(i, f"msg {i}") for i in range(1, 5)]
    client = MirrorClient(messages, fail_target="msg 2")
    setup = make_copier(env, messages, client=client, max_retries=2)
    with fast_sleep():
        asyncio.run(setup.copier.mirror(setup.source, setup.dest))
    assert [s["text"] for s in client.sent] == [f"msg {i}" for i in range(1, 5)]
    routes = _routes(env.repo, setup.source.id, setup.dest.id, range(1, 5))
    assert all(routes) and routes == sorted(routes) and len(set(routes)) == 4


def test_espelho_falha_permanente_reserva_posicao(env) -> None:
    messages = [text_message(i, f"msg {i}") for i in range(1, 5)]
    client = MirrorClient(messages, fail_target="msg 2", permanent=True)
    setup = make_copier(env, messages, client=client, max_retries=0)
    with fast_sleep():
        asyncio.run(setup.copier.mirror(setup.source, setup.dest))
    marker = env.repo.get_placeholder(setup.source.id, setup.dest.id, 2)
    assert marker and marker["state"] == "open", "posição não reservada no espelho"
    routes = _routes(env.repo, setup.source.id, setup.dest.id, range(1, 5))
    assert all(routes) and routes == sorted(routes) and len(set(routes)) == 4, (
        f"buraco na ordem do espelho: {routes}")


def test_espelho_abort_nao_segue_adiante(env) -> None:
    messages = [text_message(i, f"msg {i}") for i in range(1, 5)]
    client = MirrorClient(messages, fail_target="msg 2", permanent=True)
    setup = make_copier(env, messages, client=client, max_retries=0, on_failure="abort")
    with patch.object(cm.ui, "ask", return_value="a"), \
         patch.object(cm.ui, "menu", lambda *a, **k: None), \
         patch.object(cm.ui, "warn", lambda *a, **k: None), \
         patch.object(cm.ui, "blank", lambda *a, **k: None), \
         fast_sleep():
        asyncio.run(setup.copier.mirror(setup.source, setup.dest))
    assert [s["text"] for s in client.sent] == ["msg 1"], "espelho seguiu após abortar"
    assert setup.copier._stop


def test_espelho_skip_de_dedup_resolve_posicao(env) -> None:
    messages = [text_message(1, "repetido"), text_message(2, "repetido"),
                text_message(3, "msg 3")]
    client = MirrorClient(messages)
    setup = make_copier(env, messages, client=client, use_dedup=True)
    with fast_sleep():
        asyncio.run(setup.copier.mirror(setup.source, setup.dest))
    route = env.repo.checkpoint_route(setup.source.id, setup.dest.id, 2)
    assert route.get("status") == "skipped", f"skip não resolveu a posição: {route}"
    assert not env.repo.order_pending_before(setup.source.id, setup.dest.id, None, 3)


def test_fila_de_repeticao_nao_abre_buraco(env) -> None:
    """Regressão D9: msg2 falha na fila e msg3 não pode ser entregue antes dela."""
    from tests.test_ordem import TransientTextClient

    messages = [text_message(i, f"msg {i}") for i in (1, 2, 3)]
    client = TransientTextClient(messages, fail_always={2})
    setup = make_copier(env, messages, client=client, max_retries=0)
    env.repo.add_failure(setup.source.id, setup.dest.id, 2, "falhou antes")
    env.repo.add_failure(setup.source.id, setup.dest.id, 3, "falhou antes")
    with fast_sleep():
        asyncio.run(setup.copier.retry_messages(setup.source, setup.dest, [2, 3]))
    sent = [s["text"] for s in client.sent]
    marker = env.repo.get_placeholder(setup.source.id, setup.dest.id, 2)
    assert marker and marker["state"] == "open", "posição não reservada na fila"
    routes = _routes(env.repo, setup.source.id, setup.dest.id, (2, 3))
    assert all(routes) and routes == sorted(routes), f"buraco na fila: {routes} sent={sent}"


def test_fila_de_repeticao_retenta_transitorio(env) -> None:
    from tests.test_ordem import TransientTextClient

    messages = [text_message(i, f"msg {i}") for i in (1, 2)]
    client = TransientTextClient(messages, fail_once={1})
    setup = make_copier(env, messages, client=client, max_retries=2)
    env.repo.add_failure(setup.source.id, setup.dest.id, 1, "falhou antes")
    env.repo.add_failure(setup.source.id, setup.dest.id, 2, "falhou antes")
    with fast_sleep():
        result = asyncio.run(setup.copier.retry_messages(setup.source, setup.dest, [1, 2]))
    assert result["done"] == 2 and result["errors"] == 0
    assert [s["text"] for s in client.sent] == ["msg 1", "msg 2"]

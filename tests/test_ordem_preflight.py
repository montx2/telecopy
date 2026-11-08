"""Regressão D5 — o preflight não pode ser cegado por filtro nem por tópico alheio."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from database import Status
from tests import test_ordem as _ordem
from tests.fake_telegram import text_message

env = _ordem.env
fast_sleep = _ordem.fast_sleep
make_copier = _ordem.make_copier
tl_video = _ordem.tl_video


def test_preflight_nao_e_cegado_por_filtro(env) -> None:
    """msg2 falhou sem marcador; reexecutar com filtro não pode ultrapassá-la."""
    messages = [tl_video(1), text_message(2, "falhou"), tl_video(3)]
    setup = make_copier(env, messages)
    env.repo.upsert_checkpoint(setup.source.id, setup.dest.id, 1, Status.DONE, dest_msg_id=1001)
    env.repo.upsert_checkpoint(setup.source.id, setup.dest.id, 2, Status.ERROR)
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest, filter_code="videos"))
    assert result["errors"] == 1
    assert "ordem" in (result["first_error"] or "").lower()
    assert not [s for s in setup.client.sent if s.get("input_media") is not None]


def test_pendencia_de_outro_topico_nao_bloqueia(env) -> None:
    def topic_msg(i, topic):
        return text_message(i, f"msg {i}", reply_to=SimpleNamespace(
            reply_to_msg_id=topic, reply_to_top_id=topic, forum_topic=True))

    messages = [topic_msg(1, 11), topic_msg(2, 22), topic_msg(3, 22)]
    setup = make_copier(env, messages)
    # Pendência no tópico 911 não pode impedir o tópico 922.
    env.repo.upsert_checkpoint(setup.source.id, setup.dest.id, 1, Status.ERROR, dest_topic=911)
    with fast_sleep():
        result = asyncio.run(setup.copier.run(
            setup.source, setup.dest, topic_map={11: 911, 22: 922}))
    assert result["errors"] == 0
    sent = [s["text"] for s in setup.client.sent if s.get("text")]
    assert "msg 2" in sent and "msg 3" in sent


def test_pendencia_posterior_nao_bloqueia(env) -> None:
    """Pendência com msg_id maior que tudo que será enviado é estado normal."""
    messages = [text_message(1, "msg 1"), text_message(2, "msg 2")]
    setup = make_copier(env, messages)
    env.repo.upsert_checkpoint(setup.source.id, setup.dest.id, 9, Status.ERROR)
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert result["errors"] == 0 and result["done"] == 2


def test_mensagem_apagada_da_origem_resolve_posicao(env) -> None:
    """Sem isso, uma pendência de mensagem inexistente travaria o preflight para sempre."""
    messages = [text_message(1, "msg 1")]
    setup = make_copier(env, messages)
    env.repo.upsert_checkpoint(setup.source.id, setup.dest.id, 7, Status.ERROR)
    env.repo.add_failure(setup.source.id, setup.dest.id, 7, "falhou antes")
    with fast_sleep():
        result = asyncio.run(setup.copier.retry_messages(setup.source, setup.dest, [7]))
    assert result["skipped"] == 1 and result["intentional_skips"] == 1
    route = env.repo.checkpoint_route(setup.source.id, setup.dest.id, 7)
    assert route["status"] == "skipped"
    assert not env.repo.order_pending_before(setup.source.id, setup.dest.id, None, 8)

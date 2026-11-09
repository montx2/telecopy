"""O FakeClient deve filtrar tópico antes de aplicar limit, como a API real."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from tests.fake_telegram import FakeClient, channel, text_message


def _msg_topic(msg_id: int, top: int):
    m = text_message(msg_id, f"m{msg_id}")
    m.reply_to = SimpleNamespace(reply_to_top_id=top, reply_to_msg_id=top)
    m.reply_to_msg_id = top
    return m


def test_iter_messages_filtra_topico_antes_do_limit() -> None:
    # 5 mensagens gerais seguidas de 3 do tópico 7; limit=2 no tópico deve
    # devolver as 2 primeiras do tópico, não vazio (bug: limit antes do filtro).
    msgs = [text_message(i, f"g{i}") for i in range(1, 6)]
    msgs += [_msg_topic(i, 7) for i in range(6, 9)]
    client = FakeClient(msgs)
    source = channel(-1, "S")
    client.register_entity(source)

    async def coleta():
        return [m.id async for m in client.iter_messages(source, reply_to=7, limit=2, reverse=True)]

    assert asyncio.run(coleta()) == [6, 7]


def test_iter_messages_limit_sem_topico_inalterado() -> None:
    client = FakeClient([text_message(i, str(i)) for i in range(1, 6)])
    source = channel(-1, "S")
    client.register_entity(source)

    async def coleta():
        return [m.id async for m in client.iter_messages(source, limit=2, reverse=True)]

    assert asyncio.run(coleta()) == [1, 2]

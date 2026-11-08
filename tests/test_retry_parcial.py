"""Regressão D1/D2 — retentativa após confirmação parcial.

Uma falha transitória no meio de um item não pode reenviar o que o Telegram já
aceitou: isso criaria duplicata no destino e quebraria a ordem estrita por tópico.
Cada envio confirmado (inclusive continuação de legenda) conta no limite diário.
"""

from __future__ import annotations

import asyncio

import copier as cm
from tests import test_ordem as _ordem
from tests.fake_telegram import FakeClient, text_message

# Fixture e helpers compartilhados, reexportados como nomes de módulo para o
# pytest coletar sem sombrear import (pyflakes limpo).
env = _ordem.env
fast_sleep = _ordem.fast_sleep
make_copier = _ordem.make_copier
tl_video = _ordem.tl_video


class ContinuationFailsOnce(FakeClient):
    """A mídia entra; a continuação da legenda falha uma vez (transitório)."""

    def __init__(self, messages):
        super().__init__(messages)
        self.failed = False

    async def send_message(self, entity, text, reply_to=None, **kwargs):
        if not self.failed:
            self.failed = True
            raise OSError("rede caiu na continuação")
        return await super().send_message(entity, text, reply_to=reply_to, **kwargs)


class SecondContinuationFailsOnce(FakeClient):
    """Retoma após reinício pelo ponto salvo, sem baixar/enviar a mídia de novo."""

    def __init__(self, messages):
        super().__init__(messages)
        self.text_calls = 0

    async def send_message(self, entity, text, reply_to=None, **kwargs):
        self.text_calls += 1
        if self.text_calls == 2:
            raise OSError("falha depois da primeira continuação confirmada")
        return await super().send_message(entity, text, reply_to=reply_to, **kwargs)


class SecondAlbumChunkFailsOnce(FakeClient):
    def __init__(self, messages):
        super().__init__(messages)
        self.calls = 0

    async def __call__(self, request):
        if type(request).__name__ == "SendMultiMediaRequest":
            self.calls += 1
            if self.calls == 2:
                raise OSError("rede caiu no segundo bloco do álbum")
        return await super().__call__(request)


def _media_sends(client):
    return [s for s in client.sent if s.get("input_media") is not None]


def test_retry_nao_reenvia_midia_ja_confirmada(env) -> None:
    msg = tl_video(1, "x" * 1500)
    client = ContinuationFailsOnce([msg])
    setup = make_copier(env, [msg], client=client, max_retries=2)
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert result["done"] == 1 and result["errors"] == 0
    sends = _media_sends(client)
    assert len(sends) == 1, f"mídia reenviada no retry: {[s['msg_id'] for s in sends]}"
    # a continuação que falhou foi entregue na retentativa
    assert len([s for s in client.sent if s.get("text")]) == 1
    assert env.repo.checkpoint_route(setup.source.id, setup.dest.id, 1)["dest_msg_id"] == sends[0]["msg_id"]


def test_retry_nao_reenvia_bloco_de_album_confirmado(env) -> None:
    messages = [tl_video(i, f"p{i}", grouped_id=500) for i in range(1, 13)]
    client = SecondAlbumChunkFailsOnce(messages)
    setup = make_copier(env, messages, client=client, max_retries=2)
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert result["done"] == 12 and result["errors"] == 0
    sends = _media_sends(client)
    assert len(sends) == 12, f"bloco confirmado reenviado: {len(sends)} envios para 12 msgs"
    ids = [s["msg_id"] for s in sends]
    assert ids == sorted(ids) and len(set(ids)) == 12
    routes = [env.repo.checkpoint_route(setup.source.id, setup.dest.id, i)["dest_msg_id"]
              for i in range(1, 13)]
    assert routes == ids


def test_retry_upload_nao_reenvia_midia_confirmada(env) -> None:
    msg = tl_video(1, "x" * 1500)
    client = ContinuationFailsOnce([msg])
    setup = make_copier(env, [msg], client=client, max_retries=2, server_copy=False)
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert result["done"] == 1
    assert len(client.downloads) == 1
    assert len([s for s in client.sent if s.get("path")]) == 1


def test_retry_reabre_checkpoint_parcial_sem_baixar_ou_reenviar_media(env) -> None:
    from unittest.mock import patch

    msg = tl_video(1, "z" * 5000)
    client = SecondContinuationFailsOnce([msg])
    setup = make_copier(env, [msg], client=client, max_retries=0,
                        server_copy=False, placeholder=False, on_failure="abort")
    with patch.object(setup.copier, "_prompt_failure", return_value="a"), fast_sleep():
        first = asyncio.run(setup.copier.run(setup.source, setup.dest))
    route = env.repo.checkpoint_route(setup.source.id, setup.dest.id, 1)
    assert first["errors"] == 1 and route["status"] == "error"
    assert route["dest_msg_id"] and route["continuation_parts_sent"] == 1

    resumed_copier = cm.Copier(
        client, repo=env.repo, config=setup.copier.cfg, rich_ok=False,
    )
    with fast_sleep():
        resumed = asyncio.run(resumed_copier.retry_messages(setup.source, setup.dest, [1]))
    assert resumed["done"] == 1 and resumed["errors"] == 0
    assert client.downloads == [1]
    assert len([s for s in client.sent if s.get("kind") == "file"]) == 1
    parts = resumed_copier._parts(msg, cm.CAPTION_LIMIT)
    assert len([s for s in client.sent if s.get("kind") == "text"]) == len(parts) - 1
    final_route = env.repo.checkpoint_route(setup.source.id, setup.dest.id, 1)
    assert final_route["status"] == "done"
    assert final_route["continuation_parts_sent"] == len(parts) - 1


def test_cada_envio_confirmado_conta_no_limite_diario(env) -> None:
    msg = tl_video(1, "x" * 1500)
    client = ContinuationFailsOnce([msg])
    setup = make_copier(env, [msg], client=client, max_retries=2)
    before = cm.daily_limit.status()[1]
    with fast_sleep():
        asyncio.run(setup.copier.run(setup.source, setup.dest))
    counted = cm.daily_limit.status()[1] - before
    assert counted == len(client.sent), (
        f"{len(client.sent)} envios reais, {counted} contados no limite diário")


def test_texto_longo_retoma_apenas_o_restante(env) -> None:
    class SecondChunkFails(FakeClient):
        def __init__(self, messages):
            super().__init__(messages)
            self.calls = 0

        async def send_message(self, entity, text, reply_to=None, **kwargs):
            self.calls += 1
            if self.calls == 2:
                raise OSError("rede caiu no segundo bloco de texto")
            return await super().send_message(entity, text, reply_to=reply_to, **kwargs)

    msg = text_message(1, "y" * 5000)
    client = SecondChunkFails([msg])
    setup = make_copier(env, [msg], client=client, max_retries=2)
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert result["done"] == 1 and result["errors"] == 0
    texts = [s["text"] for s in client.sent]
    assert len(texts) == 2, f"primeiro bloco reenviado: {len(texts)} blocos"
    assert "".join(texts) == "y" * 5000

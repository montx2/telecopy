"""SONDAS DE AUDITORIA — cada teste expressa o comportamento EXIGIDO pelo contrato.
Um teste que FALHA aqui = bug real confirmado, não teste mal escrito.
Arquivo temporário de auditoria; não faz parte da suíte entregue.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import copier as cm
import core
from database import Status
from tests.fake_telegram import FakeClient, text_message
from tests.test_ordem import env, fast_sleep, make_copier, tl_video  # noqa: F401


class ContinuationFailsOnce(FakeClient):
    """Mídia vai OK; a continuação de legenda longa falha 1x (transitório)."""

    def __init__(self, messages):
        super().__init__(messages)
        self.failed = False

    async def send_message(self, entity, text, reply_to=None, **kwargs):
        if not self.failed:
            self.failed = True
            raise OSError("rede caiu na continuação")
        return await super().send_message(entity, text, reply_to=reply_to, **kwargs)


def test_probe1_retry_nao_reenvia_midia_ja_confirmada(env) -> None:  # noqa: F811
    msg = tl_video(1, "x" * 1500)
    client = ContinuationFailsOnce([msg])
    setup = make_copier(env, [msg], client=client, max_retries=2)
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    media_sends = [s for s in client.sent if s.get("input_media") is not None]
    print(f"\nPROBE1 done={result['done']} errors={result['errors']} "
          f"media_sends={len(media_sends)} ids={[s['msg_id'] for s in media_sends]}")
    assert len(media_sends) == 1, (
        f"DUPLICATA: mídia confirmada foi reenviada no retry "
        f"({len(media_sends)} envios, ids {[s['msg_id'] for s in media_sends]})")


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


def test_probe2_retry_album_nao_reenvia_bloco_confirmado(env) -> None:  # noqa: F811
    messages = [tl_video(i, f"p{i}", grouped_id=500) for i in range(1, 13)]
    client = SecondAlbumChunkFailsOnce(messages)
    setup = make_copier(env, messages, client=client, max_retries=2)
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    media_sends = [s for s in client.sent if s.get("input_media") is not None]
    print(f"\nPROBE2 done={result['done']} errors={result['errors']} "
          f"media_sends={len(media_sends)} (esperado 12)")
    assert len(media_sends) == 12, (
        f"DUPLICATA: bloco de álbum já confirmado foi reenviado "
        f"({len(media_sends)} envios para 12 mensagens)")


class ForwardFailsOnce(FakeClient):
    def __init__(self, messages):
        super().__init__(messages)
        self.failed = False

    async def forward_messages(self, entity, messages, from_peer=None, **kwargs):
        ids = messages if isinstance(messages, list) else [messages]
        if 2 in ids and not self.failed:
            self.failed = True
            raise OSError("falha transitória no forward")
        return await super().forward_messages(entity, messages, from_peer=from_peer, **kwargs)


def test_probe3_forward_nao_avanca_sem_reservar_posicao(env) -> None:  # noqa: F811
    messages = [text_message(i, f"msg {i}") for i in range(1, 5)]
    client = ForwardFailsOnce(messages)
    setup = make_copier(env, messages, client=client, forward_mode=True)
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    forwarded = [i for call in client.forwarded for i in call["ids"]]
    ph = env.repo.placeholder_stats(setup.source.id, setup.dest.id)
    print(f"\nPROBE3 forwarded={forwarded} placeholders={ph} "
          f"done={result['done']} errors={result['errors']} interrupted={result['interrupted']}")
    # Contrato: ou retenta e entrega na ordem, ou reserva a posição antes de seguir.
    routes = [env.repo.checkpoint_route(setup.source.id, setup.dest.id, i)["dest_msg_id"]
              for i in (1, 2, 3, 4)]
    ok_retry = forwarded == [1, 2, 3, 4]
    ok_barrier = ph["open"] >= 1 and all(routes) and routes == sorted(routes)
    assert ok_retry or ok_barrier, (
        f"ORDEM VIOLADA: forwarded={forwarded} marcadores={ph} routes={routes}")


def test_probe4_forward_done_exige_id_confirmado(env) -> None:  # noqa: F811
    messages = [text_message(1, "msg 1")]
    setup = make_copier(env, messages, forward_mode=True)
    # Resposta não parseável (parser ausente) -> _forward_item devolve [None]
    setup.client._get_response_message = None
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest, dest_topic=911))
    route = env.repo.checkpoint_route(setup.source.id, setup.dest.id, 1)
    print(f"\nPROBE4 done={result['done']} route={route}")
    assert not (result["done"] and not route.get("dest_msg_id")), (
        "DONE gravado SEM ID de destino confirmado")


def test_probe5_verificador_sem_falso_positivo(tmp_path, monkeypatch) -> None:
    """Item não entregue no FIM da sequência não é violação de ordem."""
    from database import Repository
    repo = Repository(tmp_path / "r.db")
    monkeypatch.setattr(core, "db", repo)
    repo.upsert_checkpoint(1, 2, 1, Status.DONE, dest_msg_id=1001)
    repo.upsert_checkpoint(1, 2, 2, Status.ERROR)  # falhou, sem marcador, é o último
    captured = {}
    for name in ("clear", "header", "pause", "warn", "ok", "info", "err"):
        monkeypatch.setattr(core.ui, name, lambda *a, **k: None)
    monkeypatch.setattr(core.ui, "box",
                        lambda title, rows, color=None: captured.update(rows=rows))
    app = core.App.__new__(core.App)
    app._tool_order()
    print(f"\nPROBE5 rows={captured['rows']}")
    assert captured["rows"][0][1] == "0", (
        "FALSO POSITIVO: pendência no fim da sequência reportada como violação")


def test_probe6_preflight_detecta_buraco_fora_do_filtro(env) -> None:  # noqa: F811
    """msg2 (texto) falhou sem marcador; reexecutar com filtro 'videos' não pode ignorá-la."""
    messages = [tl_video(1), text_message(2, "falhou"), tl_video(3)]
    setup = make_copier(env, messages)
    env.repo.upsert_checkpoint(setup.source.id, setup.dest.id, 1, Status.DONE, dest_msg_id=1001)
    env.repo.upsert_checkpoint(setup.source.id, setup.dest.id, 2, Status.ERROR)
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest, filter_code="videos"))
    sent_media = [s for s in setup.client.sent if s.get("input_media") is not None]
    print(f"\nPROBE6 errors={result['errors']} first_error={result['first_error']} "
          f"envios={len(sent_media)}")
    assert result["errors"] == 1 and not sent_media, (
        "BURACO: msg3 entregue apesar de msg2 não resolvida (filtro escondeu a pendência)")

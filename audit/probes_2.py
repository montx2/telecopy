"""Sondas complementares de auditoria (temporárias, fora da suíte entregue)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from config import RunConfig
from tests.fake_telegram import FakeClient, text_message
from tests.test_ordem import env, fast_sleep, make_copier, tl_video  # noqa: F401
from tests.test_audit_probe import ContinuationFailsOnce


def test_probe7_upload_path_tambem_duplica(env) -> None:  # noqa: F811
    msg = tl_video(1, "x" * 1500)
    client = ContinuationFailsOnce([msg])
    setup = make_copier(env, [msg], client=client, max_retries=2, server_copy=False)
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    print(f"\nPROBE7 downloads={client.downloads} "
          f"media_sends={len([s for s in client.sent if s.get('path')])} done={result['done']}")
    assert len(client.downloads) == 1, "DUPLICATA no caminho upload: mídia reenviada no retry"


def test_probe8_limite_diario_conta_envios_reais(env) -> None:  # noqa: F811
    import copier as cm
    msg = tl_video(1, "x" * 1500)
    client = ContinuationFailsOnce([msg])
    setup = make_copier(env, [msg], client=client, max_retries=2)
    before = cm.daily_limit.status()[1]
    with fast_sleep():
        asyncio.run(setup.copier.run(setup.source, setup.dest))
    after = cm.daily_limit.status()[1]
    real_sends = len(client.sent)
    print(f"\nPROBE8 envios_reais={real_sends} limite_contado={after - before}")
    assert after - before >= real_sends, (
        f"LIMITE DIÁRIO subcontado: {real_sends} envios reais, {after - before} contados")


class ForwardRawClient(FakeClient):
    async def __call__(self, request):
        if type(request).__name__ == "ForwardMessagesRequest":
            await self.forward_messages(request.to_peer, request.id, from_peer=request.from_peer)
            return SimpleNamespace()
        return await super().__call__(request)


def test_probe4b_forward_done_sem_id_confirmado(env) -> None:  # noqa: F811
    messages = [text_message(1, "msg 1")]
    client = ForwardRawClient(messages)
    setup = make_copier(env, messages, client=client, forward_mode=True)
    client._get_response_message = lambda *a, **k: None  # resposta não parseável
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest, dest_topic=911))
    route = env.repo.checkpoint_route(setup.source.id, setup.dest.id, 1)
    print(f"\nPROBE4b done={result['done']} route={route} forwarded={client.forwarded}")
    assert not (result["done"] and not route.get("dest_msg_id")), (
        "DONE gravado SEM ID de destino confirmado (forward)")


def test_probe9_delivery_forward_e_honrado(env) -> None:  # noqa: F811
    messages = [text_message(1, "msg 1")]
    setup = make_copier(env, messages, delivery="forward")
    with fast_sleep():
        asyncio.run(setup.copier.run(setup.source, setup.dest))
    print(f"\nPROBE9 delivery={setup.copier.cfg.delivery} "
          f"forward_mode={setup.copier.cfg.forward_mode} forwarded={len(setup.client.forwarded)}")
    assert setup.client.forwarded, (
        "delivery='forward' ignorado pelo motor: fez cópia em vez de encaminhar")


def test_probe10_slots_contam_no_limite(env) -> None:  # noqa: F811
    import copier as cm
    msg = tl_video(1, "x" * 3000)
    client = FakeClient([msg], fail_ids={1})
    setup = make_copier(env, [msg], client=client, server_copy=False, max_retries=0)
    before = cm.daily_limit.status()[1]
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    after = cm.daily_limit.status()[1]
    parts = env.repo.placeholder_parts(setup.source.id, setup.dest.id, 1)
    print(f"\nPROBE10 placeholders={result['placeholders']} slots={len(parts)} "
          f"limite_contado={after - before} envios={len(client.sent)}")
    assert after - before == len(client.sent), "slots/marcador fora do limite diário"

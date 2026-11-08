import asyncio
from types import SimpleNamespace
from tests import test_ordem as _ordem
from tests.fake_telegram import FakeClient, text_message

env = _ordem.env
fast_sleep = _ordem.fast_sleep
make_copier = _ordem.make_copier


class MirrorClient(FakeClient):
    """Alimenta o handler do espelho e falha 1x (transitorio) no msg 2."""
    def __init__(self, messages, permanent=False):
        super().__init__(messages)
        self.failed = False
        self.permanent = permanent
        self.attempts = []

    async def send_message(self, entity, text, reply_to=None, **kwargs):
        self.attempts.append(text)
        if text == "msg 2" and (self.permanent or not self.failed):
            self.failed = True
            raise OSError("rede caiu no espelho")
        return await super().send_message(entity, text, reply_to=reply_to, **kwargs)

    async def run_until_disconnected(self):
        for message in self.messages:
            for handler in list(self.handlers):
                await handler(SimpleNamespace(message=message))


def test_probe_espelho_transitorio(env):
    messages = [text_message(i, f"msg {i}") for i in range(1, 5)]
    client = MirrorClient(messages)
    setup = make_copier(env, messages, client=client, max_retries=2)
    with fast_sleep():
        asyncio.run(setup.copier.mirror(setup.source, setup.dest))
    sent = [s["text"] for s in client.sent]
    ph = env.repo.placeholder_stats(setup.source.id, setup.dest.id)
    route2 = env.repo.checkpoint_route(setup.source.id, setup.dest.id, 2)
    print(f"\nESPELHO-TRANSITORIO sent={sent} ph={ph} route2={route2}")
    assert "msg 2" in sent or ph["open"] >= 1, (
        f"BURACO no espelho: msg2 falhou sem marcador e seguiu para {sent}")


def test_probe_espelho_permanente(env):
    messages = [text_message(i, f"msg {i}") for i in range(1, 5)]
    client = MirrorClient(messages, permanent=True)
    setup = make_copier(env, messages, client=client, max_retries=0)
    with fast_sleep():
        asyncio.run(setup.copier.mirror(setup.source, setup.dest))
    sent = [s["text"] for s in client.sent]
    ph = env.repo.placeholder_stats(setup.source.id, setup.dest.id)
    print(f"\nESPELHO-PERMANENTE sent={sent} ph={ph}")
    routes = [env.repo.checkpoint_route(setup.source.id, setup.dest.id, i)["dest_msg_id"]
              for i in (1, 2, 3, 4)]
    assert all(routes) and routes == sorted(routes), (
        f"BURACO no espelho: rotas {routes}, sent={sent}, ph={ph}")

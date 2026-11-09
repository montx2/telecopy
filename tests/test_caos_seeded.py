"""Caos seeded na rede simulada: falhas transitórias não quebram ordem/retomada."""
from __future__ import annotations

import asyncio
import random
from types import SimpleNamespace

import pytest

import copier as copier_module
from config import RunConfig
from copier import Copier
from database import Repository
from tests.fake_telegram import FakeClient, channel, text_message
from utils import DailyLimit
from tests.relogio_simulado import relogio_simulado


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(copier_module, "TEMP_DIR", tmp_path / "tmp")
    monkeypatch.setattr(copier_module, "daily_limit", DailyLimit(tmp_path / "daily.json"))
    repo = Repository(tmp_path / "caos.db")
    try:
        yield SimpleNamespace(root=tmp_path, repo=repo)
    finally:
        repo.close()


class ClienteCaos(FakeClient):
    def __init__(self, messages, seed: int):
        super().__init__(messages)
        self.rng = random.Random(seed)
        self.failures = 0

    async def send_message(self, entity, text, reply_to=None, **kwargs):
        if self.rng.random() < 0.25:
            self.failures += 1
            raise ConnectionError("caos seeded")
        if self.rng.random() < 0.2:
            await asyncio.sleep(self.rng.uniform(0, 0.01))
        return await super().send_message(entity, text, reply_to=reply_to, **kwargs)


@pytest.mark.parametrize("seed", [1, 7, 42, 1234])
def test_caos_seeded_preserva_ordem_e_nao_duplica(env, seed) -> None:
    messages = [text_message(i, f"m{i}") for i in range(1, 31)]
    client = ClienteCaos(messages, seed)
    source, dest = channel(-1, "S"), channel(-2, "D")
    client.register_entity(source), client.register_entity(dest)
    copier = Copier(client, repo=env.repo, config=RunConfig(
        workers=3, delay=0, use_dedup=False, max_retries=8,
    ), rich_ok=False)
    with relogio_simulado() as loop:
        result = loop.run_until_complete(copier.run(source, dest))
    assert result["errors"] == 0 and result["done"] == 30
    assert [r["text"] for r in client.sent] == [m.message for m in messages]
    assert client.failures > 0
    assert not list((env.root / "tmp").glob("*"))

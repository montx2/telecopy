"""Sinais portáveis e CLI verdadeiramente não interativa."""
from __future__ import annotations

import pytest

import asyncio
import signal

from telethon.errors import ChatAdminRequiredError

import copier as copier_module
import telecopy_cli
from config import RunConfig
from copier import Copier
from database import Repository
from tests.fake_telegram import FakeClient, channel, text_message
from utils import DailyLimit


@pytest.mark.skipif(not hasattr(signal, "raise_signal"), reason="raise_signal indisponível nesta plataforma")
def test_fallback_signal_restaura_handler_antigo(monkeypatch) -> None:
    old = signal.getsignal(signal.SIGTERM)

    def sem_loop_handler(*_a):
        raise NotImplementedError

    monkeypatch.setattr(asyncio.get_event_loop_policy(), "new_event_loop",
                        asyncio.get_event_loop_policy().new_event_loop)

    async def cenario():
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "add_signal_handler", sem_loop_handler)
        copier = Copier(FakeClient([]), config=RunConfig(), rich_ok=False)

        async def lento():
            await asyncio.sleep(30)
            return {}

        task = asyncio.ensure_future(telecopy_cli._com_sinais(lento(), copier))
        await asyncio.sleep(0)
        signal.raise_signal(signal.SIGTERM)
        return await task

    result = asyncio.run(cenario())
    assert result["interrupted"] is True
    assert signal.getsignal(signal.SIGTERM) is old


def test_cli_nao_abre_prompt_em_erro_fatal(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(copier_module, "TEMP_DIR", tmp_path / "tmp")
    monkeypatch.setattr(copier_module, "daily_limit", DailyLimit(tmp_path / "daily.json"))

    def nao_interativo(*_a, **_k):
        raise AssertionError("CLI não pode pedir input")

    monkeypatch.setattr(copier_module.ui, "ask", nao_interativo)
    repo = Repository(tmp_path / "cli.db")
    try:
        client = FakeClient([text_message(1, "x")])
        source, dest = channel(-1, "S"), channel(-2, "D")
        client.register_entity(source), client.register_entity(dest)

        async def falha(*_a, **_k):
            raise ChatAdminRequiredError(request=None)

        client.send_message = falha
        args = telecopy_cli.build_parser().parse_args([
            "clone", "--src", "-1", "--dst", "-2", "--json",
            "--report-dir", str(tmp_path / "reports"),
        ])
        code = asyncio.run(telecopy_cli.executar(args, client, repo))
        assert code == 1
    finally:
        repo.close()

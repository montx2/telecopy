"""Recursos P1 opt-in: proxy, janela, banda, reescrita, webhook, arquivo e UI."""
from __future__ import annotations

import asyncio
import json
import threading
import urllib.error
import urllib.request
from datetime import datetime

import pytest

import copier as copier_module
import opcional
import telecopy_cli
from database import Repository
from tests.fake_telegram import FakeClient, channel, document_message, text_message
from tests.relogio_simulado import relogio_simulado
from utils import DailyLimit, LimitadorBanda


def test_proxy_parse_e_invalido() -> None:
    p = opcional.parse_proxy("socks5://user:pass@127.0.0.1:1080")
    assert p["proxy_type"] == "socks5" and p["port"] == 1080 and p["username"] == "user"
    with pytest.raises(ValueError):
        opcional.parse_proxy("ftp://x:1")
    with pytest.raises(ValueError):
        opcional.parse_proxy("socks5://sem-porta")


def test_janela_normal_e_overnight() -> None:
    agora = datetime(2026, 1, 1, 12, 0)
    assert opcional.dentro_janela(agora, "09:00", "17:00")
    assert not opcional.dentro_janela(agora, "13:00", "17:00")
    assert opcional.dentro_janela(datetime(2026, 1, 1, 23, 0), "22:00", "06:00")
    assert opcional.dentro_janela(datetime(2026, 1, 1, 2, 0), "22:00", "06:00")
    assert opcional.segundos_ate_janela(datetime(2026, 1, 1, 12, 0), "13:00", "17:00") == 3600
    with pytest.raises(ValueError):
        opcional.dentro_janela(agora, "09:00", None)


def test_reescrita_opt_in() -> None:
    assert opcional.reescrever_texto("a b a", "a", "x") == "x b x"
    assert opcional.reescrever_texto("a", "", "x") == "a"


def test_limitador_banda_relogio_simulado() -> None:
    async def cenario(loop):
        limiter = LimitadorBanda(1000)
        await limiter.consumir(1000)
        before = loop.time()
        await limiter.consumir(1000)
        assert loop.time() - before >= 1.0
        await LimitadorBanda(0).consumir(10**9)

    with relogio_simulado() as loop:
        loop.run_until_complete(cenario(loop))


def test_webhook_monkeypatch(monkeypatch) -> None:
    captured = {}

    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode())
        return Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert opcional.notificar_webhook("https://example.invalid/hook", {"ok": True})
    assert captured["body"] == {"ok": True}
    assert not opcional.notificar_webhook("ftp://x", {})


def test_arquivo_offline(tmp_path) -> None:
    client = FakeClient([text_message(1, "oi"), document_message(2, "a.pdf", size=64)])
    source = channel(-1, "S")
    client.register_entity(source)
    result = asyncio.run(opcional.arquivar(client, source, tmp_path / "arq"))
    assert result["total"] == 2 and result["midias"] == 1
    lines = (tmp_path / "arq" / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["file"]


def test_ui_local_exige_token(tmp_path) -> None:
    (tmp_path / "relatorio-x.html").write_text("<p>ok</p>", encoding="utf-8")
    server = opcional.criar_servidor_ui(tmp_path, "segredo", host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health?token=segredo", timeout=2) as resp:
            assert json.loads(resp.read())["readonly"] is True
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/relatorio?file=relatorio-x.html&token=segredo", timeout=2
        ) as resp:
            assert b"ok" in resp.read()
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/relatorio?file=../secret&token=segredo", timeout=2
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_cli_reescrita_e_filtro(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(copier_module, "TEMP_DIR", tmp_path / "tmp")
    monkeypatch.setattr(copier_module, "daily_limit", DailyLimit(tmp_path / "daily.json"))
    repo = Repository(tmp_path / "cli.db")
    try:
        client = FakeClient([text_message(1, "olá mundo"), document_message(2, "a.pdf")])
        source, dest = channel(-1, "S"), channel(-2, "D")
        client.register_entity(source), client.register_entity(dest)
        args = telecopy_cli.build_parser().parse_args([
            "clone", "--src", "-1", "--dst", "-2", "--json", "--filtro", "text",
            "--reescrever-de", "mundo", "--reescrever-para", "terra",
            "--report-dir", str(tmp_path / "reports"),
        ])
        code = asyncio.run(telecopy_cli.executar(args, client, repo))
        assert code == 0
        assert [r["text"] for r in client.sent] == ["olá terra"]
    finally:
        repo.close()

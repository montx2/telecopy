"""Cobertura de infraestrutura: cofre legado, JSON/log/estatísticas e opcionais."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import opcional
import security
import utils
from database import Repository, Status
from security import Vault, VaultError, _derive_key, _legacy_hardware_secret


def test_vault_formatos_legados_e_corrompidos(tmp_path, monkeypatch) -> None:
    path = tmp_path / "v.enc"
    vault = Vault(path)
    path.write_bytes(security.MAGIC + b"curto")
    assert vault.load() is None
    # MAGIC válido com chave errada: todos os candidatos falham.
    salt, nonce = b"s" * 32, b"n" * 12
    ct = AESGCM(b"k" * 32).encrypt(nonce, b"{}", None)
    path.write_bytes(security.MAGIC + salt + nonce + ct)
    assert vault.load() is None
    # Formato 3.0 base64 com segredo legado de hardware.
    secret = _legacy_hardware_secret()
    key = _derive_key(salt, __import__("hashlib").sha256(secret).digest())
    ct = AESGCM(key).encrypt(nonce, json.dumps({"legacy": 1}).encode(), None)
    path.write_bytes(base64.urlsafe_b64encode(salt + nonce + ct))
    assert vault.load() == {"legacy": 1}
    path.write_bytes(b"nem base64 nem json")
    assert vault.load() is None
    monkeypatch.setattr(Path, "unlink", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    vault.delete()  # não propaga OSError


def test_vault_chmod_oserror_e_sem_crypto(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(os, "chmod", lambda *a: (_ for _ in ()).throw(OSError("x")))
    v = Vault(tmp_path / "a.enc")
    v.save({"x": 1})
    assert v.load() == {"x": 1}
    monkeypatch.setattr(security, "_CRYPTO_OK", False)
    with pytest.raises(VaultError):
        v.save({"y": 2})


def test_legacy_hardware_secret_fallback(monkeypatch) -> None:
    original_exists = Path.exists
    original_read = Path.read_bytes

    def exists(self):
        return str(self) == "/etc/machine-id"

    def read_bytes(self):
        raise OSError("negado")

    monkeypatch.setattr(Path, "exists", exists)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    assert len(_legacy_hardware_secret()) == 32
    monkeypatch.setattr(Path, "exists", original_exists)
    monkeypatch.setattr(Path, "read_bytes", original_read)


def test_json_log_stats_fmt(tmp_path, monkeypatch) -> None:
    bad = tmp_path / "bad.json"
    monkeypatch.setattr(Path, "write_text", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    assert utils.write_json(tmp_path / "x.json", {}) is False
    monkeypatch.undo()
    bad.write_text("{corrupt", encoding="utf-8")
    assert utils.read_json(bad, {"d": 1}) == {"d": 1}
    assert utils.read_json(tmp_path / "missing", 7) == 7

    log = utils.Logger(tmp_path / "log.txt", level="WARNING")
    log.debug("não entra")
    log.warning("telefone +5511999999999 hash " + "a" * 40)
    log.error("erro")
    log.success("sucesso")
    log.critical("crítico")
    try:
        raise ValueError("boom")
    except ValueError:
        log.exception("falhou")
    text = (tmp_path / "log.txt").read_text(encoding="utf-8")
    assert "[PHONE]" in text and "[HASH]" in text and "não entra" not in text
    # rotação
    monkeypatch.setattr(utils, "_MAX_LOG_BYTES", 10)
    log.info("força rotação")
    assert (tmp_path / "log.txt").exists()
    utils.setup_telethon_logging("DEBUG")
    logging.getLogger("telethon").error("via telethon")

    daily = utils.DailyLimit(tmp_path / "daily.json")
    daily.reset()
    assert daily.status(10)[0] == "ok"
    daily.add(8)
    assert daily.status(10)[0] == "warn"
    daily.add(2)
    assert daily.status(10)[0] == "limit"
    assert daily.status(0)[0] == "ok"

    stats = utils.Stats(tmp_path / "stats.json")
    stats.new_session()
    stats.inc("photos", 2)
    stats.add_bytes(100, "sent")
    stats.add_bytes(50, "recv")
    stats.add_time(1.5)
    stats.save()
    assert utils.Stats(tmp_path / "stats.json").data["photos"] == 2

    assert utils.Fmt.duration(3661) == "1h 01m"
    assert utils.Fmt.duration(90000).startswith("1d")
    assert utils.Fmt.eta(0, 10, 1) == "—"
    assert utils.Fmt.eta(5, 10, 5) != "—"
    assert utils.Fmt.speed(0) == "—"
    assert utils.Fmt.speed(1024).endswith("/s")
    assert utils.Fmt.trunc("abcdef", 4).endswith("…")
    assert utils.Fmt.number(1234) == "1.234"
    assert utils.Fmt.date(None) == "—"
    assert utils.Fmt.date("inválido") == "inválido"
    assert utils.Fmt.date(time.time())
    assert utils.now_iso()
    assert utils.elapsed_since(time.time() - 1) >= 0


def test_opcional_caminhos_restantes(tmp_path, monkeypatch) -> None:
    with pytest.raises(ValueError):
        opcional._parse_hhmm("25:00")
    with pytest.raises(ValueError):
        opcional._parse_hhmm("9h")
    assert opcional.dentro_janela(datetime(2026, 1, 1, 9), "09:00", "09:00")
    assert opcional.segundos_ate_janela(datetime(2026, 1, 1, 18), "09:00", "17:00") > 0
    assert opcional.parse_proxy("http://h:8080")["proxy_type"] == "http"

    def boom(*a, **k):
        raise urllib.error.URLError("sem rede")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert not opcional.notificar_webhook("https://example.invalid", {})
    monkeypatch.undo()

    from tests.fake_telegram import FakeClient, channel, document_message, text_message
    client = FakeClient([text_message(1, "a"), document_message(2, "b.pdf")], fail_ids={2})
    source = channel(-1, "S")
    client.register_entity(source)
    result = asyncio.run(opcional.arquivar(client, source, tmp_path / "arq", limite=2, topico=None))
    assert result["total"] == 2 and result["midias"] == 0
    rows = [json.loads(l) for l in (tmp_path / "arq" / "manifest.jsonl").read_text().splitlines()]
    assert rows[1]["error"] == "OSError"

    with pytest.raises(ValueError):
        opcional.criar_servidor_ui(tmp_path, "")
    (tmp_path / "relatorio-a.html").write_text("<p>a</p>", encoding="utf-8")
    server = opcional.criar_servidor_ui(tmp_path, "t", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/?token=t", timeout=2) as resp:
            assert b"relatorio-a.html" in resp.read()
        req = urllib.request.Request(f"http://127.0.0.1:{port}/health", headers={"Authorization": "Bearer t"})
        with urllib.request.urlopen(req, timeout=2) as resp:
            assert resp.status == 200
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(f"http://127.0.0.1:{port}/nada?token=t", timeout=2)
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(f"http://127.0.0.1:{port}/relatorio?file=nope.html&token=t", timeout=2)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_database_caminhos_restantes(tmp_path) -> None:
    repo = Repository(tmp_path / "d.db")
    try:
        repo.update_status(1, 2, 1, Status.RUNNING, bytes_done=5)
        assert not repo.is_done(1, 2, 1)
        assert repo.last_processed_id(1, 2) == 0
        repo.mark_done(1, 2, 1, "h", dest_msg_id=10)
        repo.mark_done(1, 2, 2, "h2")
        assert repo.untopiced_checkpoints(1, 2) == 2
        assert repo.pair_summary(1, 2)["done"] == 2
        repo.register_session("s", 1, 2, "S", "D")
        repo.update_stats("s", success=1)
        repo.finish_session("s")
        assert repo.recent_sessions(1)
        assert repo.global_stats()["success"] == 1
        repo.add_failure(1, 2, 3, "x")
        assert repo.failure_stats()["total"] == 1
        assert repo.clear_failures() == 1
        repo.add_placeholder(1, 2, 4, 40, state="open")
        repo.add_placeholder_part(1, 2, 4, 1, 41)
        assert repo.placeholder_stats(1, 2)["open"] == 1
        assert repo.placeholder_pairs()
        assert repo.placeholder_msg_ids(1, 2) == {4}
        assert repo.order_rows(1, 2)
        assert repo.order_pairs()
        repo.set_kv("k", "v")
        assert repo.delete_kv("k") and not repo.delete_kv("k")
        assert repo.get_kv("k", "d") == "d"
        repo.vacuum()
        with pytest.raises(ValueError):
            repo.rollback_schema(99)
    finally:
        repo.close()

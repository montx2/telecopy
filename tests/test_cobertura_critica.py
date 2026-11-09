"""Cobertura dirigida de módulos críticos: segurança, dedup, verificação e transporte."""
from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace

import pytest

import copier as copier_module
from database import Repository, Status
from dedup import DedupEngine, hash_file, hash_message
from security import Vault, load_secure, save_secure
from tests.fake_telegram import FakeClient, channel, document_message, text_message
from verificacao import verificar_par


def test_vault_roundtrip_delete_e_corrompido(tmp_path) -> None:
    path = tmp_path / "vault.enc"
    vault = Vault(path)
    assert vault.load() is None and not vault.exists()
    vault.save({"api_id": 1, "api_hash": "x" * 32})
    assert vault.exists() and vault.is_readable()
    assert vault.load()["api_id"] == 1
    vault.set("lang", "pt")
    assert vault.get("lang") == "pt"
    vault.update(lang="en")
    assert vault.get("lang") == "en"
    save_secure({"a": 1}, path)
    assert load_secure(path)["a"] == 1
    vault.delete()
    assert not vault.exists()
    path.write_bytes(b"corrompido")
    assert Vault(path).load() is None
    assert not Vault(path).is_readable()
    path.write_text('{"legacy": true}', encoding="utf-8")
    assert Vault(path).load()["legacy"] is True


def test_vault_legacy_hardware_fallback(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("USERNAME", "tester")
    path = tmp_path / "v.enc"
    vault = Vault(path)
    vault.save({"k": "v"})
    assert vault.load()["k"] == "v"


def test_dedup_file_hash_e_engine(tmp_path) -> None:
    path = tmp_path / "a.bin"
    path.write_bytes(b"a" * 10000)
    h = hash_file(str(path))
    assert h == hashlib.sha256(path.read_bytes()).hexdigest()
    repo = Repository(tmp_path / "d.db")
    try:
        engine = DedupEngine(repo, enabled=True)
        msg = text_message(1, "oi")
        key = hash_message(msg)
        assert not engine.check(key, 2)
        engine.register(key, 2, 100, 10, 1)
        assert engine.check(key, 2) == 100
        engine.warm_up(2)
        assert "lru" in engine.stats()
        engine.enabled = False
        assert not engine.check(key, 2)
    finally:
        repo.close()


def test_verify_ausente_ambiguo_e_ordem(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(copier_module, "TEMP_DIR", tmp_path / "tmp")
    repo = Repository(tmp_path / "v.db")
    try:
        source, dest = channel(-1, "S"), channel(-2, "D")
        client = FakeClient([text_message(1, "a")])
        client.register_entity(source), client.register_entity(dest)
        m1 = asyncio.run(client.send_message(dest, "a"))
        m2 = asyncio.run(client.send_message(dest, "b"))
        repo.upsert_checkpoint(source.id, dest.id, 1, Status.DONE, dest_msg_id=999)
        result = asyncio.run(verificar_par(client, repo, source, dest))
        assert result.ausentes == [1] and not result.como_dict()["complete"]
        # ordem não crescente
        client.messages.extend([text_message(2, "b"), text_message(3, "c")])
        repo.upsert_checkpoint(source.id, dest.id, 2, Status.DONE, dest_msg_id=m2.id)
        repo.upsert_checkpoint(source.id, dest.id, 3, Status.DONE, dest_msg_id=m1.id)
        result = asyncio.run(verificar_par(client, repo, source, dest))
        assert result.ordem_ok is False
    finally:
        repo.close()


def test_fasttransfer_caminhos_de_erro_e_fallback(tmp_path) -> None:
    from fasttransfer import FastTransferError, FastTransferPool, fast_download, fast_upload, input_location
    msg = document_message(1, "a.bin", size=4096)
    client = FakeClient([msg])
    client.file_data[msg.media.document.id] = b"x" * 4096
    pool = FastTransferPool(client, 2, 2)

    async def cenario():
        with pytest.raises(FastTransferError):
            await fast_download(pool, SimpleNamespace(media=None), tmp_path / "x")
        src = tmp_path / "empty.bin"
        src.write_bytes(b"")
        with pytest.raises(FastTransferError):
            await fast_upload(pool, src, "empty")
        await pool.close()
        with pytest.raises(FastTransferError):
            await pool.acquire(None)

    asyncio.run(cenario())
    with pytest.raises(FastTransferError):
        input_location(SimpleNamespace(media=SimpleNamespace(document=None)))

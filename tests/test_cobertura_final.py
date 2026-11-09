"""Caminhos residuais críticos para elevar cobertura sem enfraquecer contratos."""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest

from dedup import DedupEngine, hash_bytes
from database import Repository
from fasttransfer import FastTransferError, FastTransferPool, _write_at, _read_part, input_location
from security import Vault, VaultError, _load_or_create_local_key
from tests.fake_telegram import FakeClient, channel, photo_message
from verificacao import _assinatura_destino, _historico_destino


def test_security_oserror_falha_fechado(tmp_path, monkeypatch) -> None:
    def fail(*a, **k):
        raise OSError("sem gravação")
    monkeypatch.setattr("security.Path.exists", fail)
    with pytest.raises(VaultError):
        _load_or_create_local_key(tmp_path)


def test_dedup_caminhos_de_erro_e_stats(tmp_path) -> None:
    repo = Repository(tmp_path / "d.db")
    try:
        engine = DedupEngine(repo, enabled=True)
        repo.register_dedup("h", 2, 10)
        assert engine.warm_up(2) == 1
        assert engine.warm_up(2) == 0
        assert engine.check("h", 2) == 10
        assert engine.check("", 2) is None
        engine.reset()
        assert engine.format_stats()
        engine.enabled = False
        engine.register("x", 2, 1)
        assert engine.check("x", 2) is None
        assert hash_bytes(b"a" * 10)
        assert hash_bytes(b"a" * 3000, sample=100)
    finally:
        repo.close()

    class RepoQuebrado:
        def load_dedup_hashes(self, *a):
            raise OSError("db")
        def check_dedup(self, *a):
            raise OSError("db")
        def register_dedup(self, *a):
            raise OSError("db")
    engine = DedupEngine(RepoQuebrado(), enabled=True)
    assert engine.warm_up(1) == 0
    engine._bloom.add("h:1")
    assert engine.check("h", 1) is None
    engine.register("h", 1, 2)


def test_verificacao_assinatura_e_topico() -> None:
    doc = SimpleNamespace(media=SimpleNamespace(document=SimpleNamespace(id=1, size=2), photo=None),
                          message=" x ")
    assert _assinatura_destino(doc).endswith("|x")
    photo = SimpleNamespace(media=SimpleNamespace(document=None, photo=SimpleNamespace(id=3)), message="")
    assert "photo:3" in _assinatura_destino(photo)
    client = FakeClient([])
    dest = channel(-2, "D")
    client.register_entity(dest)
    asyncio.run(client.send_message(dest, "oi"))
    msgs = asyncio.run(_historico_destino(client, dest, None, 10))
    assert len(msgs) == 1


def test_fasttransfer_borrowed_release_write_fallback(tmp_path, monkeypatch) -> None:
    client = FakeClient([])
    pool = FastTransferPool(client, 2, 2)

    async def cenario():
        async def create(dc):
            raise OSError("export indisponível")
        client._create_exported_sender = create
        info = await pool.acquire(2)
        assert info.borrowed
        await pool.release(info)
        await pool.close()

    asyncio.run(cenario())
    path = tmp_path / "f.bin"
    path.write_bytes(b"abc")
    monkeypatch.delattr(os, "pwrite", raising=False)
    _write_at(str(path), 1, b"Z")
    assert _read_part(str(path), 1, 1) == b"Z"
    with pytest.raises(OSError):
        _read_part(str(path), 10, 1)
    with pytest.raises(FastTransferError):
        input_location(SimpleNamespace(media=SimpleNamespace(photo=None)))
    photo = photo_message(1)
    photo.media.photo.sizes = []
    with pytest.raises(FastTransferError):
        input_location(photo)


def test_vault_erros_de_io(tmp_path, monkeypatch) -> None:
    vault = Vault(tmp_path / "v.enc")
    assert vault.load() is None and not vault.is_readable()
    monkeypatch.setattr("security.Path.write_bytes", lambda *a: (_ for _ in ()).throw(OSError("write")))
    with pytest.raises(VaultError):
        vault.save({"x": 1})
    monkeypatch.undo()
    monkeypatch.setattr("security.Path.read_bytes", lambda *a: (_ for _ in ()).throw(OSError("read")))
    vault.path.write_bytes(b"x")
    assert vault.load() is None

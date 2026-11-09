"""Cobertura adicional de caminhos críticos e propriedades."""
from __future__ import annotations

import asyncio
import os

import pytest
from hypothesis import given, settings, strategies as st
from telethon.errors import FileMigrateError, FileReferenceExpiredError

import copier as copier_module
from config import RunConfig
from copier import Copier, split_text, utf16_len
from database import Repository, Status
from dedup import BloomFilter, LRUCache, hash_file, hash_message
from fasttransfer import FastTransferPool, fast_download, input_location
from security import Vault, VaultError
from tests.fake_telegram import FakeClient, channel, document_message, photo_message, text_message
from tests.relogio_simulado import relogio_simulado
from verificacao import verificar_par


def test_bloom_lru_e_hash_file_grande(tmp_path) -> None:
    bloom = BloomFilter(capacity=100, fp_rate=0.01)
    bloom.add("a")
    assert "a" in bloom and bloom.count == 1
    assert bloom.fp_current() >= 0
    bloom.clear()
    assert bloom.count == 0
    lru = LRUCache(capacity=2)
    lru.put("a", 1); lru.put("b", 2); lru.put("c", 3)
    assert lru.get("a") is None and lru.get("c") == 3
    lru.clear()
    assert lru.stats()["items"] == 0
    path = tmp_path / "big.bin"
    path.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    assert hash_file(str(path), sample=1024)
    with pytest.raises(OSError):
        hash_file(str(tmp_path / "missing"))


def test_vault_sem_chave_persistivel_falha_fechado(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("security._local_key_path", lambda vault_dir: tmp_path / "missing" / "key")
    vault = Vault(tmp_path / "v.enc")
    monkeypatch.setattr("security.Path.mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError("sem gravação")))
    with pytest.raises(VaultError):
        vault.save({"a": 1})
    assert not (tmp_path / "v.tmp").exists()


def test_verify_ambiguo_bloqueia_reparo(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(copier_module, "TEMP_DIR", tmp_path / "tmp")
    repo = Repository(tmp_path / "v.db")
    try:
        source, dest = channel(-1, "S"), channel(-2, "D")
        client = FakeClient([text_message(1, "dup")])
        client.register_entity(source), client.register_entity(dest)
        asyncio.run(client.send_message(dest, "dup"))
        asyncio.run(client.send_message(dest, "dup"))
        repo.upsert_checkpoint(source.id, dest.id, 1, Status.DONE)
        copier = Copier(client, repo=repo, config=RunConfig(), rich_ok=False)
        fp = copier._intent_fingerprint(kind="text", text="dup", entities=None, dest_topic=None, part_index=0)
        repo.begin_intent(source.id, dest.id, 1, 0, fingerprint=fp, kind="text")
        result = asyncio.run(verificar_par(client, repo, source, dest, reparar=True))
        assert result.ambiguos == [1] and result.reparados == []
    finally:
        repo.close()


def test_fast_download_migrate_refresh_e_cdn(tmp_path) -> None:
    msg = document_message(1, "a.bin", size=4096)
    client = FakeClient([msg])
    client.file_data[msg.media.document.id] = b"x" * 4096
    client.part_errors = {("GetFileRequest", 0): [FileMigrateError(request=None, capture=2)]}
    pool = FastTransferPool(client, 2, 2)

    async def cenario():
        path = await fast_download(pool, msg, tmp_path / "m.bin")
        assert os.path.getsize(path) == 4096
        await pool.close()

    with relogio_simulado() as loop:
        loop.run_until_complete(cenario())
    assert client.created_senders

    msg2 = document_message(2, "b.bin", size=4096)
    client2 = FakeClient([msg2])
    client2.file_data[msg2.media.document.id] = b"y" * 4096
    client2.part_errors = {("GetFileRequest", 0): [FileReferenceExpiredError(request=None)]}
    pool2 = FastTransferPool(client2, 2, 2)

    async def refresh():
        return msg2

    async def cenario2():
        path = await fast_download(pool2, msg2, tmp_path / "r.bin", refresh=refresh)
        assert os.path.getsize(path) == 4096
        await pool2.close()

    with relogio_simulado() as loop:
        loop.run_until_complete(cenario2())

    photo = photo_message(3, "foto")
    dc, loc, size = input_location(photo)
    assert size > 0 and loc is not None


@settings(max_examples=50, deadline=None)
@given(st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=0x10FFFF,
                                     blacklist_categories=("Cs",)), min_size=0, max_size=5000))
def test_split_text_propriedades(texto: str) -> None:
    partes = split_text(texto, 4096)
    assert "".join(partes) == texto
    assert all(utf16_len(p) <= 4096 for p in partes)


@settings(max_examples=50, deadline=None)
@given(st.lists(st.tuples(st.integers(1, 1000), st.text(min_size=1, max_size=20)), min_size=1, max_size=30))
def test_hash_message_estavel(items) -> None:
    msgs = [text_message(i, t) for i, t in items]
    assert [hash_message(m) for m in msgs] == [hash_message(m) for m in msgs]

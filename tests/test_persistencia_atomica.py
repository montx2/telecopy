"""Regressões de atomicidade/manutenção do repositório e do motor."""
from __future__ import annotations

import sqlite3
import threading
import time

import pytest

import copier as copier_module
from config import RunConfig
from copier import Copier, TransferStats
from database import Repository, Status
from tests.fake_telegram import FakeClient, text_message
from utils import DailyLimit


@pytest.fixture
def repo(tmp_path):
    r = Repository(tmp_path / "atomic.db")
    try:
        yield r
    finally:
        r.close()


def test_record_delivery_rollback_nao_deixa_estado_pela_metade(repo, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(copier_module, "daily_limit", DailyLimit(tmp_path / "daily.json"))
    client = FakeClient([])
    copier = Copier(client, repo=repo, config=RunConfig(use_dedup=True), rich_ok=False)
    msg = text_message(1, "conteúdo")
    entry = {"msg": msg, "media_type": "text", "size": 0}
    repo.add_failure(1, 2, 1, "erro anterior")
    with repo._db() as conn:
        conn.execute(
            "CREATE TRIGGER falha_limpeza BEFORE DELETE ON failures "
            "BEGIN SELECT RAISE(ABORT, 'falha simulada'); END"
        )
    stats = TransferStats()
    with pytest.raises(sqlite3.IntegrityError):
        copier._record_delivered(entry, 1, 2, 100, None, stats)
    assert not repo.is_done(1, 2, 1)
    assert not repo.is_processed(1, 1, 2)
    assert repo.check_dedup(__import__("dedup").hash_message(msg), 2) is None
    assert len(repo.failures(1, 2)) == 1
    assert stats.done == 0
    assert copier.dedup.check(__import__("dedup").hash_message(msg), 2) is None


def test_record_delivery_confirma_checkpoint_processed_dedup_e_intencao(repo, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(copier_module, "daily_limit", DailyLimit(tmp_path / "daily.json"))
    copier = Copier(FakeClient([]), repo=repo, config=RunConfig(), rich_ok=False)
    msg = text_message(1, "ok")
    entry = {"msg": msg, "media_type": "text", "size": 0, "_source_id": 1, "_dest_id": 2}
    copier._begin_intent(entry, 0, None, kind="text", text="ok")
    repo.add_failure(1, 2, 1, "erro")
    stats = TransferStats()
    copier._record_delivered(entry, 1, 2, 100, None, stats)
    assert repo.is_done(1, 2, 1) and repo.is_processed(1, 1, 2)
    assert repo.failures(1, 2) == []
    assert repo.intent_for(1, 2, 1, 0)["state"] == "confirmed"
    assert stats.done == 1


def test_reset_pair_e_reset_all_apagam_intencoes_e_partes_de_reserva(repo) -> None:
    repo.upsert_checkpoint(1, 2, 1, Status.DONE, dest_msg_id=10)
    repo.upsert_checkpoint(1, 3, 1, Status.DONE, dest_msg_id=11)
    repo.begin_intent(1, 2, 1, 0, fingerprint="a")
    repo.begin_intent(1, 3, 1, 0, fingerprint="b")
    repo.add_placeholder_part(1, 2, 1, 1, 12)
    repo.add_placeholder_part(1, 3, 1, 1, 13)
    repo.reset_pair(1, 2)
    assert repo.intent_for(1, 2, 1, 0) == {}
    assert repo.placeholder_parts(1, 2, 1) == []
    assert repo.intent_for(1, 3, 1, 0)["fingerprint"] == "b"
    assert len(repo.placeholder_parts(1, 3, 1)) == 1
    repo.reset_all()
    assert repo.intent_for(1, 3, 1, 0) == {}
    with repo._db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM placeholder_parts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM send_intents").fetchone()[0] == 0


def test_prune_preserva_evidencia_de_retomada(repo) -> None:
    old = time.time() - 40 * 86400
    repo.mark_done(1, 2, 1, "h", dest_msg_id=10)
    repo.register_dedup("h", 2, 10)
    repo.mark_processed(1, 1, "h", 2)
    repo.begin_intent(1, 2, 1, 0, fingerprint="fp")
    repo.confirm_intent(1, 2, 1, 0, 10)
    with repo._db() as conn:
        conn.execute("UPDATE checkpoints SET timestamp=?", (old,))
        conn.execute("UPDATE dedup SET created_at=?", (old,))
        conn.execute("UPDATE processed SET created_at=?", (old,))
        conn.execute("UPDATE send_intents SET created_at=?, updated_at=?", (old, old))
    removed = repo.prune(days=30)
    assert isinstance(removed, dict)
    assert repo.is_done(1, 2, 1)
    assert repo.check_dedup("h", 2) == 10
    assert repo.is_processed(1, 1, 2)
    assert repo.intent_for(1, 2, 1, 0)["state"] == "confirmed"


def test_close_fecha_conexoes_de_outras_threads(repo) -> None:
    captured = {}

    def worker() -> None:
        captured["conn"] = repo._conn()
        repo.is_done(1, 2, 1)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    repo.close()
    with pytest.raises(sqlite3.ProgrammingError):
        captured["conn"].execute("SELECT 1")

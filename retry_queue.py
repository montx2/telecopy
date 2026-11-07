"""
retry_queue.py — Fila persistente de mensagens que falharam.

A fila é armazenada na tabela `failures` do SQLite (já transacional),
garantindo que não exista arquivo JSON paralelo para corromper.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from config import MAX_RETRIES


@dataclass
class Job:
    msg_id: int
    source_id: int
    dest_id: int
    attempts: int = 0
    reason: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    @property
    def exhausted(self) -> bool:
        return self.attempts >= MAX_RETRIES


class RetryQueue:
    """Fachada sobre a tabela de falhas do repositório."""

    def __init__(self, repo=None) -> None:
        if repo is None:
            from database import db as _db

            repo = _db
        self.repo = repo

    # -- escrita ------------------------------------------------------------

    def add(self, msg_id: int, source_id: int, dest_id: int, reason: str = "") -> None:
        self.repo.add_failure(source_id, dest_id, msg_id, reason)

    on_failure = add

    def on_success(self, msg_id: int, source_id: Optional[int] = None,
                   dest_id: Optional[int] = None) -> bool:
        if source_id is None or dest_id is None:
            return False
        self.repo.clear_failure(source_id, dest_id, msg_id)
        return True

    def clear_all(self) -> int:
        return self.repo.clear_failures()

    def clear_exhausted(self) -> int:
        """Remove da fila tudo que já atingiu o limite de tentativas."""
        removed = 0
        for row in self.repo.failures():
            if int(row.get("attempts") or 0) >= MAX_RETRIES:
                self.repo.clear_failure(row["source_id"], row["dest_id"], row["msg_id"])
                removed += 1
        return removed

    # -- leitura ------------------------------------------------------------

    def pending(self, source_id: int, dest_id: int) -> List[Job]:
        return [
            self._job(row)
            for row in self.repo.failures(source_id, dest_id)
            if int(row.get("attempts") or 0) < MAX_RETRIES
        ]

    def all_pending(self) -> List[Job]:
        return [
            self._job(row)
            for row in self.repo.failures()
            if int(row.get("attempts") or 0) < MAX_RETRIES
        ]

    def all_jobs(self) -> List[Job]:
        return [self._job(row) for row in self.repo.failures()]

    @staticmethod
    def _job(row: Dict) -> Job:
        return Job(
            msg_id=int(row["msg_id"]),
            source_id=int(row["source_id"]),
            dest_id=int(row["dest_id"]),
            attempts=int(row.get("attempts") or 0),
            reason=row.get("reason") or "",
            created_at=float(row.get("created_at") or 0),
            updated_at=float(row.get("updated_at") or 0),
        )

    def stats(self) -> Dict[str, int]:
        rows = self.repo.failures()
        exhausted = sum(1 for r in rows if int(r.get("attempts") or 0) >= MAX_RETRIES)
        total = len(rows)
        return {
            "total": total,
            "pending": total - exhausted,
            "exhausted": exhausted,
            "attempts": sum(int(r.get("attempts") or 0) for r in rows),
        }

    # -- compat -------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.repo.failures())

    def __bool__(self) -> bool:
        return len(self) > 0


def get_queue(repo=None) -> RetryQueue:
    return RetryQueue(repo)

"""
retry_queue.py — Persistent retry queue for failed messages.

Failed transfers are written to a JSON file so they survive restarts.
Exponential back-off is applied per job; once MAX_RETRIES is exhausted
the job is marked exhausted and removed from active processing.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from config import QUEUE_FILE, MAX_RETRIES


# ── Job dataclass ─────────────────────────────────────────────────────────────

@dataclass
class Job:
    msg_id:     int
    source_id:  int
    dest_id:    int
    attempts:   int  = 0
    last_error: str  = ""
    created_at: str  = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str  = ""

    @property
    def exhausted(self) -> bool:
        return self.attempts >= MAX_RETRIES


# ── Queue ─────────────────────────────────────────────────────────────────────

class RetryQueue:
    """
    Simple JSON-backed queue.  Thread-safety is not required because
    the queue is always accessed from the main async task.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path: Path = path or QUEUE_FILE
        self._jobs: Dict[str, Job] = {}
        self._load()

    # -- persistence -----------------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            for item in json.loads(self.path.read_text(encoding="utf-8")):
                j = Job(**{k: item[k] for k in item if k in Job.__dataclass_fields__})
                key = self._key(j.source_id, j.dest_id, j.msg_id)
                self._jobs[key] = j
        except Exception:
            self._jobs = {}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps([asdict(j) for j in self._jobs.values()], indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    @staticmethod
    def _key(source_id: int, dest_id: int, msg_id: int) -> str:
        return f"{source_id}:{dest_id}:{msg_id}"

    # -- public API ------------------------------------------------------------

    def add(self, msg_id: int, source_id: int, dest_id: int, error: str = "") -> None:
        key = self._key(source_id, dest_id, msg_id)
        if key not in self._jobs:
            self._jobs[key] = Job(
                msg_id=msg_id,
                source_id=source_id,
                dest_id=dest_id,
                last_error=error[:200],
            )
            self._save()

    def on_failure(self, msg_id: int, error: str = "") -> bool:
        """Increment attempt counter.  Returns True when job is now exhausted."""
        for job in self._jobs.values():
            if job.msg_id == msg_id:
                job.attempts   += 1
                job.last_error  = error[:200]
                job.updated_at  = datetime.now().isoformat()
                self._save()
                return job.exhausted
        return False

    def on_success(self, msg_id: int) -> bool:
        """Remove job on success.  Returns True if the job existed."""
        keys = [k for k, j in self._jobs.items() if j.msg_id == msg_id]
        if not keys:
            return False
        for k in keys:
            del self._jobs[k]
        self._save()
        return True

    def pending(self, source_id: int, dest_id: int) -> List[Job]:
        return [
            j for j in self._jobs.values()
            if j.source_id == source_id
            and j.dest_id == dest_id
            and not j.exhausted
        ]

    def all_pending(self) -> Iterator[Job]:
        return (j for j in self._jobs.values() if not j.exhausted)

    def clear_exhausted(self) -> int:
        before = len(self._jobs)
        self._jobs = {k: j for k, j in self._jobs.items() if not j.exhausted}
        removed = before - len(self._jobs)
        if removed:
            self._save()
        return removed

    def clear_all(self) -> None:
        self._jobs.clear()
        self._save()

    def stats(self) -> Dict[str, int]:
        total     = len(self._jobs)
        exhausted = sum(1 for j in self._jobs.values() if j.exhausted)
        return {
            "total":     total,
            "pending":   total - exhausted,
            "exhausted": exhausted,
        }


# ── Singleton ─────────────────────────────────────────────────────────────────

queue = RetryQueue()

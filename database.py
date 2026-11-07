"""
database.py — SQLite repository with WAL mode and deduplication support.

All writes go through thread-local connections so the same module can be
used safely from both sync code and asyncio tasks running in thread pools.
"""

import sqlite3
import time
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, Set


# ── Enumerations ──────────────────────────────────────────────────────────────

class Status(Enum):
    PENDING    = "pending"
    RUNNING    = "running"
    DONE       = "done"
    ERROR      = "error"
    PAUSED     = "paused"
    DUPLICATE  = "duplicate"


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class Checkpoint:
    id:           int
    source_id:    int
    dest_id:      int
    msg_id:       int
    status:       Status
    attempts:     int
    timestamp:    float
    content_hash: Optional[str] = None
    bytes_done:   int = 0
    media_type:   str = ""


# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
-- Progress checkpoints (enables resume)
CREATE TABLE IF NOT EXISTS checkpoints (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id     INTEGER NOT NULL,
    dest_id       INTEGER NOT NULL,
    msg_id        INTEGER NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'pending',
    attempts      INTEGER NOT NULL DEFAULT 0,
    timestamp     REAL    NOT NULL DEFAULT (unixepoch('now', 'subsec')),
    content_hash  TEXT,
    bytes_done    INTEGER NOT NULL DEFAULT 0,
    media_type    TEXT    NOT NULL DEFAULT '',
    UNIQUE (source_id, dest_id, msg_id)
);

CREATE INDEX IF NOT EXISTS idx_cp_pair   ON checkpoints (source_id, dest_id);
CREATE INDEX IF NOT EXISTS idx_cp_status ON checkpoints (status) WHERE status != 'done';
CREATE INDEX IF NOT EXISTS idx_cp_hash   ON checkpoints (content_hash) WHERE content_hash IS NOT NULL;

-- Content hashes → already-sent message IDs (deduplication)
CREATE TABLE IF NOT EXISTS dedup (
    content_hash  TEXT    NOT NULL,
    dest_id       INTEGER NOT NULL,
    sent_msg_id   INTEGER NOT NULL,
    file_size     INTEGER NOT NULL DEFAULT 0,
    source_id     INTEGER NOT NULL DEFAULT 0,
    created_at    REAL    NOT NULL DEFAULT (unixepoch('now', 'subsec')),
    PRIMARY KEY (content_hash, dest_id)
);

CREATE INDEX IF NOT EXISTS idx_dedup_hash ON dedup (content_hash);
CREATE INDEX IF NOT EXISTS idx_dedup_dest ON dedup (dest_id);

-- Processed message IDs (fast duplicate-by-ID check)
CREATE TABLE IF NOT EXISTS processed (
    source_id   INTEGER NOT NULL,
    msg_id      INTEGER NOT NULL,
    content_hash TEXT,
    created_at  REAL    NOT NULL DEFAULT (unixepoch('now', 'subsec')),
    PRIMARY KEY (source_id, msg_id)
);

CREATE INDEX IF NOT EXISTS idx_proc_source ON processed (source_id);

-- Session metadata
CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    source_name   TEXT,
    dest_name     TEXT,
    state         TEXT NOT NULL DEFAULT 'active',
    last_msg_id   INTEGER,
    total_msgs    INTEGER NOT NULL DEFAULT 0,
    started_at    REAL    NOT NULL DEFAULT (unixepoch('now', 'subsec')),
    updated_at    REAL
);

-- Aggregated statistics per session
CREATE TABLE IF NOT EXISTS stats (
    session_id    TEXT    NOT NULL,
    source_id     INTEGER NOT NULL,
    dest_id       INTEGER NOT NULL,
    success       INTEGER NOT NULL DEFAULT 0,
    errors        INTEGER NOT NULL DEFAULT 0,
    duplicates    INTEGER NOT NULL DEFAULT 0,
    bytes_total   INTEGER NOT NULL DEFAULT 0,
    started_at    REAL,
    ended_at      REAL,
    PRIMARY KEY (session_id)
);

CREATE INDEX IF NOT EXISTS idx_stats_pair ON stats (source_id, dest_id);
"""


# ── Repository ────────────────────────────────────────────────────────────────

class Repository:
    """
    Thread-safe SQLite repository.

    Each thread/coroutine gets its own connection (thread-local storage),
    with WAL mode and optimised PRAGMAs applied on first use.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._lock  = threading.RLock()
        self._init_schema()
        self._setup_maintenance()
    
    def _setup_maintenance(self):
        """Configura backup automático e otimização."""
        with self._db() as c:
            c.execute("PRAGMA wal_autocheckpoint = 1000")
            c.execute("PRAGMA integrity_check")

    # ── Connection management ─────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        if not getattr(self._local, "conn", None):
            c = sqlite3.connect(
                str(self.path),
                check_same_thread=False,
                isolation_level=None,   # autocommit; transactions opened manually
                timeout=30.0,
            )
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode = WAL")
            c.execute("PRAGMA synchronous  = NORMAL")
            c.execute("PRAGMA cache_size   = -8000")     # 8 MB
            c.execute("PRAGMA temp_store   = MEMORY")
            c.execute("PRAGMA mmap_size    = 268435456")  # 256 MB
            self._local.conn = c
        return self._local.conn

    @contextmanager
    def _db(self):
        with self._lock:
            conn = self._conn()
            try:
                yield conn
            except Exception:
                conn.rollback()
                raise

    def _init_schema(self) -> None:
        with self._db() as c:
            c.executescript(_SCHEMA)

    def close(self) -> None:
        if getattr(self._local, "conn", None):
            self._local.conn.close()
            self._local.conn = None

    # ── Checkpoints ───────────────────────────────────────────────────────────

    def upsert_checkpoint(
        self,
        source_id: int,
        dest_id: int,
        msg_id: int,
        status: Status = Status.PENDING,
        content_hash: str = None,
        bytes_done: int = 0,
        media_type: str = "",
    ) -> None:
        with self._db() as c:
            c.execute(
                """
                INSERT INTO checkpoints
                    (source_id, dest_id, msg_id, status, content_hash, bytes_done, media_type)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (source_id, dest_id, msg_id) DO UPDATE SET
                    status       = excluded.status,
                    content_hash = excluded.content_hash,
                    bytes_done   = excluded.bytes_done,
                    media_type   = excluded.media_type,
                    timestamp    = unixepoch('now', 'subsec')
                """,
                (source_id, dest_id, msg_id, status.value, content_hash, bytes_done, media_type),
            )

    def update_status(
        self,
        source_id: int,
        dest_id: int,
        msg_id: int,
        status: Status,
        bytes_done: int = 0,
    ) -> None:
        with self._db() as c:
            c.execute(
                """
                UPDATE checkpoints
                SET status = ?, bytes_done = ?, attempts = attempts + 1,
                    timestamp = unixepoch('now', 'subsec')
                WHERE source_id = ? AND dest_id = ? AND msg_id = ?
                """,
                (status.value, bytes_done, source_id, dest_id, msg_id),
            )

    def get_last_processed_id(self, source_id: int, dest_id: int) -> int:
        """Returns the highest msg_id successfully processed, or 0."""
        with self._db() as c:
            row = c.execute(
                """
                SELECT COALESCE(MAX(msg_id), 0) FROM checkpoints
                WHERE source_id = ? AND dest_id = ? AND status = 'done'
                """,
                (source_id, dest_id),
            ).fetchone()
            return row[0] if row else 0

    # ── Deduplication by content hash ─────────────────────────────────────────

    def register_dedup(
        self,
        content_hash: str,
        dest_id: int,
        sent_msg_id: int,
        file_size: int,
        source_id: int = 0,
    ) -> None:
        with self._db() as c:
            c.execute(
                """
                INSERT OR REPLACE INTO dedup
                    (content_hash, dest_id, sent_msg_id, file_size, source_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (content_hash, dest_id, sent_msg_id, file_size, source_id),
            )

    def check_dedup(self, content_hash: str, dest_id: int) -> Optional[int]:
        """Returns the previously sent msg_id or None."""
        with self._db() as c:
            row = c.execute(
                "SELECT sent_msg_id FROM dedup WHERE content_hash = ? AND dest_id = ?",
                (content_hash, dest_id),
            ).fetchone()
            return row[0] if row else None

    def load_dedup_hashes(self, dest_id: int, limit: int = 10_000) -> list[tuple[str, int]]:
        """Bulk-load recent hashes for Bloom Filter warm-up."""
        with self._db() as c:
            rows = c.execute(
                """
                SELECT content_hash, sent_msg_id FROM dedup
                WHERE dest_id = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (dest_id, limit),
            ).fetchall()
            return [(r[0], r[1]) for r in rows]

    # ── Processed message IDs ─────────────────────────────────────────────────

    def mark_processed(self, source_id: int, msg_id: int, content_hash: str = None) -> bool:
        with self._db() as c:
            try:
                c.execute(
                    "INSERT OR IGNORE INTO processed (source_id, msg_id, content_hash) VALUES (?,?,?)",
                    (source_id, msg_id, content_hash),
                )
                return c.total_changes > 0
            except sqlite3.Error:
                return False

    def is_processed(self, source_id: int, msg_id: int) -> bool:
        with self._db() as c:
            return bool(
                c.execute(
                    "SELECT 1 FROM processed WHERE source_id = ? AND msg_id = ?",
                    (source_id, msg_id),
                ).fetchone()
            )

    def get_processed_ids(self, source_id: int) -> Set[int]:
        with self._db() as c:
            return {
                r[0]
                for r in c.execute(
                    "SELECT msg_id FROM processed WHERE source_id = ?",
                    (source_id,),
                ).fetchall()
            }

    # ── Sessions & statistics ─────────────────────────────────────────────────

    def register_session(
        self, session_id: str, source_id: int, dest_id: int,
        source_name: str = "", dest_name: str = "",
    ) -> None:
        with self._db() as c:
            c.execute(
                """
                INSERT OR REPLACE INTO sessions (id, source_name, dest_name, updated_at)
                VALUES (?, ?, ?, unixepoch('now', 'subsec'))
                """,
                (session_id, source_name, dest_name),
            )
            c.execute(
                """
                INSERT OR IGNORE INTO stats (session_id, source_id, dest_id, started_at)
                VALUES (?, ?, ?, unixepoch('now', 'subsec'))
                """,
                (session_id, source_id, dest_id),
            )

    def update_stats(
        self, session_id: str,
        success: int = 0, errors: int = 0,
        duplicates: int = 0, bytes_total: int = 0,
    ) -> None:
        with self._db() as c:
            c.execute(
                """
                UPDATE stats SET
                    success    = success    + ?,
                    errors     = errors     + ?,
                    duplicates = duplicates + ?,
                    bytes_total= bytes_total+ ?,
                    ended_at   = unixepoch('now', 'subsec')
                WHERE session_id = ?
                """,
                (success, errors, duplicates, bytes_total, session_id),
            )

    def session_summary(self, source_id: int, dest_id: int) -> Dict[str, Any]:
        with self._db() as c:
            row = c.execute(
                """
                SELECT
                    COUNT(*)                                         AS total,
                    SUM(status = 'done')                             AS done,
                    SUM(status = 'error')                            AS errors,
                    SUM(status = 'duplicate')                        AS dupes,
                    COALESCE(SUM(bytes_done), 0)                     AS bytes
                FROM checkpoints
                WHERE source_id = ? AND dest_id = ?
                """,
                (source_id, dest_id),
            ).fetchone()
            return {k: (row[k] or 0) for k in row.keys()} if row else {}

    def global_stats(self) -> Dict[str, Any]:
        with self._db() as c:
            row = c.execute(
                """
                SELECT
                    COUNT(DISTINCT session_id) AS sessions,
                    COALESCE(SUM(success),    0) AS success,
                    COALESCE(SUM(errors),     0) AS errors,
                    COALESCE(SUM(duplicates), 0) AS duplicates,
                    COALESCE(SUM(bytes_total),0) AS bytes
                FROM stats
                """
            ).fetchone()
            return {k: row[k] for k in row.keys()} if row else {}

    # ── Maintenance ───────────────────────────────────────────────────────────

    def prune(self, days: int = 30) -> int:
        """Remove old completed records to keep the DB lean."""
        cutoff = time.time() - days * 86400
        with self._db() as c:
            c.execute("DELETE FROM checkpoints WHERE timestamp < ? AND status = 'done'", (cutoff,))
            removed = c.total_changes
            c.execute("DELETE FROM dedup      WHERE created_at < ?", (cutoff,))
            c.execute("DELETE FROM processed  WHERE created_at < ?", (cutoff,))
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            c.execute("ANALYZE")
        return removed


# ── Global singleton (imported by other modules) ──────────────────────────────

from config import DB_FILE
db = Repository(DB_FILE)

"""
database.py — Repositório SQLite (WAL, thread-safe, sem dependência de
funções de data específicas da versão do SQLite).

Tabelas
    checkpoints  progresso por mensagem (retomada exata)
    dedup        hash de conteúdo já enviado por destino
    processed    ids de mensagens já processadas por origem
    sessions     metadados de cada execução
    stats        contadores agregados por sessão
    kv           preferências / estado interno
"""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from config import DB_FILE


class Status(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    PAUSED = "paused"
    SKIPPED = "skipped"

    def __str__(self) -> str:  # pragma: no cover - conveniência
        return self.value


@dataclass
class Checkpoint:
    source_id: int
    dest_id: int
    msg_id: int
    status: str = Status.PENDING.value
    attempts: int = 0
    timestamp: float = 0.0
    content_hash: Optional[str] = None
    bytes_done: int = 0
    media_type: str = ""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS checkpoints (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id     INTEGER NOT NULL,
    dest_id       INTEGER NOT NULL,
    msg_id        INTEGER NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'pending',
    attempts      INTEGER NOT NULL DEFAULT 0,
    timestamp     REAL    NOT NULL DEFAULT 0,
    content_hash  TEXT,
    bytes_done    INTEGER NOT NULL DEFAULT 0,
    media_type    TEXT    NOT NULL DEFAULT '',
    UNIQUE (source_id, dest_id, msg_id)
);
CREATE INDEX IF NOT EXISTS idx_cp_pair   ON checkpoints (source_id, dest_id);
CREATE INDEX IF NOT EXISTS idx_cp_status ON checkpoints (status);

CREATE TABLE IF NOT EXISTS dedup (
    content_hash  TEXT    NOT NULL,
    dest_id       INTEGER NOT NULL,
    sent_msg_id   INTEGER NOT NULL,
    file_size     INTEGER NOT NULL DEFAULT 0,
    source_id     INTEGER NOT NULL DEFAULT 0,
    created_at    REAL    NOT NULL DEFAULT 0,
    PRIMARY KEY (content_hash, dest_id)
);
CREATE INDEX IF NOT EXISTS idx_dedup_dest ON dedup (dest_id);

CREATE TABLE IF NOT EXISTS processed (
    source_id    INTEGER NOT NULL,
    msg_id       INTEGER NOT NULL,
    dest_id      INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT,
    created_at   REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (source_id, dest_id, msg_id)
);
CREATE INDEX IF NOT EXISTS idx_proc_source ON processed (source_id, dest_id);

CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    source_id     INTEGER NOT NULL DEFAULT 0,
    dest_id       INTEGER NOT NULL DEFAULT 0,
    source_name   TEXT,
    dest_name     TEXT,
    state         TEXT NOT NULL DEFAULT 'active',
    last_msg_id   INTEGER,
    total_msgs    INTEGER NOT NULL DEFAULT 0,
    started_at    REAL NOT NULL DEFAULT 0,
    updated_at    REAL
);

CREATE TABLE IF NOT EXISTS stats (
    session_id    TEXT PRIMARY KEY,
    source_id     INTEGER NOT NULL DEFAULT 0,
    dest_id       INTEGER NOT NULL DEFAULT 0,
    success       INTEGER NOT NULL DEFAULT 0,
    errors        INTEGER NOT NULL DEFAULT 0,
    duplicates    INTEGER NOT NULL DEFAULT 0,
    bytes_total   INTEGER NOT NULL DEFAULT 0,
    started_at    REAL,
    ended_at      REAL
);
CREATE INDEX IF NOT EXISTS idx_stats_pair ON stats (source_id, dest_id);

CREATE TABLE IF NOT EXISTS failures (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id   INTEGER NOT NULL,
    dest_id     INTEGER NOT NULL,
    msg_id      INTEGER NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 1,
    reason      TEXT,
    created_at  REAL NOT NULL DEFAULT 0,
    updated_at  REAL NOT NULL DEFAULT 0,
    UNIQUE (source_id, dest_id, msg_id)
);
CREATE INDEX IF NOT EXISTS idx_fail_pair ON failures (source_id, dest_id);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class Repository:
    """
    SQLite thread-safe.  Cada thread recebe sua própria conexão
    (thread-local) e todas as transações passam por um RLock.
    """

    def __init__(self, path: Path | str = DB_FILE) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._lock = threading.RLock()
        self._init_schema()

    # ── conexão ───────────────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            return conn
        conn = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
            isolation_level=None,
            timeout=30.0,
        )
        conn.row_factory = sqlite3.Row
        for pragma in (
            "PRAGMA journal_mode = WAL",
            "PRAGMA synchronous  = NORMAL",
            "PRAGMA cache_size   = -16000",
            "PRAGMA temp_store   = MEMORY",
            "PRAGMA busy_timeout = 30000",
            "PRAGMA foreign_keys = ON",
            "PRAGMA wal_autocheckpoint = 1000",
        ):
            try:
                conn.execute(pragma)
            except sqlite3.Error:
                pass
        self._local.conn = conn
        return conn

    @contextmanager
    def _db(self):
        with self._lock:
            conn = self._conn()
            try:
                yield conn
            except sqlite3.Error:
                raise

    def _init_schema(self) -> None:
        with self._db() as conn:
            conn.executescript(_SCHEMA)

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            try:
                conn.close()
            except sqlite3.Error:
                pass
            self._local.conn = None

    def size_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(self.path) + suffix)
            if candidate.exists():
                try:
                    total += candidate.stat().st_size
                except OSError:
                    pass
        return total

    def health(self) -> Dict[str, Any]:
        with self._db() as conn:
            try:
                ok = conn.execute("PRAGMA quick_check").fetchone()[0]
            except sqlite3.Error as exc:  # pragma: no cover
                ok = f"erro: {exc}"
            counts = {}
            for table in ("checkpoints", "dedup", "processed", "sessions", "failures"):
                try:
                    counts[table] = conn.execute(
                        f"SELECT COUNT(*) FROM {table}"  # noqa: S608 - nomes fixos
                    ).fetchone()[0]
                except sqlite3.Error:
                    counts[table] = -1
        return {"integrity": ok, "counts": counts, "size": self.size_bytes()}

    # ── checkpoints ───────────────────────────────────────────────────────────

    def upsert_checkpoint(
        self,
        source_id: int,
        dest_id: int,
        msg_id: int,
        status: Status | str = Status.PENDING,
        content_hash: Optional[str] = None,
        bytes_done: int = 0,
        media_type: str = "",
    ) -> None:
        status_value = status.value if isinstance(status, Status) else str(status)
        now = time.time()
        with self._db() as conn:
            conn.execute(
                """
                INSERT INTO checkpoints
                    (source_id, dest_id, msg_id, status, content_hash,
                     bytes_done, media_type, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (source_id, dest_id, msg_id) DO UPDATE SET
                    status       = excluded.status,
                    content_hash = COALESCE(excluded.content_hash, checkpoints.content_hash),
                    bytes_done   = excluded.bytes_done,
                    media_type   = excluded.media_type,
                    timestamp    = excluded.timestamp
                """,
                (source_id, dest_id, msg_id, status_value, content_hash,
                 bytes_done, media_type, now),
            )

    def update_status(
        self,
        source_id: int,
        dest_id: int,
        msg_id: int,
        status: Status | str,
        bytes_done: int = 0,
        attempts_inc: bool = True,
    ) -> None:
        status_value = status.value if isinstance(status, Status) else str(status)
        with self._db() as conn:
            conn.execute(
                """
                UPDATE checkpoints
                   SET status = ?, bytes_done = ?,
                       attempts = attempts + ?, timestamp = ?
                 WHERE source_id = ? AND dest_id = ? AND msg_id = ?
                """,
                (status_value, bytes_done, 1 if attempts_inc else 0,
                 time.time(), source_id, dest_id, msg_id),
            )

    def mark_done(
        self,
        source_id: int,
        dest_id: int,
        msg_id: int,
        content_hash: Optional[str] = None,
        bytes_done: int = 0,
        media_type: str = "",
    ) -> None:
        self.upsert_checkpoint(
            source_id, dest_id, msg_id, Status.DONE, content_hash, bytes_done, media_type
        )
        with self._db() as conn:
            conn.execute(
                "UPDATE checkpoints SET attempts = attempts + 1 WHERE source_id=? AND dest_id=? AND msg_id=?",
                (source_id, dest_id, msg_id),
            )

    def is_done(self, source_id: int, dest_id: int, msg_id: int) -> bool:
        with self._db() as conn:
            row = conn.execute(
                "SELECT 1 FROM checkpoints WHERE source_id=? AND dest_id=? AND msg_id=? AND status='done'",
                (source_id, dest_id, msg_id),
            ).fetchone()
            return row is not None

    def done_ids(self, source_id: int, dest_id: int) -> Set[int]:
        with self._db() as conn:
            return {
                row[0]
                for row in conn.execute(
                    "SELECT msg_id FROM checkpoints WHERE source_id=? AND dest_id=? AND status='done'",
                    (source_id, dest_id),
                )
            }

    def last_processed_id(self, source_id: int, dest_id: int) -> int:
        with self._db() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(msg_id), 0) FROM checkpoints "
                "WHERE source_id=? AND dest_id=? AND status='done'",
                (source_id, dest_id),
            ).fetchone()
            return int(row[0] or 0)

    def pair_summary(self, source_id: int, dest_id: int) -> Dict[str, int]:
        with self._db() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*)                                            AS total,
                       COALESCE(SUM(status = 'done'),  0)                  AS done,
                       COALESCE(SUM(status = 'error'), 0)                  AS errors,
                       COALESCE(SUM(status = 'skipped'), 0)                AS skipped,
                       COALESCE(SUM(bytes_done), 0)                        AS bytes
                  FROM checkpoints
                 WHERE source_id = ? AND dest_id = ?
                """,
                (source_id, dest_id),
            ).fetchone()
        return {k: int(row[k] or 0) for k in row.keys()}

    # ── deduplicação por hash ─────────────────────────────────────────────────

    def register_dedup(
        self,
        content_hash: str,
        dest_id: int,
        sent_msg_id: int,
        file_size: int = 0,
        source_id: int = 0,
    ) -> None:
        with self._db() as conn:
            conn.execute(
                """
                INSERT INTO dedup
                    (content_hash, dest_id, sent_msg_id, file_size, source_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (content_hash, dest_id) DO UPDATE SET
                    sent_msg_id = excluded.sent_msg_id,
                    file_size   = excluded.file_size
                """,
                (content_hash, dest_id, sent_msg_id, file_size, source_id, time.time()),
            )

    def check_dedup(self, content_hash: str, dest_id: int) -> Optional[int]:
        with self._db() as conn:
            row = conn.execute(
                "SELECT sent_msg_id FROM dedup WHERE content_hash=? AND dest_id=?",
                (content_hash, dest_id),
            ).fetchone()
            return int(row[0]) if row else None

    def load_dedup_hashes(self, dest_id: int, limit: int = 200_000) -> List[Tuple[str, int]]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT content_hash, sent_msg_id FROM dedup "
                "WHERE dest_id=? ORDER BY created_at DESC LIMIT ?",
                (dest_id, limit),
            ).fetchall()
        return [(r[0], int(r[1])) for r in rows]

    def dedup_count(self, dest_id: Optional[int] = None) -> int:
        with self._db() as conn:
            if dest_id is None:
                row = conn.execute("SELECT COUNT(*) FROM dedup").fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM dedup WHERE dest_id=?", (dest_id,)
                ).fetchone()
            return int(row[0] or 0)

    # ── mensagens processadas ─────────────────────────────────────────────────

    def mark_processed(
        self,
        source_id: int,
        msg_id: int,
        content_hash: Optional[str] = None,
        dest_id: int = 0,
    ) -> bool:
        with self._db() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO processed (source_id, dest_id, msg_id, content_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (source_id, dest_id, msg_id, content_hash, time.time()),
            )
            return cur.rowcount > 0

    def is_processed(self, source_id: int, msg_id: int, dest_id: int = 0) -> bool:
        with self._db() as conn:
            row = conn.execute(
                "SELECT 1 FROM processed WHERE source_id=? AND dest_id=? AND msg_id=?",
                (source_id, dest_id, msg_id),
            ).fetchone()
            return row is not None

    def processed_ids(self, source_id: int, dest_id: int = 0) -> Set[int]:
        with self._db() as conn:
            return {
                int(row[0])
                for row in conn.execute(
                    "SELECT msg_id FROM processed WHERE source_id=? AND dest_id=?",
                    (source_id, dest_id),
                )
            }

    # compat com versões anteriores
    get_processed_ids = processed_ids

    # ── sessões / estatísticas ────────────────────────────────────────────────

    def register_session(
        self,
        session_id: str,
        source_id: int,
        dest_id: int,
        source_name: str = "",
        dest_name: str = "",
    ) -> None:
        now = time.time()
        with self._db() as conn:
            conn.execute(
                """
                INSERT INTO sessions
                    (id, source_id, dest_id, source_name, dest_name, state, started_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
                ON CONFLICT (id) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    state      = 'active'
                """,
                (session_id, source_id, dest_id, source_name, dest_name, now, now),
            )
            conn.execute(
                "INSERT OR IGNORE INTO stats (session_id, source_id, dest_id, started_at) "
                "VALUES (?, ?, ?, ?)",
                (session_id, source_id, dest_id, now),
            )

    def finish_session(self, session_id: str, state: str = "finished") -> None:
        with self._db() as conn:
            conn.execute(
                "UPDATE sessions SET state=?, updated_at=? WHERE id=?",
                (state, time.time(), session_id),
            )

    def update_stats(
        self,
        session_id: str,
        success: int = 0,
        errors: int = 0,
        duplicates: int = 0,
        bytes_total: int = 0,
    ) -> None:
        with self._db() as conn:
            conn.execute(
                """
                UPDATE stats SET
                    success     = success     + ?,
                    errors      = errors      + ?,
                    duplicates  = duplicates  + ?,
                    bytes_total = bytes_total + ?,
                    ended_at    = ?
                 WHERE session_id = ?
                """,
                (success, errors, duplicates, bytes_total, time.time(), session_id),
            )

    def global_stats(self) -> Dict[str, int]:
        with self._db() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*)                              AS sessions,
                       COALESCE(SUM(success),     0)         AS success,
                       COALESCE(SUM(errors),      0)         AS errors,
                       COALESCE(SUM(duplicates),  0)         AS duplicates,
                       COALESCE(SUM(bytes_total), 0)         AS bytes
                  FROM stats
                """
            ).fetchone()
        return {k: int(row[k] or 0) for k in row.keys()}

    def recent_sessions(self, limit: int = 8) -> List[Dict[str, Any]]:
        with self._db() as conn:
            rows = conn.execute(
                """
                SELECT s.id, s.source_name, s.dest_name, s.state, s.started_at,
                       st.success, st.errors, st.duplicates, st.bytes_total
                  FROM sessions s
                  LEFT JOIN stats st ON st.session_id = s.id
                 ORDER BY s.started_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── falhas (fila de repetição) ────────────────────────────────────────────

    def add_failure(
        self, source_id: int, dest_id: int, msg_id: int, reason: str = ""
    ) -> None:
        now = time.time()
        with self._db() as conn:
            conn.execute(
                """
                INSERT INTO failures (source_id, dest_id, msg_id, attempts, reason, created_at, updated_at)
                VALUES (?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT (source_id, dest_id, msg_id) DO UPDATE SET
                    attempts   = failures.attempts + 1,
                    reason     = excluded.reason,
                    updated_at = excluded.updated_at
                """,
                (source_id, dest_id, msg_id, str(reason)[:300], now, now),
            )

    def clear_failure(self, source_id: int, dest_id: int, msg_id: int) -> None:
        with self._db() as conn:
            conn.execute(
                "DELETE FROM failures WHERE source_id=? AND dest_id=? AND msg_id=?",
                (source_id, dest_id, msg_id),
            )

    def failures(
        self, source_id: Optional[int] = None, dest_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM failures"
        params: List[Any] = []
        if source_id is not None and dest_id is not None:
            query += " WHERE source_id=? AND dest_id=?"
            params = [source_id, dest_id]
        query += " ORDER BY updated_at DESC"
        with self._db() as conn:
            return [dict(r) for r in conn.execute(query, params).fetchall()]

    def failure_stats(self) -> Dict[str, int]:
        with self._db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total, COALESCE(SUM(attempts), 0) AS attempts FROM failures"
            ).fetchone()
        return {"total": int(row["total"] or 0), "attempts": int(row["attempts"] or 0)}

    def clear_failures(self) -> int:
        with self._db() as conn:
            cur = conn.execute("DELETE FROM failures")
            return cur.rowcount or 0

    # ── chave/valor ───────────────────────────────────────────────────────────

    def set_kv(self, key: str, value: str) -> None:
        with self._db() as conn:
            conn.execute(
                "INSERT INTO kv (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )

    def get_kv(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._db() as conn:
            row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    # ── manutenção ────────────────────────────────────────────────────────────

    def prune(self, days: int = 30) -> Dict[str, int]:
        """Remove registros concluídos mais antigos que `days` dias."""
        cutoff = time.time() - days * 86400
        removed = {"checkpoints": 0, "dedup": 0, "processed": 0, "failures": 0}
        with self._db() as conn:
            for table in removed:
                column = "created_at" if table in ("dedup", "processed", "failures") else "timestamp"
                extra = " AND status='done'" if table == "checkpoints" else ""
                cur = conn.execute(
                    f"DELETE FROM {table} WHERE {column} < ?{extra}",  # noqa: S608
                    (cutoff,),
                )
                removed[table] = cur.rowcount or 0
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
        return removed

    def vacuum(self) -> None:
        with self._db() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        # VACUUM precisa de conexão dedicada (não dentro de transação)
        conn = sqlite3.connect(str(self.path), timeout=30)
        try:
            conn.execute("VACUUM")
            conn.execute("ANALYZE")
        finally:
            conn.close()

    def reset_all(self) -> None:
        """Apaga o histórico de cópias (mantém apenas as preferências)."""
        with self._db() as conn:
            for table in ("checkpoints", "dedup", "processed", "failures"):
                conn.execute(f"DELETE FROM {table}")  # noqa: S608
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


from config import DB_FILE as _DB_FILE  # noqa: E402  (evita ciclo)

db = Repository(_DB_FILE)

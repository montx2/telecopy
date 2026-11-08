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
    placeholders posições reservadas para conteúdo ainda não entregue
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


PLACEHOLDER_STATES = frozenset({"open", "recovered", "unrecoverable"})


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
    dest_msg_id   INTEGER,
    dest_topic    INTEGER,
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

CREATE TABLE IF NOT EXISTS placeholders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id    INTEGER NOT NULL,
    dest_id      INTEGER NOT NULL,
    msg_id       INTEGER NOT NULL,
    dest_msg_id  INTEGER NOT NULL,
    dest_topic   INTEGER,
    caption      TEXT,
    entities     TEXT,
    reason       TEXT,
    state        TEXT NOT NULL DEFAULT 'open',
    created_at   REAL NOT NULL DEFAULT 0,
    updated_at   REAL NOT NULL DEFAULT 0,
    UNIQUE (source_id, dest_id, msg_id)
);
CREATE INDEX IF NOT EXISTS idx_ph_pair ON placeholders (source_id, dest_id, state);

CREATE TABLE IF NOT EXISTS placeholder_parts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id    INTEGER NOT NULL,
    dest_id      INTEGER NOT NULL,
    msg_id       INTEGER NOT NULL,
    part_index   INTEGER NOT NULL,
    dest_msg_id  INTEGER NOT NULL,
    dest_topic   INTEGER,
    created_at   REAL NOT NULL DEFAULT 0,
    UNIQUE (source_id, dest_id, msg_id, part_index)
);
CREATE INDEX IF NOT EXISTS idx_ph_parts_msg ON placeholder_parts (source_id, dest_id, msg_id);
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
            # Bancos da v4.0 não tinham o vínculo origem→destino necessário
            # para reconstruir respostas e retomar retries no tópico certo.
            columns = {row[1] for row in conn.execute("PRAGMA table_info(checkpoints)")}
            for column in ("dest_msg_id", "dest_topic"):
                if column not in columns:
                    conn.execute(f"ALTER TABLE checkpoints ADD COLUMN {column} INTEGER")

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
            for table in ("checkpoints", "dedup", "processed", "sessions", "failures", "placeholders"):
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
        dest_msg_id: Optional[int] = None,
        dest_topic: Optional[int] = None,
    ) -> None:
        status_value = status.value if isinstance(status, Status) else str(status)
        now = time.time()
        with self._db() as conn:
            conn.execute(
                """
                INSERT INTO checkpoints
                    (source_id, dest_id, msg_id, status, content_hash,
                     bytes_done, media_type, timestamp, dest_msg_id, dest_topic)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (source_id, dest_id, msg_id) DO UPDATE SET
                    status       = excluded.status,
                    content_hash = COALESCE(excluded.content_hash, checkpoints.content_hash),
                    bytes_done   = excluded.bytes_done,
                    media_type   = excluded.media_type,
                    timestamp    = excluded.timestamp,
                    dest_msg_id  = COALESCE(excluded.dest_msg_id, checkpoints.dest_msg_id),
                    dest_topic   = COALESCE(excluded.dest_topic, checkpoints.dest_topic)
                """,
                (source_id, dest_id, msg_id, status_value, content_hash,
                 bytes_done, media_type, now, dest_msg_id, dest_topic),
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
        dest_msg_id: Optional[int] = None,
        dest_topic: Optional[int] = None,
    ) -> None:
        self.upsert_checkpoint(
            source_id, dest_id, msg_id, Status.DONE, content_hash, bytes_done, media_type,
            dest_msg_id=dest_msg_id, dest_topic=dest_topic,
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

    def completed_ids(self, source_id: int, dest_id: int) -> Set[int]:
        """IDs concluídos ou explicitamente ignorados por deduplicação."""
        with self._db() as conn:
            return {
                int(row[0]) for row in conn.execute(
                    "SELECT msg_id FROM checkpoints "
                    "WHERE source_id=? AND dest_id=? AND status IN ('done', 'skipped')",
                    (source_id, dest_id),
                )
            }

    def checkpoint_route(self, source_id: int, dest_id: int, msg_id: int) -> Dict[str, Any]:
        """Vínculo persistido para replies/retries; vazio em bancos antigos."""
        with self._db() as conn:
            row = conn.execute(
                "SELECT status, dest_msg_id, dest_topic FROM checkpoints "
                "WHERE source_id=? AND dest_id=? AND msg_id=?",
                (source_id, dest_id, msg_id),
            ).fetchone()
        return dict(row) if row else {}

    def last_processed_id(self, source_id: int, dest_id: int) -> int:
        with self._db() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(msg_id), 0) FROM checkpoints "
                "WHERE source_id=? AND dest_id=? AND status='done'",
                (source_id, dest_id),
            ).fetchone()
            return int(row[0] or 0)

    def untopiced_checkpoints(self, source_id: int, dest_id: int) -> int:
        """Concluídos sem tópico de destino: cópia antiga que foi ao General."""
        with self._db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM checkpoints "
                "WHERE source_id=? AND dest_id=? AND status IN ('done', 'skipped') "
                "AND dest_topic IS NULL",
                (source_id, dest_id),
            ).fetchone()
        return int(row[0] or 0)

    def reset_pair(self, source_id: int, dest_id: int) -> int:
        """Zera checkpoints/falhas/marcas apenas deste par origem→destino."""
        removed = 0
        with self._db() as conn:
            for table in ("checkpoints", "failures", "processed", "placeholders"):
                cur = conn.execute(
                    f"DELETE FROM {table} WHERE source_id=? AND dest_id=?",
                    (source_id, dest_id),
                )
                removed += cur.rowcount or 0
        return removed

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

    # ── reservas de posição ───────────────────────────────────────────────────

    def add_placeholder(
        self,
        source_id: int,
        dest_id: int,
        msg_id: int,
        dest_msg_id: int,
        dest_topic: Optional[int] = None,
        caption: Optional[str] = None,
        entities: Optional[str] = None,
        reason: str = "",
        state: str = "open",
    ) -> int:
        """Vincula uma posição reservada; não sobrescreve vínculo existente."""
        if state not in PLACEHOLDER_STATES:
            raise ValueError(f"invalid placeholder state: {state}")
        now = time.time()
        with self._db() as conn:
            conn.execute(
                """
                INSERT INTO placeholders
                    (source_id, dest_id, msg_id, dest_msg_id, dest_topic,
                     caption, entities, reason, state, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (source_id, dest_id, msg_id) DO NOTHING
                """,
                (source_id, dest_id, msg_id, dest_msg_id, dest_topic,
                 caption, entities, str(reason)[:300], state, now, now),
            )
            row = conn.execute(
                "SELECT id FROM placeholders WHERE source_id=? AND dest_id=? AND msg_id=?",
                (source_id, dest_id, msg_id),
            ).fetchone()
        return int(row["id"])

    def get_placeholder(
        self, source_id: int, dest_id: int, msg_id: int
    ) -> Optional[Dict[str, Any]]:
        with self._db() as conn:
            row = conn.execute(
                "SELECT * FROM placeholders WHERE source_id=? AND dest_id=? AND msg_id=?",
                (source_id, dest_id, msg_id),
            ).fetchone()
        return dict(row) if row else None

    def open_placeholders(
        self, source_id: Optional[int] = None, dest_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM placeholders WHERE state='open'"
        params: List[Any] = []
        if source_id is not None:
            query += " AND source_id=?"
            params.append(source_id)
        if dest_id is not None:
            query += " AND dest_id=?"
            params.append(dest_id)
        query += " ORDER BY source_id, dest_id, msg_id"
        with self._db() as conn:
            return [dict(r) for r in conn.execute(query, params).fetchall()]

    def set_placeholder_state(
        self, source_id: int, dest_id: int, msg_id: int,
        state: str, reason: Optional[str] = None,
    ) -> bool:
        if state not in PLACEHOLDER_STATES:
            raise ValueError(f"invalid placeholder state: {state}")
        with self._db() as conn:
            cur = conn.execute(
                """
                UPDATE placeholders
                   SET state=?, reason=COALESCE(?, reason), updated_at=?
                 WHERE source_id=? AND dest_id=? AND msg_id=?
                """,
                (state, str(reason)[:300] if reason is not None else None,
                 time.time(), source_id, dest_id, msg_id),
            )
        return (cur.rowcount or 0) > 0

    def placeholder_stats(
        self, source_id: Optional[int] = None, dest_id: Optional[int] = None
    ) -> Dict[str, int]:
        query = """
            SELECT COALESCE(SUM(state='open'), 0)          AS open,
                   COALESCE(SUM(state='recovered'), 0)     AS recovered,
                   COALESCE(SUM(state='unrecoverable'), 0) AS unrecoverable
              FROM placeholders
        """
        params: List[Any] = []
        conditions = []
        if source_id is not None:
            conditions.append("source_id=?")
            params.append(source_id)
        if dest_id is not None:
            conditions.append("dest_id=?")
            params.append(dest_id)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        with self._db() as conn:
            row = conn.execute(query, params).fetchone()
        return {key: int(row[key] or 0) for key in ("open", "recovered", "unrecoverable")}

    def placeholder_pairs(self) -> List[Dict[str, Any]]:
        """Resumo por par, incluindo pares que já não têm reservas abertas."""
        with self._db() as conn:
            rows = conn.execute(
                """
                SELECT source_id, dest_id,
                       COALESCE(SUM(state='open'), 0)          AS open,
                       COALESCE(SUM(state='recovered'), 0)     AS recovered,
                       COALESCE(SUM(state='unrecoverable'), 0) AS unrecoverable
                  FROM placeholders
                 GROUP BY source_id, dest_id
                 ORDER BY source_id, dest_id
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def add_placeholder_part(
        self, source_id: int, dest_id: int, msg_id: int, part_index: int,
        dest_msg_id: int, dest_topic: Optional[int] = None,
    ) -> int:
        now = time.time()
        with self._db() as conn:
            conn.execute(
                """
                INSERT INTO placeholder_parts
                    (source_id, dest_id, msg_id, part_index, dest_msg_id, dest_topic, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (source_id, dest_id, msg_id, part_index) DO NOTHING
                """,
                (source_id, dest_id, msg_id, int(part_index), int(dest_msg_id), dest_topic, now),
            )
            row = conn.execute(
                """
                SELECT id FROM placeholder_parts
                 WHERE source_id=? AND dest_id=? AND msg_id=? AND part_index=?
                """,
                (source_id, dest_id, msg_id, int(part_index)),
            ).fetchone()
        return int(row["id"])

    def placeholder_parts(
        self, source_id: int, dest_id: int, msg_id: int
    ) -> List[Dict[str, Any]]:
        with self._db() as conn:
            rows = conn.execute(
                """
                SELECT * FROM placeholder_parts
                 WHERE source_id=? AND dest_id=? AND msg_id=?
                 ORDER BY part_index
                """,
                (source_id, dest_id, msg_id),
            ).fetchall()
        return [dict(r) for r in rows]

    def placeholder_msg_ids(self, source_id: int, dest_id: int) -> Set[int]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT msg_id FROM placeholders WHERE source_id=? AND dest_id=?",
                (source_id, dest_id),
            ).fetchall()
        return {int(row["msg_id"]) for row in rows}

    def order_pending_before(
        self, source_id: int, dest_id: int, dest_topic: Optional[int], msg_id: int
    ) -> bool:
        with self._db() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM checkpoints c
                  LEFT JOIN placeholders p
                    ON p.source_id=c.source_id AND p.dest_id=c.dest_id AND p.msg_id=c.msg_id
                 WHERE c.source_id=? AND c.dest_id=? AND c.msg_id<?
                   AND (c.dest_topic=? OR (c.dest_topic IS NULL AND ? IS NULL))
                   AND NOT (c.status IN ('done','skipped') OR p.dest_msg_id IS NOT NULL)
                 LIMIT 1
                """,
                (source_id, dest_id, int(msg_id), dest_topic, dest_topic),
            ).fetchone()
        return row is not None

    def order_terminal_after(
        self, source_id: int, dest_id: int, dest_topic: Optional[int], msg_id: int
    ) -> bool:
        with self._db() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM checkpoints c
                  LEFT JOIN placeholders p
                    ON p.source_id=c.source_id AND p.dest_id=c.dest_id AND p.msg_id=c.msg_id
                 WHERE c.source_id=? AND c.dest_id=? AND c.msg_id>?
                   AND (c.dest_topic=? OR (c.dest_topic IS NULL AND ? IS NULL))
                   AND (c.status IN ('done','skipped') OR p.dest_msg_id IS NOT NULL)
                 LIMIT 1
                """,
                (source_id, dest_id, int(msg_id), dest_topic, dest_topic),
            ).fetchone()
        return row is not None

    def order_pairs(self) -> List[Dict[str, int]]:
        with self._db() as conn:
            rows = conn.execute(
                """
                SELECT source_id, dest_id FROM checkpoints
                UNION
                SELECT source_id, dest_id FROM placeholders
                 ORDER BY source_id, dest_id
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def order_rows(self, source_id: int, dest_id: int) -> List[Dict[str, Any]]:
        with self._db() as conn:
            rows = conn.execute(
                """
                SELECT msg_id, dest_msg_id, dest_topic, 0 AS part_index, 'content' AS kind,
                       (dest_msg_id IS NULL) AS pending_pos
                  FROM checkpoints
                 WHERE source_id=? AND dest_id=?
                   AND (status='done' OR (status='error' AND dest_msg_id IS NULL))
                UNION ALL
                SELECT msg_id, dest_msg_id, dest_topic, 0 AS part_index, 'marker' AS kind,
                       (dest_msg_id IS NULL) AS pending_pos
                  FROM placeholders WHERE source_id=? AND dest_id=?
                UNION ALL
                SELECT msg_id, dest_msg_id, dest_topic, part_index, 'continuation' AS kind,
                       (dest_msg_id IS NULL) AS pending_pos
                  FROM placeholder_parts WHERE source_id=? AND dest_id=?
                 ORDER BY dest_topic, pending_pos, dest_msg_id, part_index
                """,
                (source_id, dest_id, source_id, dest_id, source_id, dest_id),
            ).fetchall()
        return [dict(r) for r in rows]

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

    def delete_kv(self, key: str) -> bool:
        with self._db() as conn:
            cur = conn.execute("DELETE FROM kv WHERE key=?", (key,))
            return (cur.rowcount or 0) > 0

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
            for table in ("checkpoints", "dedup", "processed", "failures", "placeholders",
                          "placeholder_parts"):
                conn.execute(f"DELETE FROM {table}")  # noqa: S608
            # Mapas de tópicos são estado de cópia; sem isso, "recomeçar do
            # zero" ainda reutilizaria associações antigas.
            conn.execute("DELETE FROM kv WHERE key LIKE 'forum_topic_map:%'")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


from config import DB_FILE as _DB_FILE  # noqa: E402  (evita ciclo)

db = Repository(_DB_FILE)

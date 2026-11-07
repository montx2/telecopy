"""
utils.py — Logging com rotação, estatísticas persistentes e formatadores.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from config import (
    DAILY_FILE,
    DAILY_LIMIT,
    DAILY_WARN,
    LOG_FILE,
    STATS_FILE,
    init_dirs,
)

init_dirs()

_MAX_LOG_BYTES = 2 * 1024 * 1024  # 2 MB por arquivo, mantém 3 backups


# ── Escrita atômica de JSON ───────────────────────────────────────────────────


def write_json(path: Path, data: Any) -> bool:
    """Grava JSON de forma atômica (nunca deixa arquivo corrompido)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


# ── Logger ────────────────────────────────────────────────────────────────────


class Logger:
    """
    Log em arquivo com rotação simples.  Valores sensíveis (telefone,
    hashes, tokens, sessão) são mascarados automaticamente.
    """

    _REDACT = [
        (re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"), "[TOKEN]"),
        (re.compile(r"\+\d{10,15}\b"), "[PHONE]"),
        (re.compile(r"\b[a-f0-9]{40,}\b", re.I), "[HASH]"),
    ]

    def __init__(self, path: Optional[Path] = None, level: str = "INFO") -> None:
        self.path = path or LOG_FILE
        self.level = level.upper()
        self._lock = threading.RLock()
        self._levels = {"DEBUG": 10, "INFO": 20, "SUCCESS": 20,
                        "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.touch(exist_ok=True)
        except OSError:
            pass

    def _redact(self, text: str) -> str:
        for pattern, replacement in self._REDACT:
            text = pattern.sub(replacement, text)
        return text

    def _rotate_if_needed(self) -> None:
        try:
            if self.path.stat().st_size < _MAX_LOG_BYTES:
                return
            for i in range(2, 0, -1):
                old = self.path.with_suffix(f".{i}.log")
                if old.exists():
                    os.replace(old, self.path.with_suffix(f".{i + 1}.log"))
            os.replace(self.path, self.path.with_suffix(".1.log"))
            self.path.touch()
        except OSError:
            pass

    def _write(self, level: str, msg: str) -> None:
        if self._levels.get(level, 20) < self._levels.get(self.level, 20):
            return
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] [{level:8}] {self._redact(str(msg))}\n"
        with self._lock:
            try:
                self._rotate_if_needed()
                with self.path.open("a", encoding="utf-8", errors="replace") as fh:
                    fh.write(line)
            except OSError:
                pass

    def debug(self, msg: str) -> None:
        self._write("DEBUG", msg)

    def info(self, msg: str) -> None:
        self._write("INFO", msg)

    def success(self, msg: str) -> None:
        self._write("SUCCESS", msg)

    def warning(self, msg: str) -> None:
        self._write("WARNING", msg)

    def error(self, msg: str) -> None:
        self._write("ERROR", msg)

    def critical(self, msg: str) -> None:
        self._write("CRITICAL", msg)

    def exception(self, msg: str) -> None:
        """Registra a mensagem junto com o traceback atual."""
        import traceback

        frame = traceback.format_exc()
        detail = "" if frame.strip() == "NoneType: None" else f"\n{frame.rstrip()}"
        self._write("ERROR", f"{msg}{detail}")

    # compat
    warn = warning


logger = Logger()


# ── Limite diário ─────────────────────────────────────────────────────────────


class DailyLimit:
    """Conta as mensagens enviadas no dia para evitar bloqueio do Telegram."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or DAILY_FILE
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = {"date": "", "count": 0}
        self._load()

    @staticmethod
    def _today() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _load(self) -> None:
        data = read_json(self.path, {}) or {}
        date = data.get("date") or ""
        count = int(data.get("count") or 0)
        if date != self._today():
            date, count = self._today(), 0
        self._data = {"date": date, "count": count}
        self._save()

    def _save(self) -> None:
        write_json(self.path, self._data)

    def ensure_today(self) -> None:
        with self._lock:
            if self._data.get("date") != self._today():
                self._data = {"date": self._today(), "count": 0}
                self._save()

    def add(self, n: int = 1) -> None:
        with self._lock:
            self.ensure_today()
            self._data["count"] = int(self._data.get("count", 0)) + n
            self._save()

    @property
    def count(self) -> int:
        self.ensure_today()
        return int(self._data.get("count", 0))

    def reset(self) -> None:
        with self._lock:
            self._data = {"date": self._today(), "count": 0}
            self._save()

    def status(self) -> Tuple[str, int, int]:
        """(nível, enviadas, restantes)."""
        n = self.count
        remaining = max(0, DAILY_LIMIT - n)
        if n >= DAILY_LIMIT:
            return "limit", n, 0
        if n >= DAILY_WARN:
            return "warn", n, remaining
        return "ok", n, remaining


daily_limit = DailyLimit()


# ── Estatísticas ──────────────────────────────────────────────────────────────


class Stats:
    """Estatísticas acumuladas entre execuções."""

    _DEFAULTS: Dict[str, Any] = {
        "total": 0,
        "photos": 0,
        "videos": 0,
        "audio": 0,
        "documents": 0,
        "text": 0,
        "bytes_sent": 0,
        "bytes_recv": 0,
        "elapsed": 0.0,
        "sessions": 0,
        "errors": 0,
        "skipped": 0,
        "last_run": None,
    }

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or STATS_FILE
        self._lock = threading.RLock()
        self.data: Dict[str, Any] = dict(self._DEFAULTS)
        self._load()

    def _load(self) -> None:
        saved = read_json(self.path, {})
        if isinstance(saved, dict):
            self.data = {**self._DEFAULTS, **saved}

    def save(self) -> None:
        with self._lock:
            write_json(self.path, self.data)

    def inc(self, kind: str, n: int = 1) -> None:
        with self._lock:
            if kind in self.data:
                self.data[kind] = int(self.data.get(kind, 0)) + n
            if kind not in ("errors", "skipped"):
                self.data["total"] = int(self.data.get("total", 0)) + n
            self.data["last_run"] = datetime.now().isoformat(timespec="seconds")
            self.save()
        daily_limit.add(n)

    def add_bytes(self, n: int, direction: str = "sent") -> None:
        key = "bytes_sent" if direction == "sent" else "bytes_recv"
        with self._lock:
            self.data[key] = int(self.data.get(key, 0)) + int(n)

    def add_time(self, seconds: float) -> None:
        with self._lock:
            self.data["elapsed"] = round(
                float(self.data.get("elapsed", 0.0)) + float(seconds), 2
            )
            self.save()

    def new_session(self) -> None:
        with self._lock:
            self.data["sessions"] = int(self.data.get("sessions", 0)) + 1
            self.save()


# ── Formatadores ──────────────────────────────────────────────────────────────


class Fmt:
    @staticmethod
    def size(n: float) -> str:
        n = float(max(0, n))
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024.0:
                return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
            n /= 1024.0
        return f"{n:.1f} PB"

    @staticmethod
    def duration(seconds: float) -> str:
        s = int(max(0, seconds))
        if s < 60:
            return f"{s}s"
        if s < 3600:
            return f"{s // 60}m {s % 60:02d}s"
        if s < 86400:
            return f"{s // 3600}h {(s % 3600) // 60:02d}m"
        return f"{s // 86400}d {(s % 86400) // 3600:02d}h"

    @staticmethod
    def eta(done: int, total: int, elapsed: float) -> str:
        if done <= 0 or total <= done or elapsed <= 0:
            return "—"
        remaining = (total - done) * (elapsed / done)
        return Fmt.duration(remaining)

    @staticmethod
    def number(n: int) -> str:
        return f"{int(n):,}".replace(",", ".")

    @staticmethod
    def trunc(text: str, n: int = 40) -> str:
        text = str(text or "")
        return text if len(text) <= n else text[: max(0, n - 1)] + "…"

    @staticmethod
    def speed(bytes_per_sec: float) -> str:
        if bytes_per_sec <= 0:
            return "—"
        return Fmt.size(bytes_per_sec) + "/s"

    @staticmethod
    def date(value: Any) -> str:
        if not value:
            return "—"
        try:
            if isinstance(value, (int, float)):
                dt = datetime.fromtimestamp(value)
            else:
                dt = datetime.fromisoformat(str(value))
            return dt.strftime("%d/%m/%Y %H:%M")
        except Exception:
            return str(value)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def elapsed_since(start: float) -> float:
    return max(0.0, time.time() - start)

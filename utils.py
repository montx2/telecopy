"""
utils.py — Logging, statistics tracking, and formatting helpers.
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from config import DATA_DIR, LOGS_DIR, STATS_FILE, DAILY_LIMIT, DAILY_WARN, init_dirs

init_dirs()


# ── Logger ────────────────────────────────────────────────────────────────────

class Logger:
    """
    Append-only log file.  Sensitive values (hashes, phone numbers,
    session strings, bot tokens) are redacted automatically.
    """

    _REDACT = [
        (re.compile(r"[a-f0-9]{32,}", re.I), "[HASH]"),
        (re.compile(r"\+\d{10,15}"),         "[PHONE]"),
        (re.compile(r"session_\w+", re.I),   "[SESSION]"),
        (re.compile(r"\d{8,10}:[A-Za-z0-9_-]{35}"), "[TOKEN]"),
    ]

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or (LOGS_DIR / "telecopy.log")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()

    def _redact(self, text: str) -> str:
        for pattern, replacement in self._REDACT:
            text = pattern.sub(replacement, text)
        return text

    def _write(self, level: str, msg: str) -> None:
        ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] [{level:7}] {self._redact(str(msg))}\n"
        try:
            with self.path.open("a", encoding="utf-8", errors="ignore") as fh:
                fh.write(line)
        except OSError:
            pass

    def debug(self, msg: str)    -> None: self._write("DEBUG",   msg)
    def info(self, msg: str)     -> None: self._write("INFO",    msg)
    def success(self, msg: str)  -> None: self._write("SUCCESS", msg)
    def warning(self, msg: str)  -> None: self._write("WARNING", msg)
    def error(self, msg: str)    -> None: self._write("ERROR",   msg)
    def critical(self, msg: str) -> None: self._write("CRITICAL",msg)


logger = Logger()


# ── Daily limit tracker ───────────────────────────────────────────────────────

class DailyLimit:
    """
    Tracks how many messages were processed today and emits warnings
    as the recommended daily limit is approached.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or (DATA_DIR / "daily.json")
        self._data = self._load()
        self._reset_if_new_day()

    def _load(self) -> Dict[str, Any]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"date": "", "count": 0}

    def _save(self) -> None:
        try:
            self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _reset_if_new_day(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        if self._data.get("date") != today:
            self._data = {"date": today, "count": 0}
            self._save()

    def add(self, n: int = 1) -> None:
        self._reset_if_new_day()
        self._data["count"] += n
        self._save()

    @property
    def count(self) -> int:
        self._reset_if_new_day()
        return self._data.get("count", 0)

    def status(self) -> Tuple[str, int, int]:
        """Return (level, count, remaining)."""
        n   = self.count
        rem = max(0, DAILY_LIMIT - n)
        if n >= DAILY_LIMIT:
            return "limit", n, 0
        if n >= DAILY_WARN:
            return "warn", n, rem
        return "ok", n, rem


daily_limit = DailyLimit()


# ── Statistics ────────────────────────────────────────────────────────────────

class Stats:
    """Persistent session statistics."""

    _DEFAULTS: Dict[str, Any] = {
        "total":        0,
        "photos":       0,
        "videos":       0,
        "audio":        0,
        "documents":    0,
        "text":         0,
        "bytes_sent":   0,
        "bytes_recv":   0,
        "elapsed":      0.0,
        "sessions":     0,
        "last_run":     None,
    }

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or STATS_FILE
        self.data = self._load()

    def _load(self) -> Dict[str, Any]:
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                return {**self._DEFAULTS, **saved}
            except Exception:
                pass
        return dict(self._DEFAULTS)

    def save(self) -> None:
        try:
            self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        except OSError:
            pass

    def inc(self, kind: str, n: int = 1) -> None:
        self.data["total"] = self.data.get("total", 0) + n
        if kind in self.data:
            self.data[kind] = self.data.get(kind, 0) + n
        self.data["last_run"] = datetime.now().isoformat()
        daily_limit.add(n)
        self.save()

    def add_bytes(self, n: int, direction: str = "sent") -> None:
        key = "bytes_sent" if direction == "sent" else "bytes_recv"
        self.data[key] = self.data.get(key, 0) + n
        self.save()

    def add_time(self, seconds: float) -> None:
        self.data["elapsed"] = self.data.get("elapsed", 0.0) + seconds
        self.save()

    def new_session(self) -> None:
        self.data["sessions"] = self.data.get("sessions", 0) + 1
        self.save()


# ── Formatting helpers ────────────────────────────────────────────────────────

class Fmt:
    @staticmethod
    def size(n: int) -> str:
        if n < 0:
            return "0 B"
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024.0:
                return f"{n:.1f} {unit}"
            n /= 1024.0
        return f"{n:.1f} PB"

    @staticmethod
    def duration(seconds: float) -> str:
        s = int(seconds)
        if s < 60:
            return f"{s}s"
        if s < 3600:
            return f"{s // 60}m {s % 60:02d}s"
        return f"{s // 3600}h {(s % 3600) // 60:02d}m"

    @staticmethod
    def number(n: int) -> str:
        return f"{n:,}".replace(",", ".")

    @staticmethod
    def trunc(text: str, n: int = 40) -> str:
        if not text:
            return ""
        return text[:n] + "…" if len(text) > n else text

    @staticmethod
    def speed(bytes_per_sec: float) -> str:
        return Fmt.size(int(bytes_per_sec)) + "/s"

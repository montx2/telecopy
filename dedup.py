"""
dedup.py — Three-layer deduplication engine.

Lookup order (fastest → authoritative):
    1. LRU cache   — in-memory, O(1)
    2. Bloom filter — in-memory, O(k), probabilistic (FP ≈ 0.1 %)
    3. SQLite      — on-disk,   O(1) with index, always correct

On a cache miss in layers 1 and 2 we go to SQLite, so a Bloom
false-positive just adds one unnecessary DB query — harmless.
"""

import hashlib
import math
import threading
from collections import OrderedDict
from typing import Optional

from config import BLOOM_CAPACITY, BLOOM_FP_RATE, LRU_SIZE


# ── Bloom filter ──────────────────────────────────────────────────────────────

class BloomFilter:
    """
    Space-efficient probabilistic membership test.

    Uses double hashing (MD5 + SHA-1) to simulate k independent hash
    functions.  No external dependencies; backed by a bytearray.
    """

    def __init__(self, capacity: int = BLOOM_CAPACITY, fp_rate: float = BLOOM_FP_RATE) -> None:
        self.capacity = capacity
        self.fp_rate  = fp_rate

        # m = -(n · ln p) / (ln 2)²
        self._m = max(1, int(-(capacity * math.log(fp_rate)) / math.log(2) ** 2))
        # k = (m/n) · ln 2
        self._k = max(1, int((self._m / capacity) * math.log(2)))

        self._bits   = bytearray((self._m + 7) // 8)
        self._count  = 0
        self._lock   = threading.RLock()

    # -- internal helpers ------------------------------------------------------

    def _positions(self, key: str):
        h1 = int(hashlib.md5(key.encode()).hexdigest(), 16)
        h2 = int(hashlib.sha1(key.encode()).hexdigest(), 16)
        for i in range(self._k):
            yield (h1 + i * h2) % self._m

    def _set(self, pos: int) -> None:
        self._bits[pos >> 3] |= 1 << (pos & 7)

    def _test(self, pos: int) -> bool:
        return bool(self._bits[pos >> 3] & (1 << (pos & 7)))

    # -- public API ------------------------------------------------------------

    def add(self, key: str) -> None:
        with self._lock:
            for p in self._positions(key):
                self._set(p)
            self._count += 1

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return all(self._test(p) for p in self._positions(key))

    def clear(self) -> None:
        with self._lock:
            self._bits  = bytearray((self._m + 7) // 8)
            self._count = 0

    @property
    def count(self) -> int:
        return self._count

    @property
    def memory_kb(self) -> float:
        return len(self._bits) / 1024

    def fp_current(self) -> float:
        """Estimated current false-positive rate."""
        if self._count == 0:
            return 0.0
        return (1 - math.exp(-self._k * self._count / self._m)) ** self._k

    def stats(self) -> dict:
        return {
            "capacity":    self.capacity,
            "items":       self._count,
            "bits":        self._m,
            "hash_funcs":  self._k,
            "memory_kb":   round(self.memory_kb, 2),
            "fp_rate":     round(self.fp_current() * 100, 4),
        }


# ── LRU cache ─────────────────────────────────────────────────────────────────

class LRUCache:
    """Fixed-capacity LRU cache mapping str keys to int values."""

    def __init__(self, capacity: int = LRU_SIZE) -> None:
        self._capacity = capacity
        self._cache: OrderedDict[str, int] = OrderedDict()
        self._lock = threading.RLock()

    def get(self, key: str) -> Optional[int]:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            return None

    def put(self, key: str, value: int) -> None:
        with self._lock:
            self._cache[key] = value
            self._cache.move_to_end(key)
            if len(self._cache) > self._capacity:
                self._cache.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def stats(self) -> dict:
        return {
            "items":    len(self._cache),
            "capacity": self._capacity,
            "usage_pct": round(len(self._cache) / self._capacity * 100, 1),
        }


# ── Deduplication engine ──────────────────────────────────────────────────────

class DedupEngine:
    """
    Orchestrates the three deduplication layers.

    Callers interact with two async methods:
        check(hash, dest_id) → Optional[int]
        register(hash, dest_id, msg_id, size, source_id)

    The engine does NOT import `database` directly — it receives a
    repository object at construction time to stay testable.
    """

    def __init__(self, repo) -> None:
        self._repo  = repo
        self._lru   = LRUCache()
        self._bloom = BloomFilter()

    # -- warm-up ---------------------------------------------------------------

    def warm_up(self, dest_id: int) -> None:
        """Populate Bloom filter + LRU from recently stored hashes."""
        for h, msg_id in self._repo.load_dedup_hashes(dest_id):
            key = f"{h}:{dest_id}"
            self._bloom.add(key)
            self._lru.put(key, msg_id)

    # -- public API (called from async context via asyncio.to_thread) ----------

    def check(self, content_hash: str, dest_id: int) -> Optional[int]:
        """
        Return previously sent msg_id if duplicate, else None.
        This method is synchronous — wrap with asyncio.to_thread if needed.
        """
        key = f"{content_hash}:{dest_id}"

        # 1. LRU
        hit = self._lru.get(key)
        if hit is not None:
            return hit

        # 2. Bloom filter — definitive miss
        if key not in self._bloom:
            return None

        # 3. SQLite — resolves potential false positive
        msg_id = self._repo.check_dedup(content_hash, dest_id)
        if msg_id is not None:
            self._lru.put(key, msg_id)
        return msg_id

    def register(
        self,
        content_hash: str,
        dest_id: int,
        sent_msg_id: int,
        file_size: int,
        source_id: int = 0,
    ) -> None:
        """Record a successful send in all three layers."""
        key = f"{content_hash}:{dest_id}"
        self._lru.put(key, sent_msg_id)
        self._bloom.add(key)
        self._repo.register_dedup(content_hash, dest_id, sent_msg_id, file_size, source_id)

    # -- stats -----------------------------------------------------------------

    def stats(self) -> dict:
        return {
            "lru":   self._lru.stats(),
            "bloom": self._bloom.stats(),
        }

    def format_stats(self) -> str:
        s = self.stats()
        lru   = s["lru"]
        bloom = s["bloom"]
        return (
            f"LRU {lru['items']}/{lru['capacity']} ({lru['usage_pct']}%)  "
            f"·  Bloom {bloom['items']} items  {bloom['memory_kb']} KB  "
            f"FP {bloom['fp_rate']}%"
        )


# ── Content hashing helpers ───────────────────────────────────────────────────

def hash_bytes(data: bytes, sample: int = 1024 * 1024) -> str:
    """
    SHA-256 of data.  For large files, hash first + last MB + size
    to keep things fast without sacrificing practical uniqueness.
    """
    if len(data) <= sample * 2:
        return hashlib.sha256(data).hexdigest()

    h = hashlib.sha256()
    h.update(data[:sample])
    h.update(data[-sample:])
    h.update(str(len(data)).encode())
    return h.hexdigest()


def hash_message(msg) -> str:
    """Build a stable hash from a Telethon message object."""
    parts = []

    # Text
    text = (
        getattr(msg, "text", None)
        or getattr(msg, "caption", None)
        or getattr(msg, "message", None)
        or ""
    )
    parts.append(f"t:{text[:500]}")

    # Media identity
    for attr in ("photo", "video", "audio", "voice", "document", "sticker"):
        obj = getattr(msg, attr, None)
        if obj:
            parts.append(
                f"m:{attr}:{getattr(obj, 'id', 0)}:{getattr(obj, 'size', 0)}"
            )
            break
    else:
        parts.append("m:none")

    # Date (day granularity — avoids timezone drift issues)
    if getattr(msg, "date", None):
        try:
            parts.append(f"d:{msg.date.strftime('%Y%m%d')}")
        except Exception:
            pass

    return hashlib.sha256("|".join(parts).encode()).hexdigest()

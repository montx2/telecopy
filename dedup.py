"""
dedup.py — Motor de deduplicação em três camadas.

    1. LRU em memória   — O(1), primeiras ~50 mil mensagens
    2. Bloom filter     — O(k), probabilístico (falso positivo ≈ 0,1 %)
    3. SQLite           — fonte da verdade, índice por (hash, destino)

Um falso positivo do Bloom apenas gera uma consulta extra ao SQLite,
nunca um "skip" indevido.
"""

from __future__ import annotations

import hashlib
import math
import threading
from collections import OrderedDict
from typing import Dict, Optional

from config import BLOOM_CAPACITY, BLOOM_FP_RATE, LRU_SIZE


# ── Bloom filter ──────────────────────────────────────────────────────────────


class BloomFilter:
    """Bloom filter com dupla hash (blake2b + sha1) e vetor de bits."""

    def __init__(self, capacity: int = BLOOM_CAPACITY, fp_rate: float = BLOOM_FP_RATE) -> None:
        self.capacity = max(1, int(capacity))
        self.fp_rate = fp_rate
        self._m = max(64, int(-(self.capacity * math.log(fp_rate)) / (math.log(2) ** 2)))
        self._k = max(1, int((self._m / self.capacity) * math.log(2)))
        self._bits = bytearray((self._m + 7) // 8)
        self._count = 0
        self._lock = threading.RLock()

    def _hashes(self, key: str) -> tuple[int, int]:
        data = key.encode("utf-8", "replace")
        h1 = int.from_bytes(hashlib.blake2b(data, digest_size=8).digest(), "big")
        h2 = int.from_bytes(hashlib.sha1(data).digest()[:8], "big") | 1
        return h1, h2

    def _positions(self, key: str):
        h1, h2 = self._hashes(key)
        for i in range(self._k):
            yield (h1 + i * h2) % self._m

    def add(self, key: str) -> None:
        with self._lock:
            for pos in self._positions(key):
                self._bits[pos >> 3] |= 1 << (pos & 7)
            self._count += 1

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return all(
                self._bits[pos >> 3] & (1 << (pos & 7)) for pos in self._positions(key)
            )

    def clear(self) -> None:
        with self._lock:
            self._bits = bytearray((self._m + 7) // 8)
            self._count = 0

    @property
    def count(self) -> int:
        return self._count

    @property
    def memory_kb(self) -> float:
        return len(self._bits) / 1024

    def fp_current(self) -> float:
        if self._count == 0:
            return 0.0
        return (1 - math.exp(-self._k * self._count / self._m)) ** self._k

    def stats(self) -> Dict[str, float]:
        return {
            "capacity": self.capacity,
            "items": self._count,
            "bits": self._m,
            "hash_funcs": self._k,
            "memory_kb": round(self.memory_kb, 1),
            "fp_rate": round(self.fp_current() * 100, 4),
        }


# ── LRU ───────────────────────────────────────────────────────────────────────


class LRUCache:
    def __init__(self, capacity: int = LRU_SIZE) -> None:
        self._capacity = max(1, int(capacity))
        self._cache: "OrderedDict[str, int]" = OrderedDict()
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
            while len(self._cache) > self._capacity:
                self._cache.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def stats(self) -> Dict[str, float]:
        return {
            "items": len(self._cache),
            "capacity": self._capacity,
            "usage_pct": round(len(self._cache) / self._capacity * 100, 1),
        }


# ── Motor ─────────────────────────────────────────────────────────────────────


class DedupEngine:
    """Orquestra as três camadas. Recebe o repositório (injeção de dependência)."""

    def __init__(self, repo, enabled: bool = True) -> None:
        self._repo = repo
        self.enabled = enabled
        self._lru = LRUCache()
        self._bloom = BloomFilter()
        self._warmed: set[int] = set()
        self._lock = threading.RLock()

    def warm_up(self, dest_id: int, limit: int = 200_000) -> int:
        """Carrega hashes já enviados para este destino (uma vez por execução)."""
        if not self.enabled or dest_id in self._warmed:
            return 0
        with self._lock:
            if dest_id in self._warmed:
                return 0
            count = 0
            try:
                for content_hash, msg_id in self._repo.load_dedup_hashes(dest_id, limit):
                    key = f"{content_hash}:{dest_id}"
                    self._bloom.add(key)
                    self._lru.put(key, msg_id)
                    count += 1
            except Exception:
                count = 0
            self._warmed.add(dest_id)
            return count

    def check(self, content_hash: str, dest_id: int) -> Optional[int]:
        """Retorna o id da mensagem já enviada ou None."""
        if not self.enabled or not content_hash:
            return None
        key = f"{content_hash}:{dest_id}"

        hit = self._lru.get(key)
        if hit is not None:
            return hit

        if key not in self._bloom:
            return None  # definitivamente novo

        try:
            msg_id = self._repo.check_dedup(content_hash, dest_id)
        except Exception:
            return None
        if msg_id is not None:
            self._lru.put(key, msg_id)
        return msg_id

    def register(
        self,
        content_hash: str,
        dest_id: int,
        sent_msg_id: int,
        file_size: int = 0,
        source_id: int = 0,
    ) -> None:
        if not self.enabled or not content_hash:
            return
        key = f"{content_hash}:{dest_id}"
        self._lru.put(key, sent_msg_id)
        self._bloom.add(key)
        if self.enabled:
            try:
                self._repo.register_dedup(
                    content_hash, dest_id, sent_msg_id, file_size, source_id
                )
            except Exception:
                pass

    def reset(self) -> None:
        self._lru.clear()
        self._bloom.clear()
        self._warmed.clear()

    def stats(self) -> Dict[str, Dict[str, float]]:
        return {"lru": self._lru.stats(), "bloom": self._bloom.stats()}

    def format_stats(self) -> str:
        lru = self._lru.stats()
        bloom = self._bloom.stats()
        return (
            f"LRU {lru['items']}/{lru['capacity']} ({lru['usage_pct']}%) · "
            f"Bloom {bloom['items']} itens {bloom['memory_kb']} KB · FP {bloom['fp_rate']}%"
        )


# ── Hashes de conteúdo ────────────────────────────────────────────────────────


def hash_bytes(data: bytes, sample: int = 1024 * 1024) -> str:
    """SHA-256 rápido: arquivos grandes usam início + fim + tamanho."""
    if len(data) <= sample * 2:
        return hashlib.sha256(data).hexdigest()
    digest = hashlib.sha256()
    digest.update(data[:sample])
    digest.update(data[-sample:])
    digest.update(str(len(data)).encode())
    return digest.hexdigest()


def hash_file(path: str, sample: int = 1024 * 1024) -> str:
    """Hash de arquivo sem carregá-lo inteiro na memória."""
    import os

    size = os.path.getsize(path)
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        if size <= sample * 2:
            for chunk in iter(lambda: fh.read(1024 * 256), b""):
                digest.update(chunk)
        else:
            digest.update(fh.read(sample))
            fh.seek(-sample, os.SEEK_END)
            digest.update(fh.read(sample))
            digest.update(str(size).encode())
    return digest.hexdigest()


def _media_identity(msg) -> str:
    """Identidade estável da mídia (documento/foto do Telegram)."""
    media = getattr(msg, "media", None)
    if media is None:
        return "none"

    doc = getattr(media, "document", None)
    if doc is not None:
        doc_id = getattr(doc, "id", 0) or 0
        size = getattr(doc, "size", 0) or 0
        mime = getattr(doc, "mime_type", "") or ""
        access = getattr(doc, "access_hash", 0) or 0
        return f"doc:{doc_id}:{size}:{mime}:{access}"

    photo = getattr(media, "photo", None)
    if photo is not None:
        # fotos são recomprimidas ao reenviar: usa o id do arquivo original
        return f"photo:{getattr(photo, 'id', 0)}:{getattr(photo, 'access_hash', 0)}"

    # geo, contato, enquete, etc.
    return f"other:{type(media).__name__}"


def hash_message(msg) -> str:
    """
    Hash estável por conteúdo.  Duas cópias da mesma mensagem (mesmo que
    em canais diferentes) produzem o mesmo hash.
    """
    parts: list[str] = []

    media_id = _media_identity(msg)
    parts.append(f"m:{media_id}")

    text = (
        getattr(msg, "message", None)
        or getattr(msg, "text", None)
        or getattr(msg, "raw_text", None)
        or ""
    )
    if text:
        parts.append(f"t:{text.strip()}")

    if media_id == "none":
        # Mensagens só de texto: granularidade de dia evita colapsar
        # avisos legítimos repetidos em datas diferentes.
        date = getattr(msg, "date", None)
        if date is not None:
            try:
                parts.append(f"d:{date.strftime('%Y%m%d')}")
            except Exception:
                pass
    else:
        # Legendas diferentes de uma mesma mídia continuam sendo mídias distintas
        parts.append(f"g:{getattr(msg, 'grouped_id', '') or ''}")

    return hashlib.sha256("|".join(parts).encode("utf-8", "replace")).hexdigest()

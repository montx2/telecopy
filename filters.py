"""
filters.py — Filtros de mensagens (combináveis) e seleção interativa.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

FilterFn = Callable[[object], bool]

_URL_RE = re.compile(r"(https?://|www\.|t\.me/)", re.I)


# ── Predicados ────────────────────────────────────────────────────────────────


def media_of(msg):
    return getattr(msg, "media", None)


def is_photo(msg) -> bool:
    name = type(media_of(msg)).__name__
    return name in ("MessageMediaPhoto",)


def is_document(msg) -> bool:
    return type(media_of(msg)).__name__ == "MessageMediaDocument"


def _doc_mime(msg) -> str:
    media = media_of(msg)
    doc = getattr(media, "document", None)
    return (getattr(doc, "mime_type", "") or "").lower() if doc else ""


def is_video(msg) -> bool:
    if not is_document(msg):
        return False
    mime = _doc_mime(msg)
    if mime.startswith("video/"):
        return True
    attrs = getattr(getattr(media_of(msg), "document", None), "attributes", []) or []
    return any(type(a).__name__ == "DocumentAttributeVideo" for a in attrs)


def is_audio(msg) -> bool:
    if not is_document(msg):
        return False
    mime = _doc_mime(msg)
    if mime.startswith("audio/") or "voice" in mime or "ogg" in mime:
        return True
    attrs = getattr(getattr(media_of(msg), "document", None), "attributes", []) or []
    return any(type(a).__name__ in ("DocumentAttributeAudio",) for a in attrs)


def is_sticker(msg) -> bool:
    name = type(media_of(msg)).__name__
    if name == "MessageMediaDocument":
        attrs = getattr(getattr(media_of(msg), "document", None), "attributes", []) or []
        return any(type(a).__name__ == "DocumentAttributeSticker" for a in attrs)
    return False


def is_plain_document(msg) -> bool:
    """Documentos que não são vídeo, áudio, sticker ou imagem."""
    if not is_document(msg) or is_sticker(msg):
        return False
    if is_video(msg) or is_audio(msg):
        return False
    mime = _doc_mime(msg)
    return not mime.startswith("image/")


def is_text(msg) -> bool:
    return bool((getattr(msg, "message", "") or "").strip()) and media_of(msg) is None


def has_link(msg) -> bool:
    text = getattr(msg, "message", "") or ""
    return bool(_URL_RE.search(text))


def is_media(msg) -> bool:
    return media_of(msg) is not None


def is_recent(msg, days: int = 7) -> bool:
    date = getattr(msg, "date", None)
    if not date:
        return False
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    return date >= datetime.now(timezone.utc) - timedelta(days=days)


def is_original(msg) -> bool:
    return not getattr(msg, "fwd_from", None)


def is_large(msg, threshold: int = 50 * 1024 * 1024) -> bool:
    doc = getattr(media_of(msg), "document", None)
    return bool(doc) and int(getattr(doc, "size", 0) or 0) > threshold


# ── Registro: código → (chave i18n do nome, chave da descrição, função) ───────

_REGISTRY: Dict[str, Tuple[str, str, FilterFn]] = {
    "all":       ("filter.all",       "filter.all.desc",       lambda m: True),
    "photos":    ("filter.photos",    "filter.photos.desc",    is_photo),
    "videos":    ("filter.videos",    "filter.videos.desc",    is_video),
    "audio":     ("filter.audio",     "filter.audio.desc",     is_audio),
    "documents": ("filter.documents", "filter.documents.desc", is_plain_document),
    "text":      ("filter.text",      "filter.text.desc",      is_text),
    "links":     ("filter.links",     "filter.links.desc",     has_link),
    "media":     ("filter.media",     "filter.media.desc",     is_media),
    "large":     ("filter.large",     "filter.large.desc",     is_large),
    "recent":    ("filter.recent",    "filter.recent.desc",    is_recent),
    "original":  ("filter.original",  "filter.original.desc",  is_original),
}

ORDER: List[str] = list(_REGISTRY.keys())
# Os números correspondem exatamente ao menu: 0 = todas, 1..N = filtros.
_NUM_MAP: Dict[str, str] = {str(i): code for i, code in enumerate(ORDER)}


# ── API pública ───────────────────────────────────────────────────────────────


def _tr(key: str, fallback: str) -> str:
    try:
        from i18n import t

        return t(key)
    except Exception:
        return fallback


def list_filters() -> List[Tuple[str, str, str]]:
    return [
        (code, _tr(name_key, code), _tr(desc_key, ""))
        for code, (name_key, desc_key, _) in _REGISTRY.items()
    ]


def get_name(code: str) -> str:
    entry = _REGISTRY.get(code)
    return _tr(entry[0], code) if entry else code


def resolve(user_input: str) -> Optional[str]:
    """Aceita código ('photos') ou o número exibido no menu (0 = todas)."""
    value = (user_input or "").strip().lower()
    if value in _REGISTRY:
        return value
    return _NUM_MAP.get(value)


def apply(code: str, msg) -> bool:
    entry = _REGISTRY.get(code)
    if not entry or code == "all":
        return True
    try:
        return entry[2](msg)
    except Exception:
        return True


def available_codes() -> List[str]:
    return list(ORDER)

"""
filters.py — Message filtering engine with i18n support.
"""

from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

from i18n import t

FilterFn = Callable[[object], bool]


# ── Individual predicates ─────────────────────────────────────────────────────

def _is_photo(msg) -> bool:
    try:
        from telethon.tl.types import MessageMediaPhoto
        return isinstance(msg.media, MessageMediaPhoto)
    except Exception:
        return False


def _is_video(msg) -> bool:
    try:
        from telethon.tl.types import MessageMediaDocument
        if not isinstance(msg.media, MessageMediaDocument):
            return False
        mime = getattr(msg.media.document, "mime_type", "") or ""
        return "video" in mime.lower()
    except Exception:
        return False


def _is_audio(msg) -> bool:
    try:
        from telethon.tl.types import MessageMediaDocument, DocumentAttributeAudio
        if not isinstance(msg.media, MessageMediaDocument):
            return False
        doc  = msg.media.document
        mime = getattr(doc, "mime_type", "") or ""
        if "audio" in mime.lower() or "voice" in mime.lower():
            return True
        return any(isinstance(a, DocumentAttributeAudio)
                   for a in getattr(doc, "attributes", []))
    except Exception:
        return False


def _is_document(msg) -> bool:
    try:
        from telethon.tl.types import MessageMediaDocument
        if not isinstance(msg.media, MessageMediaDocument):
            return False
        mime = getattr(msg.media.document, "mime_type", "") or ""
        return not any(x in mime.lower() for x in ("video", "audio", "image"))
    except Exception:
        return False


def _is_text(msg) -> bool:
    try:
        return bool(msg.text and not msg.media)
    except Exception:
        return False


def _has_link(msg) -> bool:
    try:
        text = (msg.text or "").lower()
        return any(p in text for p in ("http://", "https://", "t.me/"))
    except Exception:
        return False


def _is_media(msg) -> bool:
    try:
        from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument
        return isinstance(msg.media, (MessageMediaPhoto, MessageMediaDocument))
    except Exception:
        return False


def _is_large(msg, threshold: int = 50 * 1024 * 1024) -> bool:
    try:
        from telethon.tl.types import MessageMediaDocument
        if not isinstance(msg.media, MessageMediaDocument):
            return False
        return (getattr(msg.media.document, "size", 0) or 0) > threshold
    except Exception:
        return False


def _is_recent(msg, days: int = 7) -> bool:
    try:
        if not msg.date:
            return False
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        date   = msg.date
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return date >= cutoff
    except Exception:
        return False


def _is_original(msg) -> bool:
    return not getattr(msg, "fwd_from", None)


# ── Registry ──────────────────────────────────────────────────────────────────
# code → (i18n_name_key, i18n_desc_key, predicate)

_REGISTRY: Dict[str, Tuple[str, str, FilterFn]] = {
    "all":       ("filter.all",       "filter.all.desc",       lambda m: True),
    "photos":    ("filter.photos",    "filter.photos.desc",    _is_photo),
    "videos":    ("filter.videos",    "filter.videos.desc",    _is_video),
    "audio":     ("filter.audio",     "filter.audio.desc",     _is_audio),
    "documents": ("filter.documents", "filter.documents.desc", _is_document),
    "text":      ("filter.text",      "filter.text.desc",      _is_text),
    "links":     ("filter.links",     "filter.links.desc",     _has_link),
    "media":     ("filter.media",     "filter.media.desc",     _is_media),
    "large":     ("filter.large",     "filter.large.desc",     _is_large),
    "recent":    ("filter.recent",    "filter.recent.desc",    _is_recent),
    "original":  ("filter.original",  "filter.original.desc",  _is_original),
}

_NUM_MAP: Dict[str, str] = {str(i + 1): k for i, k in enumerate(_REGISTRY)}


# ── Public API ────────────────────────────────────────────────────────────────

def list_filters() -> List[Tuple[str, str, str]]:
    """Return list of (code, translated_name, translated_desc)."""
    return [(code, t(nk), t(dk)) for code, (nk, dk, _) in _REGISTRY.items()]


def get_name(code: str) -> str:
    entry = _REGISTRY.get(code)
    return t(entry[0]) if entry else code


def resolve(code: str) -> Optional[str]:
    if code in _REGISTRY:
        return code
    return _NUM_MAP.get(code)


def apply(code: str, msg) -> bool:
    entry = _REGISTRY.get(code)
    if not entry:
        return True
    try:
        return entry[2](msg)
    except Exception:
        return True

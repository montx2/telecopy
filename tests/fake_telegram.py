"""
tests/fake_telegram.py — Cliente Telegram simulado.

Permite testar o motor de cópia completo (download, envio, álbuns,
dedup, checkpoints, retomada, flood) sem tocar na rede.

Uso:
    from tests.fake_telegram import FakeClient, FakeMessage, make_channel
    client = FakeClient(messages)
    result = await Copier(client, repo=Repository(tmp_db), config=cfg).run(...)
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from telethon.errors import FloodWaitError


# ── Entidades e mídias falsas ─────────────────────────────────────────────────


@dataclass
class DocumentAttributeFilename:
    """Imita telethon.tl.types.DocumentAttributeFilename."""
    filename: Optional[str] = None


@dataclass
class DocumentAttributeVideo:
    duration: int = 10


@dataclass
class DocumentAttributeAudio:
    duration: int = 10


@dataclass
class DocumentAttributeSticker:
    alt: str = "🙂"


# compatibilidade com o nome antigo
FakeDocAttribute = DocumentAttributeFilename


@dataclass
class FakeDocument:
    id: int
    size: int
    mime_type: str = "application/pdf"
    attributes: List[Any] = field(default_factory=list)
    access_hash: int = 0


@dataclass
class FakePhotoSize:
    size: int = 1024
    type: str = "y"


@dataclass
class FakePhoto:
    id: int
    sizes: List[FakePhotoSize] = field(default_factory=list)
    access_hash: int = 0


@dataclass
class MessageMediaDocument:
    """Imita telethon.tl.types.MessageMediaDocument."""
    document: FakeDocument


@dataclass
class MessageMediaPhoto:
    """Imita telethon.tl.types.MessageMediaPhoto."""
    photo: FakePhoto


# aliases retrocompatíveis
FakeMediaDocument = MessageMediaDocument
FakeMediaPhoto = MessageMediaPhoto


@dataclass
class FakeFile:
    name: Optional[str] = None


@dataclass
class FakeMessage:
    id: int
    message: str = ""
    media: Any = None
    grouped_id: Optional[int] = None
    action: Any = None
    fwd_from: Any = None
    entities: Any = None
    date: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    file: Optional[FakeFile] = None
    reply_to_msg_id: Optional[int] = None
    photo: Any = None
    document: Any = None

    def __post_init__(self) -> None:
        if self.media is not None:
            if isinstance(self.media, MessageMediaDocument) and self.file is None:
                name = None
                for attr in self.media.document.attributes:
                    if getattr(attr, "filename", None):
                        name = attr.filename
                self.file = FakeFile(name=name or f"arquivo_{self.id}.bin")
                self.document = self.media.document
            if isinstance(self.media, MessageMediaPhoto):
                self.photo = self.media.photo


@dataclass
class FakeChannel:
    id: int
    title: str
    username: Optional[str] = None
    forum: bool = False
    broadcast: bool = True
    participants_count: int = 0

    class _Meta:
        pass


# ── Cliente falso ────────────────────────────────────────────────────────────


class FakeClient:
    """Imita a API do Telethon usada pelo TELECOPY."""

    def __init__(self, messages: List[FakeMessage], fail_ids: Optional[set] = None,
                 temp_dir: Optional[str] = None) -> None:
        self.messages = sorted(messages, key=lambda m: m.id)
        self.sent: List[Dict[str, Any]] = []
        self.forwarded: List[Dict[str, Any]] = []
        self.downloads: List[int] = []
        self.fail_ids = set(fail_ids or ())
        self.flood_on: Dict[int, int] = {}     # msg_id → segundos de FloodWait
        self.temp_dir = temp_dir or "/tmp"
        self._ids = 1000
        self.handlers: List[Any] = []

    # -- downloads/upload ---------------------------------------------------

    async def download_media(self, message, file=None, progress_callback=None, **_):
        forced = self.flood_on.pop(message.id, None)
        if forced:
            raise FloodWaitError(request=None, capture=forced)
        if message.id in self.fail_ids:
            raise OSError("falha simulada de download")

        _, media_type, size = _media_info(message)
        payload_size = max(64, min(size or 2048, 8192))
        os.makedirs(os.path.dirname(str(file)), exist_ok=True)
        with open(str(file), "wb") as handle:
            handle.write(b"x" * payload_size)
        if progress_callback:
            progress_callback(payload_size // 2, payload_size)
            progress_callback(payload_size, payload_size)
        self.downloads.append(message.id)
        return str(file)

    async def send_file(self, entity, file=None, caption=None, reply_to=None,
                        progress_callback=None, **kwargs):
        files = file if isinstance(file, list) else [file]
        captions = caption if isinstance(caption, list) else [caption] * len(files)
        total = sum(os.path.getsize(f) for f in files if os.path.exists(f))
        if progress_callback:
            progress_callback(total or 1, total or 1)
        created = []
        for index, path in enumerate(files):
            self._ids += 1
            created.append(FakeMessage(id=self._ids))
            self.sent.append({
                "kind": "file",
                "dest": getattr(entity, "id", entity),
                "path": path,
                "caption": captions[index] if index < len(captions) else None,
                "size": os.path.getsize(path) if os.path.exists(path) else 0,
                "album": len(files) > 1,
                "reply_to": reply_to,
                "force_document": kwargs.get("force_document", False),
                "msg_id": self._ids,
            })
        return created if len(created) > 1 else created[0]

    async def send_message(self, entity, text, reply_to=None, **kwargs):
        self._ids += 1
        self.sent.append({
            "kind": "text",
            "dest": getattr(entity, "id", entity),
            "text": text,
            "reply_to": reply_to,
            "msg_id": self._ids,
        })
        return FakeMessage(id=self._ids, message=text)

    async def forward_messages(self, entity, messages, from_peer=None, **kwargs):
        ids = messages if isinstance(messages, list) else [messages]
        self._ids += 1
        self.forwarded.append({
            "dest": getattr(entity, "id", entity),
            "ids": ids,
            "as_album": len(ids) > 1,
            "kwargs": dict(kwargs),
        })
        return [FakeMessage(id=self._ids) for _ in ids]

    # -- leitura ------------------------------------------------------------

    async def iter_messages(self, entity, reverse=False, **kwargs):
        topic = kwargs.get("reply_to")
        items = list(self.messages)
        if topic:
            items = [
                m for m in items
                if m.reply_to_msg_id == topic
                or (m.reply_to_msg_id or 0) >= topic * 1000
            ]
        for message in (items if reverse else reversed(items)):
            yield message

    async def get_messages(self, entity, ids=None, **_):
        if isinstance(ids, int):
            for message in self.messages:
                if message.id == ids:
                    return message
            return None
        return self.messages

    async def get_entity(self, value):
        if isinstance(value, (int, str)) and str(value) in self._entities:
            return self._entities[str(value)]
        raise ValueError(f"entidade desconhecida: {value}")

    def register_entity(self, entity) -> None:
        if not hasattr(self, "_entities"):
            self._entities: Dict[str, Any] = {}
        self._entities[str(entity.id)] = entity
        if getattr(entity, "username", None):
            self._entities[f"@{entity.username}"] = entity
            self._entities[entity.username] = entity

    # -- eventos ------------------------------------------------------------

    def on(self, event):
        def decorator(func):
            self.handlers.append(func)
            return func

        return decorator

    def remove_event_handler(self, func):
        if func in self.handlers:
            self.handlers.remove(func)

    async def run_until_disconnected(self):
        return

    async def get_me(self):
        @dataclass
        class Me:
            id: int = 1
            first_name: str = "Teste"
            username: str = "teste"
            phone: str = "+550000000000"

        return Me()


def _media_info(msg):
    from copier import media_info

    return media_info(msg)


# ── Fábricas de mensagens ────────────────────────────────────────────────────


def text_message(msg_id: int, text: str, **kwargs) -> FakeMessage:
    return FakeMessage(id=msg_id, message=text, **kwargs)


def photo_message(msg_id: int, caption: str = "", **kwargs) -> FakeMessage:
    photo = FakePhoto(id=msg_id * 7, sizes=[FakePhotoSize(size=4096)])
    return FakeMessage(id=msg_id, message=caption,
                       media=MessageMediaPhoto(photo=photo), **kwargs)


def document_message(msg_id: int, name: str = "doc.pdf",
                     size: int = 4096, mime: str = "application/pdf",
                     **kwargs) -> FakeMessage:
    doc = FakeDocument(id=msg_id * 11, size=size, mime_type=mime,
                       attributes=[FakeDocAttribute(filename=name)])
    return FakeMessage(id=msg_id, message=kwargs.pop("caption", ""),
                       media=MessageMediaDocument(document=doc), **kwargs)


def video_message(msg_id: int, name: str = "video.mp4", size: int = 8192,
                  **kwargs) -> FakeMessage:
    doc = FakeDocument(id=msg_id * 13, size=size, mime_type="video/mp4",
                       attributes=[DocumentAttributeFilename(filename=name),
                                   DocumentAttributeVideo()])
    return FakeMessage(id=msg_id, message=kwargs.pop("caption", ""),
                       media=MessageMediaDocument(document=doc), **kwargs)


def album_messages(start_id: int, count: int = 3, grouped_id: Optional[int] = None
                   ) -> List[FakeMessage]:
    gid = grouped_id or (start_id * 1000)
    return [photo_message(start_id + i, caption=f"foto {i}" if i == 0 else "",
                          grouped_id=gid) for i in range(count)]


def channel(channel_id: int, title: str, **kwargs) -> FakeChannel:
    return FakeChannel(id=channel_id, title=title, **kwargs)


def cleanup_dir(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)

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
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from telethon import functions, types
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
    reply_to: Any = None
    photo: Any = None
    document: Any = None

    def __post_init__(self) -> None:
        if self.reply_to is None and self.reply_to_msg_id is not None:
            self.reply_to = SimpleNamespace(
                reply_to_msg_id=self.reply_to_msg_id,
                reply_to_top_id=None,
                forum_topic=False,
            )
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


class FakeSender:
    """Sender MTProto simulado: send() devolve future, como no Telethon."""

    def __init__(self, client: "FakeClient", dc_id: int) -> None:
        self.client = client
        self.dc_id = dc_id
        self.disconnected = False
        self.requests: List[Any] = []

    def send(self, request, ordered: bool = False):
        import asyncio

        self.requests.append(request)
        return asyncio.create_task(self._run(request))

    async def _run(self, request):
        import asyncio

        name = type(request).__name__
        key = getattr(request, "offset", None) if name == "GetFileRequest" else getattr(request, "file_part", None)
        errors = self.client.part_errors.get((name, key), [])
        if errors:
            raise errors.pop(0)
        delay = self.client.part_delays.get((name, key), 0)
        self.client.active_parts += 1
        self.client.peak_parts = max(self.client.peak_parts, self.client.active_parts)
        try:
            if delay:
                await asyncio.sleep(delay)
            return await self.client._handle_part_request(request, self.dc_id)
        finally:
            self.client.active_parts -= 1

    async def disconnect(self) -> None:
        self.disconnected = True


class FakeClient:
    """Imita a API do Telethon usada pelo TELECOPY."""

    def __init__(self, messages: List[FakeMessage], fail_ids: Optional[set] = None,
                 temp_dir: Optional[str] = None) -> None:
        self.messages = sorted(messages, key=lambda m: m.id)
        self.sent: List[Dict[str, Any]] = []
        self.edits: List[Dict[str, Any]] = []
        self.edit_errors: Dict[int, List[Exception]] = {}
        self.edit_failures: Dict[int, Exception] = {}
        self.reference_errors: Dict[int, List[Exception]] = {}
        self.reference_attempts: List[int] = []
        self.forwarded: List[Dict[str, Any]] = []
        self.downloads: List[int] = []
        self.fail_ids = set(fail_ids or ())
        self.flood_on: Dict[int, int] = {}     # msg_id → segundos de FloodWait
        self.temp_dir = temp_dir or "/tmp"
        self._ids = 1000
        self.handlers: List[Any] = []
        self._random_messages: Dict[int, Any] = {}
        self.requests: List[Any] = []
        self.upload_attempts: List[str] = []
        self.upload_delays: Dict[str, float] = {}
        self.upload_errors: Dict[str, List[Exception]] = {}
        self.send_errors: Dict[str, List[Exception]] = {}
        self.upload_media_calls = 0
        # Transporte de partes (etapa B)
        self.session = SimpleNamespace(dc_id=1)
        self._sender = FakeSender(self, 1)
        self.file_data: Dict[int, bytes] = {}
        self.part_store: Dict[int, Dict[int, bytes]] = {}
        self.part_errors: Dict[Any, List[Exception]] = {}
        self.part_delays: Dict[Any, float] = {}
        self.part_calls: List[Any] = []
        self.active_parts = 0
        self.peak_parts = 0
        self.created_senders: List[FakeSender] = []
        self.borrowed_senders: List[FakeSender] = []

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

    async def upload_file(self, path, file_name=None, progress_callback=None, **kwargs):
        import asyncio

        name = file_name or os.path.basename(str(path))
        self.upload_attempts.append(name)
        errors = self.upload_errors.get(name, [])
        if errors:
            raise errors.pop(0)
        delay = self.upload_delays.get(name, 0)
        if delay:
            await asyncio.sleep(delay)
        if progress_callback:
            size = os.path.getsize(path) if os.path.exists(path) else 1
            progress_callback(size, size)
        return SimpleNamespace(path=str(path), name=file_name or os.path.basename(str(path)))

    async def _create_exported_sender(self, dc_id: int) -> FakeSender:
        sender = FakeSender(self, dc_id)
        self.created_senders.append(sender)
        return sender

    async def _borrow_exported_sender(self, dc_id: int) -> FakeSender:
        sender = FakeSender(self, dc_id)
        self.borrowed_senders.append(sender)
        return sender

    async def _return_exported_sender(self, sender: FakeSender) -> None:
        return None

    async def _handle_part_request(self, request, dc_id: int):
        name = type(request).__name__
        self.part_calls.append(request)
        if name == "GetFileRequest":
            offset = int(request.offset)
            limit = int(request.limit)
            assert offset % 4096 == 0
            assert limit % 4096 == 0 and 1048576 % limit == 0
            assert offset // 1048576 == (offset + limit - 1) // 1048576
            data = self.file_data.get(int(request.location.id), b"")
            return types.upload.File(
                type=types.storage.FileUnknown(), mtime=0,
                bytes=data[offset:offset + limit],
            )
        if name in {"SaveFilePartRequest", "SaveBigFilePartRequest"}:
            parts = self.part_store.setdefault(int(request.file_id), {})
            parts[int(request.file_part)] = bytes(request.bytes)
            return True
        raise AssertionError(f"Request de parte inesperado: {name}")

    @staticmethod
    def _reply_ids(reply_to):
        if reply_to is None:
            return None, None
        if isinstance(reply_to, int):
            return reply_to, None
        return getattr(reply_to, "reply_to_msg_id", None), getattr(reply_to, "top_msg_id", None)

    def _record_send(self, entity, kind, *, path=None, caption=None, text=None,
                     reply_to=None, album=False, random_id=None, **extra):
        self._ids += 1
        msg = FakeMessage(id=self._ids, message=text or caption or "")
        reply_id, top_id = self._reply_ids(reply_to)
        record = {
            "kind": kind, "dest": getattr(entity, "id", entity),
            "path": path, "caption": caption, "text": text,
            "size": os.path.getsize(path) if path and os.path.exists(path) else 0,
            "album": album, "reply_to": reply_id, "top_msg_id": top_id,
            "msg_id": self._ids, **extra,
        }
        self.sent.append(record)
        if random_id is not None:
            self._random_messages[int(random_id)] = msg
        return msg

    def _check_reference(self, media) -> None:
        media_id = getattr(getattr(media, "id", None), "id", None)
        if media_id is None:
            return
        self.reference_attempts.append(int(media_id))
        errors = self.reference_errors.get(int(media_id), [])
        if errors:
            raise errors.pop(0)

    async def send_file(self, entity, file=None, caption=None, reply_to=None,
                        progress_callback=None, **kwargs):
        files = file if isinstance(file, list) else [file]
        for item in files:
            if not isinstance(item, (str, Path)):
                self._check_reference(item)
        captions = caption if isinstance(caption, list) else [caption] * len(files)
        entities = kwargs.get("formatting_entities")
        if entities and not isinstance(entities[0], list):
            entities = [entities] * len(files)
        total = sum(os.path.getsize(f) for f in files if isinstance(f, (str, Path)) and os.path.exists(f))
        if progress_callback:
            progress_callback(total or 1, total or 1)
        created = []
        for index, item in enumerate(files):
            is_path = isinstance(item, (str, Path))
            created.append(self._record_send(
                entity, "file" if is_path else "native",
                path=str(item) if is_path else None,
                caption=captions[index] if index < len(captions) else None,
                reply_to=reply_to, album=len(files) > 1,
                force_document=kwargs.get("force_document", False),
                formatting_entities=(entities[index] if entities and index < len(entities) else None),
                attributes=kwargs.get("attributes"), mime_type=kwargs.get("mime_type"),
                input_media=None if is_path else item,
            ))
        return created if len(created) > 1 else created[0]

    async def send_message(self, entity, text, reply_to=None, **kwargs):
        return self._record_send(
            entity, "text", text=text, reply_to=reply_to,
            formatting_entities=kwargs.get("formatting_entities"),
        )

    async def edit_message(self, entity, message, text=None, *, file=None,
                           formatting_entities=None, **kwargs):
        msg_id = int(getattr(message, "id", message))
        errors = self.edit_errors.get(msg_id, [])
        if errors:
            raise errors.pop(0)
        if msg_id in self.edit_failures:
            raise self.edit_failures[msg_id]
        record = next((item for item in self.sent if item["msg_id"] == msg_id), None)
        if record is None:
            raise AssertionError(f"mensagem enviada não encontrada para edição: {msg_id}")
        is_path = isinstance(file, (str, Path))
        path = str(file) if is_path else None
        input_media = None if is_path else file
        if input_media is not None:
            self._check_reference(input_media)
        record.update({
            "kind": "file" if path else ("reference" if input_media is not None else "text"),
            "path": path,
            "input_media": input_media,
            "text": text if not path and input_media is None else None,
            "caption": text if path or input_media is not None else None,
            "size": os.path.getsize(path) if path and os.path.exists(path) else 0,
            "formatting_entities": formatting_entities,
        })
        self.edits.append({
            "dest": getattr(entity, "id", entity), "msg_id": msg_id,
            "text": text, "path": path, "input_media": input_media,
            "formatting_entities": formatting_entities,
            **kwargs,
        })
        return FakeMessage(id=msg_id, message=text or "", entities=formatting_entities)

    async def get_input_entity(self, entity):
        return entity

    def _get_response_message(self, random_id, result, peer):
        if isinstance(random_id, list):
            return [self._random_messages.get(int(r)) for r in random_id]
        return self._random_messages.get(int(random_id))

    async def __call__(self, request):
        self.requests.append(request)
        name = type(request).__name__
        errors = self.send_errors.get(name, [])
        if errors:
            raise errors.pop(0)
        if name == "SendMessageRequest":
            return self._record_send(
                request.peer, "text", text=request.message, reply_to=request.reply_to,
                random_id=request.random_id, formatting_entities=request.entities,
            ) and SimpleNamespace()
        if name == "UploadMediaRequest":
            self.upload_media_calls += 1
            # O cliente real devolve MessageMedia; para o fake, devolver a
            # própria InputMedia permite reutilizar o caminho/atributos.
            return request.media
        if name == "SendMediaRequest":
            media = request.media
            self._check_reference(media)
            kind = "file" if type(media).__name__.startswith("InputMediaUploaded") else "native"
            path = getattr(getattr(media, "file", None), "path", None)
            self._record_send(
                request.peer, kind, path=path, caption=request.message,
                reply_to=request.reply_to, random_id=request.random_id,
                formatting_entities=request.entities, input_media=media,
                attributes=getattr(media, "attributes", None),
                mime_type=getattr(media, "mime_type", None),
                force_document=getattr(media, "force_file", False),
            )
            return SimpleNamespace()
        if name == "SendMultiMediaRequest":
            for item in request.multi_media:
                self._check_reference(item.media)
            for item in request.multi_media:
                media = item.media
                kind = "file" if type(media).__name__ == "InputMediaUploadedPhoto" or type(media).__name__ == "InputMediaUploadedDocument" else "native"
                path = getattr(getattr(media, "file", None), "path", None)
                self._record_send(
                    request.peer, kind, path=path, caption=item.message,
                    reply_to=request.reply_to, album=True, random_id=item.random_id,
                    formatting_entities=item.entities, input_media=media,
                    attributes=getattr(media, "attributes", None),
                    mime_type=getattr(media, "mime_type", None),
                )
            return SimpleNamespace()
        raise AssertionError(f"Request Telegram inesperado: {name}")

    async def forward_messages(self, entity, messages, from_peer=None, **kwargs):
        ids = messages if isinstance(messages, list) else [messages]
        created = []
        for _ in ids:
            self._ids += 1
            created.append(FakeMessage(id=self._ids))
        self.forwarded.append({
            "dest": getattr(entity, "id", entity),
            "ids": ids,
            "as_album": len(ids) > 1,
            "kwargs": dict(kwargs),
            "created_ids": [m.id for m in created],
        })
        return created

    # -- leitura ------------------------------------------------------------

    async def iter_messages(self, entity, reverse=False, **kwargs):
        topic = kwargs.get("reply_to")
        items = list(self.messages)
        if topic:
            def in_topic(m):
                reply = getattr(m, "reply_to", None)
                top = getattr(reply, "reply_to_top_id", None)
                rid = getattr(reply, "reply_to_msg_id", None) or m.reply_to_msg_id
                return top == topic or rid == topic or (rid or 0) >= topic * 1000
            items = [m for m in items if in_topic(m)]
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

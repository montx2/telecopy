"""
copier.py — Motor de cópia de canais do Telegram.

Fluxo
    produtor    → enfileira as mensagens na ordem original
    downloaders → baixam o conteúdo em paralelo (limitado por `workers`)
    sender      → envia em ordem sequencial estrita, respeitando o delay
    checkpoints → cada mensagem concluída é gravada no SQLite (retomada)

Modos
    • copy    — baixa e reenvia (cópia limpa, sem "encaminhado de")
    • forward — encaminha direto no Telegram (muito mais rápido)

Trata FloodWait, mensagens de serviço, álbuns, legendas longas, mídias
expiradas e limpa todos os arquivos temporários ao final.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from telethon import TelegramClient, events, functions, utils as tl_utils
from telethon.tl import types
from telethon.errors import (
    ChatAdminRequiredError,
    ChannelPrivateError,
    ChatWriteForbiddenError,
    FloodWaitError,
    MessageAuthorRequiredError,
    MessageEditTimeExpiredError,
    MessageIdInvalidError,
    MessageNotModifiedError,
    RPCError,
)
from telethon.helpers import generate_random_long

from config import (
    CAPTION_LIMIT,
    C,
    DAILY_LIMIT,
    MESSAGE_LIMIT,
    TEMP_DIR,
    RunConfig,
)
from database import Repository, Status
from dedup import DedupEngine, hash_message
from filters import apply as filter_apply
from i18n import t
from utils import Fmt, daily_limit, logger

QueueItem = Union[Any, List[Any]]

_SAFE_NAME = re.compile(r"[^\w\-. ]+", re.UNICODE)

SERVICE_ACTIONS = frozenset({
    "MessageActionPinMessage", "MessageActionChatJoinedByLink",
    "MessageActionChatAddUser", "MessageActionChatDeleteUser",
    "MessageActionChatEditTitle", "MessageActionChatEditPhoto",
    "MessageActionChannelCreate", "MessageActionHistoryClear",
    "MessageActionEmpty", "MessageActionChatMigrateTo",
    "MessageActionChannelMigrateFrom", "MessageActionSetMessagesTTL",
    "MessageActionGroupCall", "MessageActionInviteToGroupCall",
    "MessageActionContactSignUp", "MessageActionSecureValuesSent",
})


# ── Mídia ─────────────────────────────────────────────────────────────────────


def is_service(msg) -> bool:
    # Qualquer MessageAction é serviço do Telegram, não uma mensagem enviável.
    return bool(getattr(msg, "action", None))


def media_info(msg) -> Tuple[bool, str, int]:
    """(tem_mídia_enviável, tipo, tamanho_em_bytes)."""
    media = getattr(msg, "media", None)
    if media is None:
        return False, "text", 0

    name = type(media).__name__
    if name == "MessageMediaPhoto":
        photo = getattr(media, "photo", None)
        sizes = [int(getattr(s, "size", 0) or 0) for s in (getattr(photo, "sizes", []) or [])]
        return True, "photo", max(sizes) if sizes else 0

    if name == "MessageMediaDocument":
        doc = getattr(media, "document", None)
        if doc is None:
            return False, "text", 0
        mime = (getattr(doc, "mime_type", "") or "").lower()
        size = int(getattr(doc, "size", 0) or 0)
        attrs = {type(a).__name__ for a in (getattr(doc, "attributes", []) or [])}
        if "DocumentAttributeSticker" in attrs:
            return True, "sticker", size
        if mime.startswith("video/") or "DocumentAttributeVideo" in attrs:
            return True, "video", size
        if mime.startswith("audio/") or "DocumentAttributeAudio" in attrs:
            return True, "audio", size
        # Imagem enviada como documento (arquivo) não é MessageMediaPhoto.
        # Manter como documento preserva nome, mime e bytes originais.
        return True, "document", size

    if name == "MessageMediaWebPage":
        # link preview não é mídia transferível
        return False, "text", 0
    if name == "MessageMediaPoll":
        return True, "poll", 0
    if name == "MessageMediaGeo":
        return True, "geo", 0
    if name == "MessageMediaVenue":
        return True, "venue", 0
    if name == "MessageMediaContact":
        return True, "contact", 0
    # Dice sorteia um novo valor; live location não é um arquivo. Não fingir
    # que uma nova mensagem reproduz o resultado/estado original.
    return False, "other", 0


def media_filename(msg, media_type: str) -> str:
    file_obj = getattr(msg, "file", None)
    if file_obj is not None and getattr(file_obj, "name", None):
        clean = _SAFE_NAME.sub("_", str(file_obj.name)).strip(" ._")
        if clean:
            return clean[:110]
    doc = getattr(getattr(msg, "media", None), "document", None)
    mime = (getattr(doc, "mime_type", "") or "").lower()
    ext = {
        "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp",
        "image/gif": "gif", "video/mp4": "mp4", "video/webm": "webm",
        "audio/mpeg": "mp3", "audio/ogg": "ogg", "audio/mp4": "m4a",
        "application/x-tgsticker": "tgs", "application/pdf": "pdf",
    }.get(mime)
    if not ext:
        ext = {"photo": "jpg", "video": "mp4", "audio": "mp3",
               "sticker": "webp", "document": "bin"}.get(media_type, "bin")
    return f"{media_type}_{getattr(msg, 'id', 0)}.{ext}"


def original_filename(msg, media_type: str) -> str:
    """Nome exibido no destino, sem o prefixo temporário de download."""
    file_obj = getattr(msg, "file", None)
    name = getattr(file_obj, "name", None)
    if name:
        return str(name)
    doc = getattr(getattr(msg, "media", None), "document", None)
    for attr in getattr(doc, "attributes", []) or []:
        if type(attr).__name__ == "DocumentAttributeFilename" and getattr(attr, "filename", None):
            return str(attr.filename)
    return media_filename(msg, media_type)


NATIVE_MEDIA_TYPES = frozenset({"poll", "contact", "geo", "venue"})


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le", "replace")) // 2


def _cut_index(text: str, limit: int) -> int:
    """Maior prefixo dentro do limite UTF-16, sem cortar par surrogate."""
    units = 0
    best = 0
    for index, char in enumerate(text):
        size = 2 if ord(char) > 0xFFFF else 1
        if units + size > limit:
            return best or index
        units += size
        if char in " \n" and units > limit // 2:
            best = index + 1
    return len(text)


def split_text(text: str, limit: int = MESSAGE_LIMIT) -> List[str]:
    """Divide textos longos preservando cada caractere e o limite UTF-16."""
    if utf16_len(text) <= limit:
        return [text]
    parts: List[str] = []
    remaining = text
    while remaining:
        if utf16_len(remaining) <= limit:
            parts.append(remaining)
            break
        cut = _cut_index(remaining, limit)
        if cut <= 0:
            cut = 1
        parts.append(remaining[:cut])
        remaining = remaining[cut:]
    return parts


def _split_entities(text: str, cut: int, entities) -> Tuple[List[Any], List[Any]]:
    """Recorta entidades UTF-16 junto com o texto, inclusive no meio de uma entidade."""
    if not entities:
        return [], []
    prefix = utf16_len(text[:cut])
    head: List[Any] = []
    tail: List[Any] = []
    for entity in entities:
        start = int(getattr(entity, "offset", 0) or 0)
        length = int(getattr(entity, "length", 0) or 0)
        end = start + length
        if end <= 0:
            continue
        if start < prefix:
            item = copy.copy(entity)
            item.offset = start
            item.length = min(end, prefix) - start
            if item.length > 0:
                head.append(item)
        if end > prefix:
            item = copy.copy(entity)
            item.offset = max(0, start - prefix)
            item.length = end - max(start, prefix)
            if item.length > 0:
                tail.append(item)
    return head, tail


def text_chunks(text: str, entities, limit: int) -> List[Tuple[str, List[Any]]]:
    chunks: List[Tuple[str, List[Any]]] = []
    remaining = text
    remaining_entities = list(entities or [])
    while remaining:
        if utf16_len(remaining) <= limit:
            chunks.append((remaining, remaining_entities))
            break
        cut = _cut_index(remaining, limit) or 1
        head, tail = _split_entities(remaining, cut, remaining_entities)
        chunks.append((remaining[:cut], head))
        remaining = remaining[cut:]
        remaining_entities = tail
    return chunks


def _caption(msg) -> str:
    return getattr(msg, "message", "") or ""


def _entities(msg):
    return getattr(msg, "entities", None)


def _entities_json(msg) -> str:
    """Entidades tipadas para JSON, sem pickle/eval nem perda de custom emoji."""
    items = []
    for entity in _entities(msg) or []:
        if hasattr(entity, "to_dict"):
            data = entity.to_dict()
            data.pop("_", None)
        else:
            data = {k: v for k, v in getattr(entity, "__dict__", {}).items()
                    if not k.startswith("_")}
        data["type"] = type(entity).__name__
        items.append(data)
    return json.dumps(items, ensure_ascii=False)


def _decode_entities(raw: Optional[str]) -> List[Any]:
    """Restaura entidades TL tipadas; JSON inválido é logado, nunca executado."""
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except (TypeError, ValueError) as exc:
        logger.warning(f"Entidades de reserva inválidas: {exc}")
        return []
    entities = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            logger.warning(f"Entidade de reserva inválida: {item!r}")
            continue
        name = item.get("type")
        cls = getattr(types, str(name), None)
        if cls is None or not str(name).startswith("MessageEntity"):
            logger.warning(f"Tipo de entidade desconhecido: {name}")
            continue
        fields = {k: v for k, v in item.items() if k != "type"}
        try:
            entities.append(cls(**fields))
        except (TypeError, ValueError) as exc:
            logger.warning(f"Entidade {name} não restaurada: {exc}")
    return entities


def add_signature(caption: str, signature: str) -> str:
    if not signature:
        return caption
    sig = f"\n\n{signature}"
    available = max(0, CAPTION_LIMIT - len(sig))
    base = caption or ""
    if len(base) > available:
        base = base[: max(0, available - 1)] + "…"
    return base + sig


def split_text(text: str, limit: int = MESSAGE_LIMIT) -> List[str]:
    """Divide textos longos em partes aceitas pelo Telegram."""
    if len(text) <= limit:
        return [text]
    parts: List[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            parts.append(remaining)
            break
        cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = remaining.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    return parts


# ── Estatísticas / progresso ──────────────────────────────────────────────────


class DailyLimitReached(RuntimeError):
    """Limite diário de envios atingido; interrompe sem marcar falha."""


@dataclass
class TransferStats:
    start: float = field(default_factory=time.time)
    done: int = 0
    errors: int = 0
    skipped: int = 0
    duplicates: int = 0
    bytes_uploaded: int = 0
    albums: int = 0
    planned: int = 0
    indexed: int = 0
    previously_done: int = 0
    unsupported: int = 0
    placeholders: int = 0
    interrupted: bool = False
    complete: bool = False
    topics: List[Dict[str, Any]] = field(default_factory=list)
    first_error: Optional[str] = None

    def elapsed(self) -> float:
        return max(0.001, time.time() - self.start)

    def per_minute(self) -> float:
        return (self.done + self.skipped) / self.elapsed() * 60

    def as_result(self) -> Dict[str, Any]:
        return {
            "done": self.done,
            "skipped": self.skipped,
            "errors": self.errors,
            "duplicates": self.duplicates,
            "albums": self.albums,
            "bytes": self.bytes_uploaded,
            "indexed": self.indexed,
            "previously_done": self.previously_done,
            "unsupported": self.unsupported,
            "placeholders": self.placeholders,
            "interrupted": self.interrupted,
            "complete": self.complete,
            "topics": self.topics,
            "first_error": self.first_error,
            "elapsed": Fmt.duration(self.elapsed()),
            "speed": f"{self.per_minute():.0f} {t('result.per_min')}",
        }


class ProgressDisplay:
    """Barra Rich com fallback automático para texto simples."""

    def __init__(self, total: int, title: str = "", rich_ok: bool = True) -> None:
        self.total = max(0, total)
        self.title = title
        self._prog = None
        self._task = None
        self._last_plain = 0.0
        try:
            if not rich_ok:
                raise ImportError("rich desabilitado")
            from rich.console import Console
            from rich.progress import (
                BarColumn,
                MofNCompleteColumn,
                Progress,
                SpinnerColumn,
                TextColumn,
                TimeElapsedColumn,
                TimeRemainingColumn,
            )

            self._prog = Progress(
                SpinnerColumn(style="purple"),
                TextColumn("[bold orange3]{task.description}"),
                BarColumn(complete_style="orange3", finished_style="green", pulse_style="purple"),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                TimeRemainingColumn(),
                console=Console(highlight=False),
                refresh_per_second=8,
            )
            self._task = self._prog.add_task(
                Fmt.trunc(title or "Copiando", 32), total=self.total or None
            )
        except Exception:
            self._prog = None

    def __enter__(self) -> "ProgressDisplay":
        if self._prog:
            try:
                self._prog.start()
            except Exception:
                self._prog = None
        return self

    def __exit__(self, *exc) -> None:
        if self._prog:
            try:
                self._prog.stop()
            except Exception:
                pass

    def add_transfer(self, description: str):
        if not self._prog:
            return None
        try:
            return self._prog.add_task(description, total=100)
        except Exception:
            return None

    def update_transfer(self, task_id, current: int, total: int,
                        description: Optional[str] = None) -> None:
        if not self._prog or task_id is None:
            return
        kwargs: Dict[str, Any] = {"completed": current}
        if total:
            kwargs["total"] = total
        if description:
            kwargs["description"] = description
        try:
            self._prog.update(task_id, **kwargs)
        except Exception:
            pass

    def remove_transfer(self, task_id) -> None:
        if not self._prog or task_id is None:
            return
        try:
            self._prog.remove_task(task_id)
        except Exception:
            pass

    def update(self, done: int, status: str = "") -> None:
        if self._prog:
            try:
                self._prog.update(
                    self._task,
                    completed=done,
                    description=f"[bold orange3]{Fmt.trunc(status or self.title or 'Copiando', 38)}",
                )
                return
            except Exception:
                pass
        now = time.time()
        if now - self._last_plain < 0.4:
            return
        self._last_plain = now
        total = max(1, self.total)
        pct = min(100, int(done / total * 100))
        filled = int(pct / 4)
        print(f"\r  [{'█' * filled}{'░' * (25 - filled)}] {pct:3d}%  {done}/{self.total}  "
              f"{status[:24]}    ", end="", flush=True)

    def finish(self, stats: TransferStats) -> None:
        if not self._prog:
            print()


# ── Motor ─────────────────────────────────────────────────────────────────────


class Copier:
    """Copia o histórico (ou espelha em tempo real) de um canal para outro."""

    def __init__(
        self,
        client: TelegramClient,
        repo: Optional[Repository] = None,
        config: Optional[RunConfig] = None,
        rich_ok: bool = True,
    ) -> None:
        if repo is None:
            from database import db as _db

            repo = _db
        self.client = client
        self.repo = repo
        self.cfg = config or RunConfig()
        self.rich_ok = rich_ok
        self.dedup = DedupEngine(repo, enabled=self.cfg.use_dedup)
        self._stop = False
        self._flood_penalty = 0.0

    # -- controle -----------------------------------------------------------

    @property
    def delay(self) -> float:
        return max(0.1, self.cfg.delay + self._flood_penalty)

    def request_stop(self) -> None:
        self._stop = True

    # ── API principal ────────────────────────────────────────────────────────

    async def _destination_error(self, dest) -> Optional[str]:
        """Checa permissões de postagem antes de indexar todo o histórico."""
        if not hasattr(self.client, "get_permissions"):
            return None
        try:
            me = await self.client.get_me(input_peer=True)
            permissions = await self.client.get_permissions(dest, me)
        except Exception as exc:
            if type(exc).__name__ in {
                "ChatAdminRequiredError",
                "ChannelPrivateError",
                "UserBannedInChannelError",
                "UserNotParticipantError",
            }:
                return self._explain(exc)
            logger.warning(f"Não foi possível validar permissão do destino: {exc}")
            return None

        if permissions is None:
            return None
        if getattr(dest, "broadcast", False):
            if not getattr(permissions, "post_messages", False):
                return t("err.need_admin")
        elif getattr(permissions, "is_banned", False) or getattr(permissions, "has_left", False):
            return t("err.send_forbidden")
        return None

    async def run(
        self,
        source,
        dest,
        filter_code: str = "all",
        resume: Optional[bool] = None,
        source_topic: Optional[int] = None,
        dest_topic: Optional[int] = None,
        topic_map: Optional[Dict[int, int]] = None,
        forward: Optional[bool] = None,
        session_id: Optional[str] = None,
        topic_titles: Optional[Dict[int, str]] = None,
    ) -> Dict[str, Any]:
        self._stop = False
        source_id = int(getattr(source, "id", 0) or 0)
        dest_id = int(getattr(dest, "id", 0) or 0)
        resume = self.cfg.resume if resume is None else resume
        forward = self.cfg.forward_mode if forward is None else forward
        sess = session_id or f"{source_id}:{dest_id}:{int(time.time())}"
        topic_roots = set(topic_map or {})
        if source_topic:
            topic_roots.add(int(source_topic))

        if getattr(source, "noforwards", False) and forward:
            forward = False
            print(f"\n  {C.CYAN}·{C.RST}  {t('copy.source_protected')}", flush=True)

        destination_error = await self._destination_error(dest)
        if destination_error:
            blocked = TransferStats(errors=1, first_error=destination_error)
            print(f"\n  {C.RED}✗{C.RST}  "
                  f"{t('copy.first_failure', reason=destination_error)}", flush=True)
            return blocked.as_result()

        self.dedup.warm_up(dest_id, limit=200_000)

        print(f"\n  {C.CYAN}·{C.RST}  {t('copy.indexing')}…", end="", flush=True)
        messages = await self._collect(source, filter_code, source_topic, topic_map)
        print(f"\r  {C.GREEN}✓{C.RST}  {Fmt.number(len(messages))} {t('copy.found')}            ")

        if not messages:
            print(f"  {C.YELLOW}!{C.RST}  {t('copy.none')}")
            return TransferStats(complete=True).as_result()

        items = self._group(messages, topic_roots)
        # Com deduplicação desativada (cópia fiel), um 'skipped' antigo pode
        # ter sido uma repetição legítima descartada pela versão anterior.
        completed_before = (self.repo.completed_ids(source_id, dest_id)
                            if self.cfg.use_dedup else self.repo.done_ids(source_id, dest_id))
        previously_done = sum(1 for msg in messages if msg.id in completed_before)

        if resume and completed_before:
            before = sum(len(i) if isinstance(i, list) else 1 for i in items)
            items = [
                item for item in items
                if not all(m.id in completed_before
                           for m in (item if isinstance(item, list) else [item]))
            ]
            after = sum(len(i) if isinstance(i, list) else 1 for i in items)
            if before - after:
                print(f"  {C.GRAY}·{C.RST}  {Fmt.number(before - after)} {t('copy.already')}")

        planned = sum(len(i) if isinstance(i, list) else 1 for i in items)
        stats = TransferStats(planned=planned, indexed=len(messages),
                              previously_done=previously_done)
        if planned == 0:
            print(f"  {C.GREEN}✓{C.RST}  {t('copy.all_done')}")
            self._audit(stats, messages, source_id, dest_id, topic_map, topic_titles, topic_roots)
            return stats.as_result()

        albums = sum(1 for i in items if isinstance(i, list))
        if albums:
            print(f"  {C.CYAN}·{C.RST}  {albums} {t('copy.albums')}")

        self.repo.register_session(
            sess, source_id, dest_id,
            getattr(source, "title", "") or "", getattr(dest, "title", "") or "",
        )

        mode = t("copy.mode.forward") if forward else t("copy.mode.copy")
        print(f"  {C.CYAN}·{C.RST}  {t('copy.mode')}: {mode}")
        print(f"  {C.GRAY}·  {Fmt.number(planned)} msgs · {self.cfg.workers} workers · "
              f"{self.cfg.delay:.1f}s{C.RST}\n")

        with ProgressDisplay(planned, getattr(source, "title", "") or "Copiando",
                             self.rich_ok) as progress:
            if forward:
                await self._forward_loop(items, source, dest, source_id, dest_id,
                                         dest_topic, topic_map, stats, progress)
            else:
                await self._copy_loop(items, source, dest, source_id, dest_id,
                                      dest_topic, topic_map, stats, progress)

        stats.interrupted = self._stop
        self._audit(stats, messages, source_id, dest_id, topic_map, topic_titles, topic_roots)
        self.repo.update_stats(
            sess, success=stats.done, errors=stats.errors,
            duplicates=stats.duplicates, bytes_total=stats.bytes_uploaded,
        )
        self.repo.finish_session(sess, state="interrupted" if stats.interrupted else "finished")
        return stats.as_result()

    def _audit(self, stats: TransferStats, messages: List[Any], source_id: int,
               dest_id: int, topic_map: Optional[Dict[int, int]],
               topic_titles: Optional[Dict[int, str]], topic_roots: set) -> None:
        completed = (self.repo.completed_ids(source_id, dest_id)
                     if self.cfg.use_dedup else self.repo.done_ids(source_id, dest_id))
        missing_total = sum(1 for msg in messages if msg.id not in completed)
        placeholders = self.repo.placeholder_stats(source_id, dest_id)
        stats.complete = (
            not stats.interrupted and stats.errors == 0
            and stats.unsupported == 0 and missing_total == 0
            and placeholders["open"] == 0 and placeholders["unrecoverable"] == 0
        )
        if not topic_map:
            return
        counts: Dict[int, Dict[str, Any]] = {}
        for topic_id in sorted(topic_map):
            counts[topic_id] = {
                "id": topic_id, "dest_id": topic_map[topic_id],
                "title": (topic_titles or {}).get(topic_id, str(topic_id)),
                "expected": 0, "done": 0, "missing": 0,
            }
        for msg in messages:
            topic_id = self._source_topic_id(msg, topic_roots) or 1
            row = counts.setdefault(topic_id, {
                "id": topic_id, "dest_id": topic_map.get(topic_id),
                "title": (topic_titles or {}).get(topic_id, str(topic_id)),
                "expected": 0, "done": 0, "missing": 0,
            })
            row["expected"] += 1
            if msg.id in completed:
                row["done"] += 1
            else:
                row["missing"] += 1
        stats.topics = list(counts.values())

    # ── Coleta / agrupamento ────────────────────────────────────────────────

    async def _collect(self, source, filter_code: str,
                       topic_id: Optional[int],
                       topic_map: Optional[Dict[int, int]] = None) -> List[Any]:
        """Lê cada tópico e também o histórico global, unindo por ID.

        ``messages.getHistory`` sozinho não é uma fonte confiável para todos os
        tópicos de um fórum; ``messages.getReplies`` de cada raiz cobre as
        mensagens que o histórico global pode omitir. FloodWait nunca é tratado
        como fim-da-lista: a leitura é repetida ou a execução falha explicitamente.
        """
        if topic_id:
            streams: List[Optional[int]] = [int(topic_id)]
        elif topic_map:
            streams = [int(tid) for tid in sorted(topic_map)] + [None]
        else:
            streams = [None]

        by_id: Dict[int, Any] = {}
        for stream_topic in streams:
            for attempt in range(5):
                try:
                    async for msg in self._collect_stream(source, filter_code, stream_topic):
                        by_id[int(msg.id)] = msg
                    break
                except FloodWaitError as exc:
                    if attempt == 4:
                        raise RuntimeError(self._explain(exc)) from exc
                    await self._handle_flood(exc)
                except RPCError as exc:
                    # Algumas versões da API não aceitam getReplies(1) para o
                    # General; o histórico global já cobre esse tópico.
                    if stream_topic == 1:
                        logger.warning(f"Leitura específica do General indisponível: {exc}")
                        break
                    logger.error(f"Falha ao ler a origem: {exc}")
                    raise RuntimeError(self._explain(exc)) from exc
        return [by_id[key] for key in sorted(by_id)]

    async def _collect_stream(self, source, filter_code: str, topic_id: Optional[int]):
        kwargs: Dict[str, Any] = {"reverse": True}
        if topic_id:
            kwargs["reply_to"] = int(topic_id)
        count = 0
        async for msg in self.client.iter_messages(source, **kwargs):
            if self._stop:
                break
            if is_service(msg) or not filter_apply(filter_code, msg):
                continue
            count += 1
            if count % 500 == 0:
                print(f"\r  {C.CYAN}·{C.RST}  {Fmt.number(count)} "
                      f"{t('copy.indexed')}…", end="", flush=True)
            yield msg

    @classmethod
    def _group(cls, messages: Sequence[Any], topic_roots: Optional[set] = None) -> List[QueueItem]:
        roots = topic_roots or set()
        albums: Dict[Tuple[Optional[int], int], List[Any]] = defaultdict(list)
        for msg in messages:
            gid = getattr(msg, "grouped_id", None)
            if gid:
                key = (cls._source_topic_id(msg, roots), int(gid))
                albums[key].append(msg)

        items: List[QueueItem] = []
        seen: set = set()
        for msg in messages:
            gid = getattr(msg, "grouped_id", None)
            if not gid:
                items.append(msg)
                continue
            key = (cls._source_topic_id(msg, roots), int(gid))
            if key in seen:
                continue
            seen.add(key)
            group = albums[key]
            items.append(group if len(group) > 1 else group[0])
        return items

    @staticmethod
    def _source_topic_id(msg, topic_roots: Optional[set] = None) -> Optional[int]:
        """Tópico real, distinguindo a raiz do fórum de uma reply dentro dele."""
        roots = {int(x) for x in (topic_roots or set())}
        action = getattr(msg, "action", None)
        if type(action).__name__ == "MessageActionTopicCreate":
            return int(msg.id)
        reply = getattr(msg, "reply_to", None)
        top_id = getattr(reply, "reply_to_top_id", None)
        if top_id:
            return int(top_id)
        reply_id = getattr(reply, "reply_to_msg_id", None)
        if reply_id is None:
            reply_id = getattr(msg, "reply_to_msg_id", None)
        if reply_id is not None:
            reply_id = int(reply_id)
            if reply_id in roots:
                return reply_id
            if getattr(reply, "forum_topic", False):
                # Reply no General pode não trazer reply_to_top_id.
                return 1
        return 1 if roots else None

    @staticmethod
    def _source_reply_id(msg, topic_roots: Optional[set] = None) -> Optional[int]:
        """ID da mensagem respondida, ou None quando é só a raiz do tópico."""
        roots = {int(x) for x in (topic_roots or set())}
        reply = getattr(msg, "reply_to", None)
        reply_id = getattr(reply, "reply_to_msg_id", None)
        if reply_id is None:
            reply_id = getattr(msg, "reply_to_msg_id", None)
        if reply_id is None:
            return None
        reply_id = int(reply_id)
        top_id = getattr(reply, "reply_to_top_id", None)
        if reply_id in roots and not top_id:
            return None
        if reply_id in roots and int(top_id) == reply_id:
            return None
        return reply_id

    @classmethod
    def _destination_topic(cls, item, fallback: Optional[int],
                           topic_map: Optional[Dict[int, int]]) -> Optional[int]:
        if not topic_map:
            return fallback
        messages = item if isinstance(item, list) else [item]
        roots = set(topic_map)
        source_topic = cls._source_topic_id(messages[0], roots) or 1
        if source_topic not in topic_map:
            raise RuntimeError(t("copy.topic_unmapped", topic=source_topic))
        return topic_map[source_topic]

    def _reply_target(self, msg, source_id: int, dest_id: int,
                      topic_map: Optional[Dict[int, int]]) -> Optional[int]:
        roots = set(topic_map or {})
        parent = self._source_reply_id(msg, roots)
        if parent is None:
            return None
        route = self.repo.checkpoint_route(source_id, dest_id, parent)
        if route.get("status") in {"done", "skipped"} and route.get("dest_msg_id"):
            return int(route["dest_msg_id"])
        return None

    async def _forward_item(self, source, dest, ids: List[int],
                            dest_topic: Optional[int],
                            reply_to: Optional[int] = None) -> List[Optional[int]]:
        """Encaminha para o tópico/reply mapeado e devolve os IDs criados."""
        if not dest_topic and reply_to is None:
            result = await self.client.forward_messages(dest, ids, from_peer=source)
            if not isinstance(result, list):
                result = [result]
            return [getattr(item, "id", None) for item in result]

        from_peer = await self.client.get_input_entity(source)
        to_peer = await self.client.get_input_entity(dest)
        random_ids = [generate_random_long() for _ in ids]
        reply_header = None
        if reply_to is not None:
            reply_header = types.InputReplyToMessage(
                reply_to_msg_id=int(reply_to),
                top_msg_id=int(dest_topic) if dest_topic and dest_topic != 1 else None,
            )
        response = await self.client(functions.messages.ForwardMessagesRequest(
            from_peer=from_peer, id=ids, to_peer=to_peer,
            top_msg_id=int(dest_topic) if dest_topic else None,
            reply_to=reply_header, random_id=random_ids,
        ))
        parser = getattr(self.client, "_get_response_message", None)
        if parser is not None and response is not None:
            parsed = parser(random_ids, response, to_peer)
            if isinstance(parsed, list):
                return [getattr(item, "id", None) for item in parsed]
        return [None] * len(ids)

    # ── Encaminhamento (rápido) ─────────────────────────────────────────────

    async def _copy_single_item(
        self,
        index: int,
        item: QueueItem,
        source: Any,
        dest: Any,
        source_id: int,
        dest_id: int,
        item_topic: Optional[int],
        reply_to: Optional[int],
        stats: TransferStats,
        progress: ProgressDisplay,
    ) -> None:
        messages = item if isinstance(item, list) else [item]
        if self._is_duplicate(messages, dest_id):
            stats.duplicates += len(messages)
            stats.skipped += len(messages)
            for msg in messages:
                self.repo.mark_processed(source_id, msg.id, None, dest_id)
                self.repo.upsert_checkpoint(source_id, dest_id, msg.id, Status.SKIPPED)
            return
        payload = await self._prepare(index, item, progress, source=source)
        try:
            payload.update({
                "dest": dest,
                "dest_topic": item_topic,
                "reply_to": reply_to,
            })
            for retry in range(3):
                try:
                    self._check_limit()
                    await self._send(payload, source_id, dest_id, stats, progress)
                    return
                except FloodWaitError as exc:
                    if retry < 2:
                        await self._handle_flood(exc)
                        continue
                    raise
        finally:
            self._cleanup(payload.get("plan", []))

    async def _forward_loop(self, items, source, dest, source_id, dest_id,
                            dest_topic, topic_map, stats: TransferStats,
                            progress: ProgressDisplay) -> None:
        last_error_key = None
        consecutive_errors = 0
        first_failure_shown = False
        force_copy_fallback = bool(getattr(source, "noforwards", False))

        for index, item in enumerate(items):
            if self._stop:
                break
            messages = item if isinstance(item, list) else [item]
            ids = [m.id for m in messages]

            try:
                self._check_limit()
            except DailyLimitReached as exc:
                stats.first_error = str(exc)
                stats.interrupted = True
                print(f"\n  {C.YELLOW}!{C.RST}  {exc}", flush=True)
                break

            error: Optional[Exception] = None
            item_topic = None
            copied_via_fallback = False
            errors_before_item = stats.errors
            for retry in range(3):
                try:
                    # A lista de IDs preserva o agrupamento reconhecido pelo
                    # Telegram. `as_album` não tem mais efeito no Telethon;
                    # sem `drop_author`, o rótulo de encaminhamento é mantido.
                    item_topic = self._destination_topic(item, dest_topic, topic_map)
                    reply_to = self._reply_target(messages[0], source_id, dest_id, topic_map)
                    if force_copy_fallback or any(getattr(m, "noforwards", False) for m in messages):
                        copied_via_fallback = True
                        await self._copy_single_item(
                            index, item, source, dest, source_id, dest_id,
                            item_topic, reply_to, stats, progress,
                        )
                    else:
                        sent_ids = await self._forward_item(source, dest, ids, item_topic, reply_to)
                    break
                except FloodWaitError as exc:
                    if retry < 2:
                        await self._handle_flood(exc)
                        continue
                    error = exc
                except Exception as exc:
                    if type(exc).__name__ == "ChatForwardsRestrictedError":
                        force_copy_fallback = True
                        copied_via_fallback = True
                        try:
                            await self._copy_single_item(
                                index, item, source, dest, source_id, dest_id,
                                item_topic, reply_to, stats, progress,
                            )
                            error = None
                        except Exception as copy_exc:
                            error = copy_exc
                        break
                    error = exc
                break

            should_stop = False
            if error is None:
                if not copied_via_fallback:
                    for offset, msg in enumerate(messages):
                        dest_msg_id = sent_ids[offset] if offset < len(sent_ids) else None
                        self._mark_done(msg, source_id, dest_id, stats,
                                        dest_msg_id=dest_msg_id, dest_topic=item_topic)
                    if isinstance(item, list):
                        stats.albums += 1
                elif stats.errors > errors_before_item and not first_failure_shown:
                    print(f"\n  {C.RED}✗{C.RST}  "
                          f"{t('copy.first_failure', reason=stats.first_error)}",
                          flush=True)
                    first_failure_shown = True
                last_error_key = None
                consecutive_errors = 0
            else:
                self._mark_failed(messages, source_id, dest_id, stats, error,
                                  dest_topic=item_topic)
                reason = self._explain(error)
                if not first_failure_shown:
                    print(f"\n  {C.RED}✗{C.RST}  "
                          f"{t('copy.first_failure', reason=reason)}", flush=True)
                    first_failure_shown = True

                error_key = (type(error).__name__, reason)
                if error_key == last_error_key:
                    consecutive_errors += 1
                else:
                    last_error_key = error_key
                    consecutive_errors = 1

                if isinstance(error, FloodWaitError):
                    print(f"  {C.YELLOW}!{C.RST}  {t('copy.stop_flood')}", flush=True)
                    should_stop = True
                elif self._is_fatal_send_error(error):
                    print(f"  {C.YELLOW}!{C.RST}  {t('copy.stop_fatal')}", flush=True)
                    should_stop = True
                elif consecutive_errors >= 3:
                    print(f"  {C.YELLOW}!{C.RST}  {t('copy.stop_repeated')}", flush=True)
                    should_stop = True

            progress.update(
                stats.done + stats.skipped + stats.errors,
                self._progress_status(stats),
            )
            if should_stop:
                break
            await asyncio.sleep(self.delay)

    def _progress_status(self, stats: TransferStats) -> str:
        parts = [f"{stats.done} {t('copy.sent')}"]
        if stats.errors:
            parts.append(f"{stats.errors} {t('copy.errors')}")
        if stats.skipped:
            parts.append(f"{stats.skipped} {t('copy.skipped')}")
        if stats.duplicates:
            parts.append(f"{stats.duplicates} {t('copy.duplicates')}")
        if not stats.errors:
            parts.append(f"{stats.per_minute():.0f}/min")
        return " · ".join(parts)

    @staticmethod
    def _is_fatal_send_error(exc: Exception) -> bool:
        """Falhas estruturais que não vão se resolver tentando outra mensagem."""
        if isinstance(exc, FloodWaitError):
            return True
        if isinstance(exc, RuntimeError) and str(exc) == t(
            "copy.limit_reached", limit=DAILY_LIMIT
        ):
            return True
        return type(exc).__name__ in {
            "ChatAdminRequiredError",
            "ChatForbiddenError",
            "ChatSendMediaForbiddenError",
            "ChatSendPlainForbiddenError",
            "ChatWriteForbiddenError",
            "ChannelInvalidError",
            "ChannelPrivateError",
            "PeerIdInvalidError",
            "UserBannedInChannelError",
            "UserNotParticipantError",
        }

    # ── Cópia (download + envio ordenado) ───────────────────────────────────

    async def _copy_loop(self, items, source, dest, source_id, dest_id,
                         dest_topic, topic_map, stats: TransferStats,
                         progress: ProgressDisplay) -> None:
        in_queue: asyncio.Queue = asyncio.Queue()
        ready: Dict[int, Optional[Dict[str, Any]]] = {}
        cond = asyncio.Condition()
        total = len(items)

        for index, item in enumerate(items):
            await in_queue.put((index, item))
        for _ in range(self.cfg.workers):
            await in_queue.put(None)

        async def downloader() -> None:
            while True:
                job = await in_queue.get()
                if job is None:
                    in_queue.task_done()
                    return
                index, item = job
                messages = item if isinstance(item, list) else [item]
                payload: Optional[Dict[str, Any]] = None
                try:
                    if self._stop:
                        payload = None
                    elif self._is_duplicate(messages, dest_id):
                        stats.duplicates += len(messages)
                        stats.skipped += len(messages)
                        for msg in messages:
                            self.repo.mark_processed(source_id, msg.id, None, dest_id)
                            self.repo.upsert_checkpoint(source_id, dest_id, msg.id, Status.SKIPPED)
                        payload = {"skip": True}
                    else:
                        payload = await self._prepare(index, item, progress, source=source)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception(f"Falha ao preparar item {index}: {exc}")
                    # Não perde o motivo nem as mensagens: o sender ainda pode
                    # reservar a posição pelo caminho comum de _send.
                    reason = self._explain(exc)
                    plan = []
                    for msg in messages:
                        has_media, media_type, size = media_info(msg)
                        raw_media = getattr(msg, "media", None)
                        unsupported = (
                            not has_media and raw_media is not None
                            and type(raw_media).__name__ not in {"MessageMediaWebPage", "MessageMediaEmpty"}
                        )
                        plan.append({
                            "msg": msg, "media_type": media_type, "size": size,
                            "path": None, "downloadable": False, "native": False,
                            "failed": True, "unsupported": unsupported, "reason": reason,
                        })
                    payload = {"index": index, "plan": plan, "messages": messages,
                               "prepare_error": reason}
                finally:
                    in_queue.task_done()
                    # garante que o sender nunca fique esperando este índice
                    async with cond:
                        ready[index] = payload
                        cond.notify_all()

        async def sender() -> None:
            last_error_key = None
            consecutive_errors = 0
            first_failure_shown = False
            for index in range(total):
                if self._stop:
                    break
                async with cond:
                    if index not in ready:
                        try:
                            await asyncio.wait_for(cond.wait_for(lambda: index in ready),
                                                   timeout=600)
                        except asyncio.TimeoutError:
                            logger.error(f"Timeout aguardando preparação do item {index}")
                    payload = ready.pop(index, None)

                if payload is None:
                    item = items[index]
                    messages = item if isinstance(item, list) else [item]
                    stats.errors += len(messages)
                    if stats.first_error is None:
                        stats.first_error = t("copy.prepare_failure")
                    try:
                        failed_topic = self._destination_topic(items[index], dest_topic, topic_map)
                    except Exception:
                        failed_topic = dest_topic
                    for msg in messages:
                        self.repo.upsert_checkpoint(source_id, dest_id, msg.id, Status.ERROR,
                                                    dest_topic=failed_topic)
                        self.repo.add_failure(source_id, dest_id, msg.id, "preparação falhou")
                    if not first_failure_shown:
                        print(f"\n  {C.RED}✗{C.RST}  "
                              f"{t('copy.first_failure', reason=stats.first_error)}",
                              flush=True)
                        first_failure_shown = True
                    progress.update(stats.done + stats.skipped + stats.errors,
                                    self._progress_status(stats))
                    continue
                if payload.get("skip"):
                    progress.update(stats.done + stats.skipped + stats.errors,
                                    self._progress_status(stats))
                    continue

                item_messages = items[index] if isinstance(items[index], list) else [items[index]]
                payload.update({
                    "dest": dest,
                    "dest_topic": self._destination_topic(items[index], dest_topic, topic_map),
                    "reply_to": self._reply_target(item_messages[0], source_id, dest_id, topic_map),
                })
                errors_before_send = stats.errors
                error: Optional[Exception] = None
                try:
                    for retry in range(3):
                        try:
                            self._check_limit()
                            await self._send(payload, source_id, dest_id, stats, progress)
                            break
                        except FloodWaitError as exc:
                            if retry < 2:
                                await self._handle_flood(exc)
                                continue
                            error = exc
                        except DailyLimitReached:
                            raise
                        except Exception as exc:
                            error = exc
                        break

                    if error is not None:
                        logger.error(f"Erro no envio do item {index}: {error}")
                        self._mark_failed(payload["messages"], source_id, dest_id, stats, error,
                                          dest_topic=payload.get("dest_topic"),
                                          skip_ids=payload.get("reserved_ids"))
                        reason = self._explain(error)
                        if not first_failure_shown:
                            print(f"\n  {C.RED}✗{C.RST}  "
                                  f"{t('copy.first_failure', reason=reason)}", flush=True)
                            first_failure_shown = True

                        error_key = (type(error).__name__, reason)
                        if error_key == last_error_key:
                            consecutive_errors += 1
                        else:
                            last_error_key = error_key
                            consecutive_errors = 1

                        if isinstance(error, FloodWaitError):
                            print(f"  {C.YELLOW}!{C.RST}  {t('copy.stop_flood')}", flush=True)
                            self._stop = True
                        elif self._is_fatal_send_error(error):
                            print(f"  {C.YELLOW}!{C.RST}  {t('copy.stop_fatal')}", flush=True)
                            self._stop = True
                        elif consecutive_errors >= 3:
                            print(f"  {C.YELLOW}!{C.RST}  {t('copy.stop_repeated')}", flush=True)
                            self._stop = True
                    else:
                        last_error_key = None
                        consecutive_errors = 0
                        if stats.errors > errors_before_send and not first_failure_shown:
                            print(f"\n  {C.RED}✗{C.RST}  "
                                  f"{t('copy.first_failure', reason=stats.first_error)}",
                                  flush=True)
                            first_failure_shown = True
                except DailyLimitReached as exc:
                    stats.first_error = str(exc)
                    stats.interrupted = True
                    print(f"\n  {C.YELLOW}!{C.RST}  {exc}", flush=True)
                    self._stop = True
                finally:
                    self._cleanup(payload.get("plan", []))

                progress.update(stats.done + stats.skipped + stats.errors,
                                self._progress_status(stats))
                if self._stop:
                    break
                await asyncio.sleep(self.delay)

        downloaders = [asyncio.create_task(downloader()) for _ in range(self.cfg.workers)]
        sender_task = asyncio.create_task(sender())
        try:
            await sender_task
        finally:
            # Uma interrupção no envio (Ctrl+C, erro fatal) não pode deixar os
            # downloads rodando em segundo plano, enchendo memória e tmp/.
            for task in downloaders:
                task.cancel()
            await asyncio.gather(*downloaders, return_exceptions=True)

        # se paramos no meio, descarta o que ficou pronto
        for payload in list(ready.values()):
            if isinstance(payload, dict):
                self._cleanup(payload.get("plan", []))
        ready.clear()

    def _is_duplicate(self, messages: List[Any], dest_id: int) -> bool:
        """True quando o conteúdo (mídia/texto) já foi enviado para este destino."""
        if not self.cfg.use_dedup:
            return False
        return any(self.dedup.check(hash_message(msg), dest_id) for msg in messages)

    # ── Preparação (download) ───────────────────────────────────────────────

    async def _prepare(self, index: int, item: QueueItem,
                       progress: ProgressDisplay,
                       source: Any = None, *, strong: bool = False) -> Dict[str, Any]:
        messages = item if isinstance(item, list) else [item]
        plan: List[Dict[str, Any]] = []

        for msg in messages:
            has_media, media_type, size = media_info(msg)
            native = media_type in NATIVE_MEDIA_TYPES
            raw_media = getattr(msg, "media", None)
            unsupported = (
                not has_media and raw_media is not None
                and type(raw_media).__name__ not in {"MessageMediaWebPage", "MessageMediaEmpty"}
            )
            empty = not _caption(msg) and not has_media
            entry = {
                "msg": msg, "media_type": media_type, "size": size,
                "path": None, "downloadable": bool(has_media) and not native,
                "native": native,
            }
            if unsupported or empty:
                entry["failed"] = True
                entry["unsupported"] = True
                entry["reason"] = t("copy.unsupported_media", kind=type(raw_media).__name__ if raw_media else "empty")
            plan.append(entry)

        for entry in plan:
            if not entry["downloadable"]:
                continue
            msg = entry["msg"]
            fname = media_filename(msg, entry["media_type"])
            temp_path = TEMP_DIR / f"m{msg.id}_{os.getpid()}_{int(time.time() * 1000)}_{fname}"
            task_id = progress.add_transfer(f"[cyan]↓ {Fmt.trunc(fname, 20)}")
            display = Fmt.trunc(fname, 16)
            media_type = entry["media_type"]

            def callback(current, total, _tid=task_id, _name=display, _type=media_type):
                mb = 1048576.0
                progress.update_transfer(
                    _tid,
                    int(current / total * 100) if total else 0,
                    100,
                    f"[cyan]↓ {_name} {current / mb:.1f}/{(total or 0) / mb:.1f} MB",
                )

            downloaded = None
            last_error: Optional[Exception] = None
            attempts = 5 if strong else 2
            try:
                for attempt in range(1, attempts + 1):
                    try:
                        downloaded = await self.client.download_media(
                            msg, file=str(temp_path), progress_callback=callback
                        )
                        break
                    except FloodWaitError as exc:
                        last_error = exc
                        await self._handle_flood(exc)
                    except (RPCError, OSError, ValueError) as exc:
                        # Telethon também sinaliza esgotamento interno como ValueError.
                        last_error = exc
                        logger.warning(f"Download msg {msg.id} tentativa {attempt}: {exc}")

                    if attempt == attempts:
                        break
                    if source is not None and hasattr(self.client, "get_messages"):
                        try:
                            refreshed = await self.client.get_messages(source, ids=msg.id)
                        except (RPCError, OSError, ValueError) as exc:
                            logger.warning(f"Refresh msg {msg.id} tentativa {attempt}: {exc}")
                        else:
                            if refreshed is None:
                                entry["source_missing"] = True
                                break
                            if getattr(refreshed, "media", None) is not None:
                                msg = refreshed
                                entry["msg"] = refreshed
                    await asyncio.sleep(2 ** attempt if strong else 1.5)
            finally:
                progress.remove_transfer(task_id)
                if not downloaded:
                    try:
                        temp_path.unlink(missing_ok=True)
                    except OSError as exc:
                        logger.warning(f"Limpeza do download incompleto {msg.id}: {exc}")

            if downloaded and os.path.exists(str(downloaded)):
                entry["path"] = str(downloaded)
            else:
                # Preserva o motivo real para checkpoint, fila e eventual reserva.
                detail = self._explain(last_error) if last_error else t("copy.download_unavailable")
                logger.warning(f"Mídia indisponível (msg {msg.id}): {detail}")
                entry["downloadable"] = False
                entry["failed"] = True
                entry["reason"] = t("copy.download_failed", reason=detail)

        return {"index": index, "plan": plan, "messages": messages}

    def _placeholder_caption(self, msg) -> Tuple[str, str]:
        """Legenda final completa para guardar; o aviso pode não caber junto."""
        text = _caption(msg)
        if self.cfg.signature:
            text = f"{text}\n\n{self.cfg.signature}" if text else self.cfg.signature
        return text, _entities_json(msg)

    async def _reserve_placeholder(
        self, entry: Dict[str, Any], source_id: int, dest_id: int,
        dest, dest_topic: Optional[int], reply_to: Optional[int],
        stats: TransferStats, reason: str,
    ) -> Optional[int]:
        """Cria uma reserva na posição atual; nunca registra conteúdo no dedup."""
        msg = entry["msg"]
        if self.repo.get_placeholder(source_id, dest_id, msg.id) is not None:
            return None
        caption, entities_json = self._placeholder_caption(msg)
        unsupported = bool(entry.get("unsupported"))
        key = "placeholder.unrecoverable" if unsupported else "placeholder.pending"
        warning = t(key, id=msg.id, reason=reason)
        if utf16_len(warning) > CAPTION_LIMIT:
            warning = warning[:_cut_index(warning, CAPTION_LIMIT)]
        combined = f"{caption}\n\n{warning}" if caption else warning
        if caption and utf16_len(combined) <= CAPTION_LIMIT:
            text, entities = combined, _entities(msg) or None
        else:
            text, entities = warning, None
        try:
            created_id = None
            for attempt in range(3):
                try:
                    self._check_limit()
                    created_id = await self._send_chunk(dest, text, entities, dest_topic, reply_to)
                    break
                except FloodWaitError as exc:
                    if attempt == 2:
                        raise
                    await self._handle_flood(exc)
            if created_id is None:
                raise RuntimeError(t("copy.send_unconfirmed"))
            self.repo.add_placeholder(
                source_id, dest_id, msg.id, created_id, dest_topic=dest_topic,
                caption=caption, entities=entities_json, reason=reason,
                state="unrecoverable" if unsupported else "open",
            )
            daily_limit.add(1)
            stats.placeholders += 1
            return int(created_id)
        except DailyLimitReached:
            raise
        except Exception as exc:
            logger.error(f"Reserva de posição msg {msg.id}: {exc}")
            if self._is_fatal_send_error(exc):
                self._stop = True
            return None

    async def _record_failed_entry(
        self, entry: Dict[str, Any], source_id: int, dest_id: int,
        dest, dest_topic: Optional[int], reply_to: Optional[int],
        stats: TransferStats, payload: Dict[str, Any], reason: str,
    ) -> None:
        msg = entry["msg"]
        dest_msg_id = None
        if self.cfg.placeholder:
            dest_msg_id = await self._reserve_placeholder(
                entry, source_id, dest_id, dest, dest_topic, reply_to, stats, reason
            )
        stats.errors += 1
        if entry.get("unsupported"):
            stats.unsupported += 1
        if stats.first_error is None:
            stats.first_error = reason
        self.repo.upsert_checkpoint(source_id, dest_id, msg.id, Status.ERROR,
                                    dest_msg_id=dest_msg_id, dest_topic=dest_topic)
        self.repo.add_failure(source_id, dest_id, msg.id, reason)
        payload.setdefault("reserved_ids", set()).add(msg.id)

    def _record_delivered(
        self, entry: Dict[str, Any], source_id: int, dest_id: int,
        dest_msg_id: int, dest_topic: Optional[int], stats: TransferStats,
        *, count_daily: bool = True,
    ) -> None:
        """Único registro de conteúdo realmente entregue (envio ou edição)."""
        msg = entry["msg"]
        content_hash = hash_message(msg)
        size = entry["size"] if entry.get("path") else 0
        self.dedup.register(content_hash, dest_id, dest_msg_id, size, source_id)
        self.repo.mark_processed(source_id, msg.id, content_hash, dest_id)
        self.repo.mark_done(source_id, dest_id, msg.id, content_hash, size,
                            entry["media_type"], dest_msg_id=dest_msg_id,
                            dest_topic=dest_topic)
        self.repo.clear_failure(source_id, dest_id, msg.id)
        stats.done += 1
        if count_daily:
            daily_limit.add(1)

    # ── Envio ───────────────────────────────────────────────────────────────

    async def _send(self, payload: Dict[str, Any], source_id: int, dest_id: int,
                    stats: TransferStats, progress: ProgressDisplay) -> None:
        plan: List[Dict[str, Any]] = payload["plan"]
        dest = payload["dest"]
        dest_topic = payload.get("dest_topic")
        reply_to = payload.get("reply_to")

        media_entries = [e for e in plan if e["path"] and not e.get("failed")]
        native_entries = [e for e in plan if e.get("native") and not e.get("failed")]
        failed_entries = [e for e in plan if e.get("failed")]
        text_entries = [
            e for e in plan
            if not e["path"] and not e.get("failed") and not e.get("native")
            and _caption(e["msg"])
        ]
        sent_ids: Dict[int, int] = {}

        try:
            if len(media_entries) > 1:
                ids = await self._send_album(media_entries, dest, dest_topic, progress, stats, reply_to)
                for entry, created_id in zip(media_entries, ids):
                    sent_ids[entry["msg"].id] = created_id
            elif len(media_entries) == 1:
                entry = media_entries[0]
                sent_ids[entry["msg"].id] = await self._send_media(
                    entry, dest, dest_topic, progress, stats, reply_to
                )

            for entry in native_entries:
                sent_ids[entry["msg"].id] = await self._send_native(
                    entry, dest, dest_topic, reply_to, stats
                )

            for entry in text_entries:
                sent_ids[entry["msg"].id] = await self._send_text(
                    entry["msg"], dest, dest_topic, reply_to, entry=entry
                )
        except DailyLimitReached:
            raise
        except Exception as exc:
            # Só reserva quem ainda não tem ID confirmado. A exceção original
            # continua governando FloodWait/parada fatal no sender.
            if self.cfg.placeholder:
                reason = self._explain(exc)
                for entry in plan:
                    msg = entry["msg"]
                    if entry.get("failed") or entry.get("dest_msg_id") or msg.id in sent_ids:
                        continue
                    entry["failed"] = True
                    entry["reason"] = reason
                    await self._record_failed_entry(
                        entry, source_id, dest_id, dest, dest_topic, reply_to,
                        stats, payload, reason,
                    )
            raise

        for entry in plan:
            if entry.get("failed"):
                continue
            msg = entry["msg"]
            created_id = sent_ids.get(msg.id) or entry.get("dest_msg_id")
            if created_id is None:
                raise RuntimeError(t("copy.send_unconfirmed"))
            self._record_delivered(entry, source_id, dest_id, created_id, dest_topic, stats)

        if len(media_entries) > 1:
            stats.albums += 1

        for entry in failed_entries:
            reason = entry.get("reason") or t("copy.prepare_failure")
            await self._record_failed_entry(
                entry, source_id, dest_id, dest, dest_topic, reply_to,
                stats, payload, reason,
            )

    def _parts(self, msg, limit: int) -> List[Tuple[str, List[Any]]]:
        text = _caption(msg)
        entities = _entities(msg) or []
        if self.cfg.signature:
            signed = add_signature(text, self.cfg.signature)
            # A assinatura pode truncar a legenda; não reaproveitar entidades
            # que apontariam para offsets fora do texto truncado.
            if signed != text:
                entities = []
            text = signed
        return text_chunks(text, entities, limit)

    @staticmethod
    def _reply_header(dest_topic: Optional[int], reply_to: Optional[int]):
        if reply_to is not None:
            return types.InputReplyToMessage(
                reply_to_msg_id=int(reply_to),
                top_msg_id=int(dest_topic) if dest_topic and dest_topic != 1 else None,
            )
        if dest_topic and dest_topic != 1:
            return types.InputReplyToMessage(
                reply_to_msg_id=int(dest_topic), top_msg_id=int(dest_topic),
            )
        return None

    @staticmethod
    def _needs_raw_reply(dest_topic: Optional[int], reply_to: Optional[int]) -> bool:
        # Em fóruns usamos MTProto direto para garantir top_msg_id, inclusive
        # na primeira mensagem do tópico. Replies fora de fórum também podem
        # usar o request direto sem perder o cabeçalho.
        return bool(dest_topic) or reply_to is not None

    @staticmethod
    def _created_id(result) -> int:
        created_id = getattr(result, "id", None)
        if not created_id:
            raise RuntimeError(t("copy.send_unconfirmed"))
        return int(created_id)

    def _media_options(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        msg = entry["msg"]
        media_type = entry["media_type"]
        size = os.path.getsize(entry["path"]) if entry.get("path") else 0
        options: Dict[str, Any] = {}
        if media_type == "photo":
            options["force_document"] = size > 10 * 1024 * 1024
            if options["force_document"]:
                options["attributes"] = [
                    types.DocumentAttributeFilename(original_filename(msg, media_type))
                ]
                options["mime_type"] = "image/jpeg"
            return options
        doc = getattr(getattr(msg, "media", None), "document", None)
        attrs = list(getattr(doc, "attributes", []) or [])
        if not any(type(a).__name__ == "DocumentAttributeFilename" for a in attrs):
            attrs.append(types.DocumentAttributeFilename(original_filename(msg, media_type)))
        options["attributes"] = attrs
        options["mime_type"] = getattr(doc, "mime_type", None) or None
        options["force_document"] = media_type == "document"
        for attr in attrs:
            name = type(attr).__name__
            if name == "DocumentAttributeVideo":
                options["supports_streaming"] = bool(getattr(attr, "supports_streaming", False))
                options["video_note"] = bool(getattr(attr, "round_message", False))
            elif name == "DocumentAttributeAudio":
                options["voice_note"] = bool(getattr(attr, "voice", False))
        return options

    async def _upload_media(self, entry: Dict[str, Any], progress_callback=None):
        msg = entry["msg"]
        media_type = entry["media_type"]
        path = entry["path"]
        uploaded = await self.client.upload_file(
            path, file_name=original_filename(msg, media_type),
            progress_callback=progress_callback,
        )
        media = getattr(msg, "media", None)
        spoiler = bool(getattr(media, "spoiler", False))
        size = os.path.getsize(path) if os.path.exists(path) else 0
        if media_type == "photo" and size <= 10 * 1024 * 1024:
            return types.InputMediaUploadedPhoto(file=uploaded, spoiler=spoiler)
        options = self._media_options(entry)
        return types.InputMediaUploadedDocument(
            file=uploaded,
            mime_type=options.get("mime_type") or "application/octet-stream",
            attributes=options.get("attributes") or [],
            force_file=bool(options.get("force_document")),
            spoiler=spoiler,
        )

    async def _send_raw_media(self, entry: Dict[str, Any], dest, dest_topic,
                              reply_to, caption: str, entities, progress_callback=None) -> int:
        peer = await self.client.get_input_entity(dest)
        input_media = await self._upload_media(entry, progress_callback)
        random_id = generate_random_long()
        request = functions.messages.SendMediaRequest(
            peer=peer, media=input_media, message=caption or "",
            entities=entities or None, reply_to=self._reply_header(dest_topic, reply_to),
            random_id=random_id,
        )
        response = await self.client(request)
        parsed = self.client._get_response_message(random_id, response, peer)
        return self._created_id(parsed)

    async def _send_media(self, entry: Dict[str, Any], dest, dest_topic,
                          progress: ProgressDisplay, stats: TransferStats,
                          reply_to: Optional[int] = None) -> int:
        msg = entry["msg"]
        path = entry["path"]
        size = os.path.getsize(path) if os.path.exists(path) else 0
        parts = self._parts(msg, CAPTION_LIMIT)
        caption, cap_entities = parts[0] if parts else ("", [])
        fname = original_filename(msg, entry["media_type"])
        task_id = progress.add_transfer(f"[blue]↑ {Fmt.trunc(fname, 20)}")

        def callback(current, total, _tid=task_id, _name=Fmt.trunc(fname, 16)):
            mb = 1048576.0
            progress.update_transfer(
                _tid, int(current / total * 100) if total else 0, 100,
                f"[blue]↑ {_name} {current / mb:.1f}/{(total or 0) / mb:.1f} MB",
            )

        try:
            if self._needs_raw_reply(dest_topic, reply_to):
                created_id = await self._send_raw_media(
                    entry, dest, dest_topic, reply_to, caption, cap_entities, callback
                )
            else:
                result = await self.client.send_file(
                    dest, file=path, caption=caption or None,
                    formatting_entities=cap_entities or None,
                    reply_to=reply_to if reply_to is not None else dest_topic,
                    progress_callback=callback, **self._media_options(entry),
                )
                created_id = self._created_id(result)
        finally:
            progress.remove_transfer(task_id)

        entry["dest_msg_id"] = created_id
        for chunk, entities in parts[1:]:
            await self._send_chunk(dest, chunk, entities, dest_topic, None)
        stats.bytes_uploaded += size
        return created_id

    async def _album_input_media(self, entry: Dict[str, Any], peer):
        """Álbuns exigem mídia já registrada no peer (UploadMedia), não upload cru."""
        uploaded = await self._upload_media(entry)
        response = await self.client(functions.messages.UploadMediaRequest(
            peer=peer, media=uploaded,
        ))
        return tl_utils.get_input_media(response)

    async def _send_album(self, entries: List[Dict[str, Any]], dest, dest_topic,
                          progress: ProgressDisplay, stats: TransferStats,
                          reply_to: Optional[int] = None) -> List[int]:
        task_id = progress.add_transfer(f"[magenta]↑ álbum ({len(entries)} itens)")
        created_ids: List[int] = []
        try:
            peer = await self.client.get_input_entity(dest)
            for start in range(0, len(entries), 10):
                chunk = entries[start:start + 10]
                multi_media = []
                random_ids = []
                for entry in chunk:
                    parts = self._parts(entry["msg"], CAPTION_LIMIT)
                    caption, entities = parts[0] if parts else ("", [])
                    input_media = await self._album_input_media(entry, peer)
                    random_id = generate_random_long()
                    random_ids.append(random_id)
                    multi_media.append(types.InputSingleMedia(
                        media=input_media, random_id=random_id,
                        message=caption or "", entities=entities or None,
                    ))
                response = await self.client(functions.messages.SendMultiMediaRequest(
                    peer=peer, multi_media=multi_media,
                    reply_to=self._reply_header(dest_topic, reply_to),
                ))
                parsed = self.client._get_response_message(random_ids, response, peer)
                if not isinstance(parsed, list) or len(parsed) != len(chunk):
                    raise RuntimeError(t("copy.send_unconfirmed"))
                batch_ids = [self._created_id(item) for item in parsed]
                created_ids.extend(batch_ids)
                for entry, created_id in zip(chunk, batch_ids):
                    entry["dest_msg_id"] = created_id
        finally:
            progress.remove_transfer(task_id)

        # Legendas acima do limite viram continuações no mesmo tópico, sem
        # perder texto nem formatação.
        for entry in entries:
            parts = self._parts(entry["msg"], CAPTION_LIMIT)
            for chunk, entities in parts[1:]:
                await self._send_chunk(dest, chunk, entities, dest_topic, None)
        stats.bytes_uploaded += sum(
            os.path.getsize(e["path"]) for e in entries if os.path.exists(e["path"])
        )
        return created_ids

    async def _send_chunk(self, dest, text: str, entities, dest_topic,
                          reply_to: Optional[int]) -> int:
        if self._needs_raw_reply(dest_topic, reply_to):
            peer = await self.client.get_input_entity(dest)
            random_id = generate_random_long()
            response = await self.client(functions.messages.SendMessageRequest(
                peer=peer, message=text, entities=entities or None,
                reply_to=self._reply_header(dest_topic, reply_to), random_id=random_id,
            ))
            parsed = self.client._get_response_message(random_id, response, peer)
            return self._created_id(parsed)
        result = await self.client.send_message(
            dest, text, formatting_entities=entities or None,
            reply_to=reply_to if reply_to is not None else dest_topic,
        )
        return self._created_id(result)

    async def _send_text(self, msg, dest, dest_topic,
                         reply_to: Optional[int] = None,
                         entry: Optional[Dict[str, Any]] = None) -> int:
        parts = self._parts(msg, MESSAGE_LIMIT)
        if not parts:
            raise RuntimeError(t("copy.unsupported_media", kind="empty"))
        first_id = None
        for index, (chunk, entities) in enumerate(parts):
            created_id = await self._send_chunk(
                dest, chunk, entities, dest_topic, reply_to if index == 0 else None
            )
            if first_id is None:
                first_id = created_id
                if entry is not None:
                    entry["dest_msg_id"] = created_id
        return int(first_id)

    def _native_input_media(self, msg):
        media = msg.media
        name = type(media).__name__
        if name == "MessageMediaContact":
            return types.InputMediaContact(
                phone_number=media.phone_number, first_name=media.first_name,
                last_name=media.last_name, vcard=getattr(media, "vcard", "") or "",
            )
        if name == "MessageMediaGeo":
            return types.InputMediaGeoPoint(geo_point=tl_utils.get_input_geo(media.geo))
        if name == "MessageMediaVenue":
            return types.InputMediaVenue(
                geo_point=tl_utils.get_input_geo(media.geo), title=media.title,
                address=media.address, provider=media.provider,
                venue_id=media.venue_id, venue_type=getattr(media, "venue_type", "") or "",
            )
        if name == "MessageMediaPoll":
            poll = media.poll
            results = getattr(media, "results", None)
            question = getattr(poll, "question", "")
            answers = []
            correct = []
            for index, answer in enumerate(poll.answers):
                option = getattr(answer, "option", None) or bytes([index])
                text = getattr(answer, "text", "")
                answers.append(types.PollAnswer(text=text, option=option))
                if results and any(
                    getattr(r, "option", None) == option and getattr(r, "correct", False)
                    for r in (getattr(results, "results", None) or [])
                ):
                    correct.append(option)
            new_poll = types.Poll(
                id=0, question=question, answers=answers, hash=0,
                closed=bool(getattr(poll, "closed", False)),
                public_voters=bool(getattr(poll, "public_voters", False)),
                multiple_choice=bool(getattr(poll, "multiple_choice", False)),
                quiz=bool(getattr(poll, "quiz", False)),
            )
            return types.InputMediaPoll(
                poll=new_poll, correct_answers=correct or None,
                solution=getattr(results, "solution", None),
                solution_entities=getattr(results, "solution_entities", None),
            )
        raise RuntimeError(t("copy.unsupported_media", kind=name))

    async def _send_native(self, entry: Dict[str, Any], dest, dest_topic,
                           reply_to: Optional[int], stats: TransferStats) -> int:
        msg = entry["msg"]
        input_media = self._native_input_media(msg)
        parts = self._parts(msg, CAPTION_LIMIT)
        caption, entities = parts[0] if parts else ("", [])
        if type(msg.media).__name__ == "MessageMediaPoll":
            logger.warning(
                "Enquete recriada como nova votação; votos/resultado do original não são transferíveis."
            )
        if dest_topic:
            peer = await self.client.get_input_entity(dest)
            random_id = generate_random_long()
            response = await self.client(functions.messages.SendMediaRequest(
                peer=peer, media=input_media, message=caption or "",
                entities=entities or None, reply_to=self._reply_header(dest_topic, reply_to),
                random_id=random_id,
            ))
            parsed = self.client._get_response_message(random_id, response, peer)
            created_id = self._created_id(parsed)
        else:
            result = await self.client.send_file(
                dest, file=input_media, caption=caption or None,
                formatting_entities=entities or None, reply_to=reply_to,
            )
            created_id = self._created_id(result)
        entry["dest_msg_id"] = created_id
        for chunk, chunk_entities in parts[1:]:
            await self._send_chunk(dest, chunk, chunk_entities, dest_topic, None)
        return created_id


    # ── Recuperação por edição ────────────────────────────────────────────────

    async def _edit_placeholder_message(
        self, row: Dict[str, Any], dest, text: str, entities,
        entry: Optional[Dict[str, Any]] = None, file: Optional[str] = None,
    ) -> int:
        options: Dict[str, Any] = {}
        if file and entry is not None:
            media_options = self._media_options(entry)
            options = {key: media_options[key]
                       for key in ("attributes", "force_document", "supports_streaming")
                       if key in media_options}
        result = await self.client.edit_message(
            dest, int(row["dest_msg_id"]), text=text, file=file,
            formatting_entities=list(entities or []), parse_mode=None,
            link_preview=False, **options,
        )
        edited_id = getattr(result, "id", None)
        if edited_id is None or int(edited_id) != int(row["dest_msg_id"]):
            raise RuntimeError(t("copy.send_unconfirmed"))
        return int(edited_id)

    async def _finish_placeholder_unrecoverable(
        self, row: Dict[str, Any], dest, reason: str,
    ) -> str:
        source_id, dest_id, msg_id = int(row["source_id"]), int(row["dest_id"]), int(row["msg_id"])
        caption = row.get("caption") or ""
        warning = t("placeholder.unrecoverable", id=msg_id, reason=reason)
        text = f"{caption}\n\n{warning}" if caption else warning
        if utf16_len(text) > MESSAGE_LIMIT:
            text = warning
        if utf16_len(text) > MESSAGE_LIMIT:
            text = text[:_cut_index(text, MESSAGE_LIMIT)]
        try:
            await self._edit_placeholder_message(row, dest, text, [])
        except FloodWaitError as exc:
            await self._handle_flood(exc)
            return "failed"
        except MessageNotModifiedError:
            pass
        except (MessageEditTimeExpiredError, MessageAuthorRequiredError,
                MessageIdInvalidError, ChatWriteForbiddenError) as exc:
            logger.error(f"Edição definitiva indisponível msg {msg_id}: {exc}")
        except Exception as exc:
            logger.error(f"Aviso final da reserva msg {msg_id} não editado: {exc}")
            return "failed"
        self.repo.set_placeholder_state(source_id, dest_id, msg_id, "unrecoverable", reason)
        self.repo.clear_failure(source_id, dest_id, msg_id)
        return "unrecoverable"

    async def _deliver_placeholder(
        self, source, dest, row: Dict[str, Any], stats: TransferStats,
        quiet: Optional[ProgressDisplay] = None,
    ) -> str:
        """Entrega uma reserva aberta por edição; compartilhado por retry e [5]→6."""
        source_id, dest_id, msg_id = int(row["source_id"]), int(row["dest_id"]), int(row["msg_id"])
        if row.get("state") != "open":
            return "failed"
        quiet = quiet or ProgressDisplay(0, "", False)
        message = await self.client.get_messages(source, ids=msg_id)
        if message is None:
            return await self._finish_placeholder_unrecoverable(
                row, dest, t("placeholder.missing")
            )
        payload = await self._prepare(0, message, quiet, source=source, strong=True)
        try:
            entry = payload["plan"][0]
            if entry.get("failed"):
                return await self._finish_placeholder_unrecoverable(
                    row, dest, entry.get("reason") or t("copy.download_unavailable")
                )
            caption = row.get("caption")
            if caption is None:
                caption = self._placeholder_caption(message)[0]
            entities = _decode_entities(row.get("entities"))
            file = entry.get("path")
            limit = CAPTION_LIMIT if file else MESSAGE_LIMIT
            parts = text_chunks(caption, entities, limit) or [("", [])]
            text, first_entities = parts[0]
            try:
                await self._edit_placeholder_message(
                    row, dest, text, first_entities, entry=entry, file=file
                )
                for chunk, chunk_entities in parts[1:]:
                    self._check_limit()
                    await self._send_chunk(dest, chunk, chunk_entities, row.get("dest_topic"), None)
                    daily_limit.add(1)
            except DailyLimitReached:
                raise
            except FloodWaitError as exc:
                await self._handle_flood(exc)
                return "failed"
            except MessageNotModifiedError as exc:
                logger.warning(f"Reserva msg {msg_id} não modificada: {exc}")
                return "failed"
            except (MessageEditTimeExpiredError, MessageAuthorRequiredError,
                    MessageIdInvalidError, ChatWriteForbiddenError) as exc:
                reason = self._explain(exc)
                logger.error(f"Edição definitiva indisponível msg {msg_id}: {exc}")
                self.repo.set_placeholder_state(source_id, dest_id, msg_id, "unrecoverable", reason)
                self.repo.clear_failure(source_id, dest_id, msg_id)
                return "unrecoverable"
            except Exception as exc:
                logger.error(f"Edição da reserva msg {msg_id}: {exc}")
                return "failed"
            if file and os.path.exists(file):
                stats.bytes_uploaded += os.path.getsize(file)
            self._record_delivered(
                entry, source_id, dest_id, int(row["dest_msg_id"]),
                row.get("dest_topic"), stats, count_daily=False,
            )
            self.repo.set_placeholder_state(source_id, dest_id, msg_id, "recovered")
            return "recovered"
        finally:
            self._cleanup(payload.get("plan", []))

    async def recover_placeholders(
        self, source, dest, cfg: Optional[RunConfig] = None, quiet: bool = False,
    ) -> Dict[str, int]:
        if cfg is not None:
            self.cfg = cfg
            self.dedup = DedupEngine(self.repo, enabled=self.cfg.use_dedup)
        source_id = int(getattr(source, "id", 0) or 0)
        dest_id = int(getattr(dest, "id", 0) or 0)
        self.dedup.warm_up(dest_id, limit=200_000)
        rows = self.repo.open_placeholders(source_id, dest_id)
        stats = TransferStats(planned=len(rows))
        progress = ProgressDisplay(0, "", False if quiet else self.rich_ok)
        result = {"recovered": 0, "unrecoverable": 0, "failed": 0}
        for row in rows:
            try:
                outcome = await self._deliver_placeholder(source, dest, row, stats, progress)
                result[outcome] += 1
            except DailyLimitReached as exc:
                logger.warning(f"Recuperação interrompida: {exc}")
                result["failed"] += 1
                break
            except Exception as exc:
                logger.error(f"Recuperação msg {row.get('msg_id')}: {exc}")
                result["failed"] += 1
            await asyncio.sleep(self.delay)
        return result

    # ── Reprocessar falhas ──────────────────────────────────────────────────

    async def retry_messages(self, source, dest, msg_ids: List[int],
                             dest_topic: Optional[int] = None) -> Dict[str, Any]:
        """Reprocessa mensagens específicas que falharam antes."""
        source_id = int(getattr(source, "id", 0) or 0)
        dest_id = int(getattr(dest, "id", 0) or 0)
        stats = TransferStats(planned=len(msg_ids))
        self.dedup.warm_up(dest_id, limit=200_000)
        quiet = ProgressDisplay(0, "", False)

        for msg_id in msg_ids:
            try:
                placeholder = self.repo.get_placeholder(source_id, dest_id, msg_id)
                if placeholder is not None:
                    if placeholder.get("state") == "open":
                        outcome = await self._deliver_placeholder(source, dest, placeholder, stats, quiet)
                        if outcome != "recovered":
                            stats.errors += 1
                            if stats.first_error is None:
                                stats.first_error = placeholder.get("reason") or t("copy.prepare_failure")
                    else:
                        stats.skipped += 1
                    await asyncio.sleep(self.delay)
                    continue
                message = await self.client.get_messages(source, ids=msg_id)
                if message is None:
                    self.repo.clear_failure(source_id, dest_id, msg_id)
                    stats.skipped += 1
                    continue
                route = self.repo.checkpoint_route(source_id, dest_id, msg_id)
                target_topic = dest_topic if dest_topic is not None else route.get("dest_topic")
                roots = {self._source_topic_id(message, set()) or 1}
                reply_to = self._reply_target(message, source_id, dest_id, {root: root for root in roots})
                payload = await self._prepare(0, message, quiet, source=source)
                payload.update({"dest": dest, "dest_topic": target_topic, "reply_to": reply_to})
                errors_before = stats.errors
                try:
                    self._check_limit()
                    await self._send(payload, source_id, dest_id, stats, quiet)
                finally:
                    self._cleanup(payload.get("plan", []))
                if stats.errors == errors_before:
                    self.repo.clear_failure(source_id, dest_id, msg_id)
                await asyncio.sleep(self.delay)
            except DailyLimitReached as exc:
                stats.first_error = str(exc)
                stats.interrupted = True
                break
            except FloodWaitError as exc:
                await self._handle_flood(exc)
            except Exception as exc:
                logger.error(f"Retry msg {msg_id}: {exc}")
                self.repo.add_failure(source_id, dest_id, msg_id, self._explain(exc))
                stats.errors += 1
        return stats.as_result()

    # ── Espelho em tempo real ───────────────────────────────────────────────

    async def _mirror_topic(self, msg, dest_topic: Optional[int],
                            topic_map: Optional[Dict[int, int]],
                            ensure_topic=None) -> Optional[int]:
        """Tópico do destino para uma mensagem nova do fórum de origem."""
        if not topic_map:
            return dest_topic
        source_topic = self._source_topic_id(msg, set(topic_map)) or 1
        target = topic_map.get(source_topic)
        if target:
            return target
        if ensure_topic is not None and source_topic != 1:
            # Tópico criado enquanto o espelho estava parado: cria agora.
            created = await ensure_topic(msg)
            if created:
                topic_map[source_topic] = int(created)
                return int(created)
        raise RuntimeError(t("copy.topic_unmapped", topic=source_topic))

    async def mirror(self, source, dest, filter_code: str = "all",
                     source_topic: Optional[int] = None,
                     dest_topic: Optional[int] = None,
                     topic_map: Optional[Dict[int, int]] = None,
                     ensure_topic=None) -> None:
        """Espelha mensagens novas; com ``topic_map`` roteia cada tópico.

        ``ensure_topic`` é chamado quando aparece um tópico que ainda não está
        no mapa (inclusive o serviço ``MessageActionTopicCreate``) e devolve o
        ID do tópico criado no destino.
        """
        source_id = int(getattr(source, "id", 0) or 0)
        dest_id = int(getattr(dest, "id", 0) or 0)
        self.dedup.warm_up(dest_id, limit=200_000)
        stats = TransferStats()

        print(f"\n  {C.GREEN}✓{C.RST}  {t('mirror.waiting')}")
        print(f"  {C.GRAY}{t('mirror.stop')}{C.RST}\n")

        @self.client.on(events.NewMessage(chats=[source]))
        async def handler(event):  # noqa: ANN001
            msg = event.message
            payload = None
            try:
                if type(getattr(msg, "action", None)).__name__ == "MessageActionTopicCreate":
                    # Tópico novo na origem: replica a estrutura antes de
                    # qualquer mensagem que chegue dentro dele.
                    if topic_map is not None and ensure_topic is not None:
                        created = await ensure_topic(msg)
                        if created:
                            topic_map[int(msg.id)] = int(created)
                    return
                roots = {int(source_topic)} if source_topic else set()
                if source_topic and self._source_topic_id(msg, roots) != int(source_topic):
                    return
                if is_service(msg) or not filter_apply(filter_code, msg):
                    return

                content_hash = hash_message(msg)
                if self.cfg.use_dedup and self.dedup.check(content_hash, dest_id):
                    stats.skipped += 1
                    return

                silent = ProgressDisplay(0, "", False)
                payload = await self._prepare(0, msg, silent, source=source)
                route_roots = set(topic_map or {})
                if source_topic:
                    route_roots.add(int(source_topic))
                payload.update({
                    "dest": dest,
                    "dest_topic": await self._mirror_topic(
                        msg, dest_topic, topic_map, ensure_topic
                    ),
                    "reply_to": self._reply_target(
                        msg, source_id, dest_id,
                        {root: root for root in route_roots},
                    ),
                })
                try:
                    await self._send(payload, source_id, dest_id, stats, silent)
                finally:
                    self._cleanup(payload.get("plan", []))

                print(f"  {C.GREEN}›{C.RST} {t('mirror.new_msg', id=msg.id)}  "
                      f"{C.GRAY}({stats.done} {t('mirror.sent')}){C.RST}")
            except DailyLimitReached as exc:
                print(f"\n  {C.YELLOW}!{C.RST}  {exc}", flush=True)
            except FloodWaitError as exc:
                await self._handle_flood(exc)
            except Exception as exc:
                already_recorded = bool(payload and msg.id in payload.get("reserved_ids", set()))
                if not already_recorded:
                    stats.errors += 1
                logger.error(f"Espelho msg {msg.id}: {exc}")
                print(f"  {C.RED}✗{C.RST} ID {msg.id}: {self._explain(exc)}")

        try:
            await self.client.run_until_disconnected()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            self.client.remove_event_handler(handler)
        print(f"\n  {C.GRAY}{t('mirror.stopped')} — {stats.done} {t('mirror.sent')}{C.RST}")

    # ── Auxiliares ──────────────────────────────────────────────────────────

    def _mark_done(self, msg, source_id: int, dest_id: int, stats: TransferStats,
                   dest_msg_id: Optional[int] = None,
                   dest_topic: Optional[int] = None) -> None:
        content_hash = hash_message(msg)
        self.repo.mark_done(source_id, dest_id, msg.id, content_hash,
                            dest_msg_id=dest_msg_id, dest_topic=dest_topic)
        self.repo.mark_processed(source_id, msg.id, content_hash, dest_id)
        self.repo.clear_failure(source_id, dest_id, msg.id)
        self.dedup.register(content_hash, dest_id, dest_msg_id or msg.id, 0, source_id)
        stats.done += 1
        daily_limit.add(1)

    def _mark_failed(self, messages, source_id: int, dest_id: int,
                     stats: TransferStats, exc: Exception,
                     dest_topic: Optional[int] = None,
                     skip_ids: Optional[set] = None) -> None:
        reason = self._explain(exc)
        if stats.first_error is None:
            stats.first_error = reason
        logger.error(f"Falha ao copiar {len(messages)} msg(s): {exc}")
        for msg in messages:
            if skip_ids and msg.id in skip_ids:
                continue
            stats.errors += 1
            self.repo.upsert_checkpoint(source_id, dest_id, msg.id, Status.ERROR,
                                        dest_topic=dest_topic)
            self.repo.add_failure(source_id, dest_id, msg.id, reason)

    def _check_limit(self) -> None:
        level, _, _ = daily_limit.status()
        if level == "limit":
            raise DailyLimitReached(t("copy.limit_reached", limit=DAILY_LIMIT))

    async def _handle_flood(self, exc: FloodWaitError) -> None:
        wait = int(getattr(exc, "seconds", 30) or 30) + 3
        self._flood_penalty = min(self._flood_penalty + 1.5, 20.0)
        logger.warning(f"FloodWait {wait}s (penalidade {self._flood_penalty:.1f}s)")
        print(f"\n  {C.YELLOW}!{C.RST}  {t('progress.flood', sec=wait)}", flush=True)
        await asyncio.sleep(wait)

    @staticmethod
    def _explain(exc: Exception) -> str:
        name = type(exc).__name__
        if isinstance(exc, FloodWaitError):
            return t("progress.flood", sec=int(getattr(exc, "seconds", 0) or 0))
        if isinstance(exc, ChatAdminRequiredError):
            return t("err.need_admin")
        if isinstance(exc, ChannelPrivateError):
            return t("err.channel_private")
        if name == "ChatForwardsRestrictedError":
            return t("err.forward_restricted")
        if name in {
            "ChatForbiddenError",
            "ChatSendMediaForbiddenError",
            "ChatSendPlainForbiddenError",
            "ChatWriteForbiddenError",
            "UserBannedInChannelError",
            "UserNotParticipantError",
        }:
            return t("err.send_forbidden")
        return str(exc)[:250] or t("err.generic")

    @staticmethod
    def _cleanup(plan: List[Dict[str, Any]]) -> None:
        for entry in plan:
            path = entry.get("path")
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass
                entry["path"] = None

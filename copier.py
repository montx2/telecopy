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
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from telethon import TelegramClient, events, functions
from telethon.errors import (
    ChatAdminRequiredError,
    ChannelPrivateError,
    FloodWaitError,
    RPCError,
)

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
    action = getattr(msg, "action", None)
    return bool(action) and type(action).__name__ in SERVICE_ACTIONS


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
        if mime.startswith("image/"):
            return True, "photo", size
        return True, "document", size

    if name == "MessageMediaWebPage":
        # link preview não é mídia transferível
        return False, "text", 0
    if name == "MessageMediaPoll":
        return False, "poll", 0
    if name in ("MessageMediaGeo", "MessageMediaGeoLive", "MessageMediaVenue",
                "MessageMediaContact", "MessageMediaDice"):
        return False, "other", 0
    return False, "other", 0


def media_filename(msg, media_type: str) -> str:
    file_obj = getattr(msg, "file", None)
    if file_obj is not None and getattr(file_obj, "name", None):
        clean = _SAFE_NAME.sub("_", str(file_obj.name)).strip(" ._")
        if clean:
            return clean[:110]
    ext = {"photo": "jpg", "video": "mp4", "audio": "mp3",
           "sticker": "webp", "document": "bin"}.get(media_type, "bin")
    return f"{media_type}_{getattr(msg, 'id', 0)}.{ext}"


def _caption(msg) -> str:
    return getattr(msg, "message", "") or ""


def _entities(msg):
    return getattr(msg, "entities", None)


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
    ) -> Dict[str, Any]:
        source_id = int(getattr(source, "id", 0) or 0)
        dest_id = int(getattr(dest, "id", 0) or 0)
        resume = self.cfg.resume if resume is None else resume
        forward = self.cfg.forward_mode if forward is None else forward
        sess = session_id or f"{source_id}:{dest_id}:{int(time.time())}"

        if getattr(source, "noforwards", False):
            blocked = TransferStats(errors=1, first_error=t("copy.source_protected"))
            print(f"\n  {C.RED}✗{C.RST}  {blocked.first_error}", flush=True)
            return blocked.as_result()

        destination_error = await self._destination_error(dest)
        if destination_error:
            blocked = TransferStats(errors=1, first_error=destination_error)
            print(f"\n  {C.RED}✗{C.RST}  "
                  f"{t('copy.first_failure', reason=destination_error)}", flush=True)
            return blocked.as_result()

        self.dedup.warm_up(dest_id, limit=200_000)

        print(f"\n  {C.CYAN}·{C.RST}  {t('copy.indexing')}…", end="", flush=True)
        messages = await self._collect(source, filter_code, source_topic)
        print(f"\r  {C.GREEN}✓{C.RST}  {Fmt.number(len(messages))} {t('copy.found')}            ")

        if not messages:
            print(f"  {C.YELLOW}!{C.RST}  {t('copy.none')}")
            return TransferStats().as_result()

        items = self._group(messages)

        if resume:
            done_ids = self.repo.done_ids(source_id, dest_id)
            if done_ids:
                before = sum(len(i) if isinstance(i, list) else 1 for i in items)
                items = [
                    item for item in items
                    if not all(m.id in done_ids
                               for m in (item if isinstance(item, list) else [item]))
                ]
                after = sum(len(i) if isinstance(i, list) else 1 for i in items)
                if before - after:
                    print(f"  {C.GRAY}·{C.RST}  {Fmt.number(before - after)} {t('copy.already')}")

        planned = sum(len(i) if isinstance(i, list) else 1 for i in items)
        if planned == 0:
            print(f"  {C.GREEN}✓{C.RST}  {t('copy.all_done')}")
            return TransferStats().as_result()

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

        stats = TransferStats(planned=planned)
        with ProgressDisplay(planned, getattr(source, "title", "") or "Copiando",
                             self.rich_ok) as progress:
            if forward:
                await self._forward_loop(items, source, dest, source_id, dest_id,
                                         dest_topic, topic_map, stats, progress)
            else:
                await self._copy_loop(items, source, dest, source_id, dest_id,
                                      dest_topic, topic_map, stats, progress)

        self.repo.update_stats(
            sess, success=stats.done, errors=stats.errors,
            duplicates=stats.duplicates, bytes_total=stats.bytes_uploaded,
        )
        self.repo.finish_session(sess)
        return stats.as_result()

    # ── Coleta / agrupamento ────────────────────────────────────────────────

    async def _collect(self, source, filter_code: str,
                       topic_id: Optional[int]) -> List[Any]:
        messages: List[Any] = []
        kwargs: Dict[str, Any] = {"reverse": True}
        if topic_id:
            kwargs["reply_to"] = topic_id
        try:
            async for msg in self.client.iter_messages(source, **kwargs):
                if self._stop:
                    break
                if is_service(msg):
                    continue
                if not filter_apply(filter_code, msg):
                    continue
                messages.append(msg)
                if len(messages) % 500 == 0:
                    print(f"\r  {C.CYAN}·{C.RST}  {Fmt.number(len(messages))} "
                          f"{t('copy.indexed')}…", end="", flush=True)
        except FloodWaitError as exc:
            logger.warning(f"FloodWait na indexação: {exc.seconds}s")
            await asyncio.sleep(int(exc.seconds) + 3)
        except RPCError as exc:
            logger.error(f"Falha ao ler a origem: {exc}")
            raise RuntimeError(self._explain(exc)) from exc
        return messages

    @staticmethod
    def _group(messages: Sequence[Any]) -> List[QueueItem]:
        albums: Dict[int, List[Any]] = defaultdict(list)
        for msg in messages:
            gid = getattr(msg, "grouped_id", None)
            if gid:
                albums[gid].append(msg)

        items: List[QueueItem] = []
        seen: set = set()
        for msg in messages:
            gid = getattr(msg, "grouped_id", None)
            if not gid:
                items.append(msg)
                continue
            if gid in seen:
                continue
            seen.add(gid)
            group = albums[gid]
            items.append(group if len(group) > 1 else group[0])
        return items

    @staticmethod
    def _source_topic_id(msg) -> Optional[int]:
        """Extrai o tópico do cabeçalho de resposta de uma mensagem de fórum."""
        reply = getattr(msg, "reply_to", None)
        top_id = getattr(reply, "reply_to_top_id", None)
        if top_id:
            return int(top_id)
        # Mensagens antigas/primeiras de um tópico podem apontar direto para
        # a mensagem-raiz sem preencher reply_to_top_id.
        reply_id = getattr(reply, "reply_to_msg_id", None)
        if reply_id:
            return int(reply_id)
        reply_id = getattr(msg, "reply_to_msg_id", None)
        return int(reply_id) if reply_id else None

    @classmethod
    def _destination_topic(cls, item, fallback: Optional[int],
                           topic_map: Optional[Dict[int, int]]) -> Optional[int]:
        if not topic_map:
            return fallback
        messages = item if isinstance(item, list) else [item]
        for msg in messages:
            source_topic = cls._source_topic_id(msg)
            if source_topic in topic_map:
                return topic_map[source_topic]
        return topic_map.get(1, fallback)

    async def _forward_item(self, source, dest, ids: List[int],
                            dest_topic: Optional[int]) -> None:
        """Encaminha para o tópico mapeado sem remover a atribuição original."""
        if not dest_topic:
            await self.client.forward_messages(dest, ids, from_peer=source)
            return

        # Telethon.forward_messages não expõe o campo top_msg_id da API. A
        # chamada MTProto abaixo é necessária para encaminhar dentro de fórum.
        from_peer = await self.client.get_input_entity(source)
        to_peer = await self.client.get_input_entity(dest)
        await self.client(functions.messages.ForwardMessagesRequest(
            from_peer=from_peer,
            id=ids,
            to_peer=to_peer,
            top_msg_id=int(dest_topic),
        ))

    # ── Encaminhamento (rápido) ─────────────────────────────────────────────

    async def _forward_loop(self, items, source, dest, source_id, dest_id,
                            dest_topic, topic_map, stats: TransferStats,
                            progress: ProgressDisplay) -> None:
        last_error_key = None
        consecutive_errors = 0
        first_failure_shown = False

        for item in items:
            if self._stop:
                break
            messages = item if isinstance(item, list) else [item]
            ids = [m.id for m in messages]

            try:
                self._check_limit()
            except RuntimeError as exc:
                stats.first_error = str(exc)
                print(f"\n  {C.YELLOW}!{C.RST}  {exc}", flush=True)
                break

            error: Optional[Exception] = None
            for retry in range(3):
                try:
                    # A lista de IDs preserva o agrupamento reconhecido pelo
                    # Telegram. `as_album` não tem mais efeito no Telethon;
                    # sem `drop_author`, o rótulo de encaminhamento é mantido.
                    item_topic = self._destination_topic(item, dest_topic, topic_map)
                    await self._forward_item(source, dest, ids, item_topic)
                    break
                except FloodWaitError as exc:
                    if retry < 2:
                        await self._handle_flood(exc)
                        continue
                    error = exc
                except Exception as exc:
                    error = exc
                break

            should_stop = False
            if error is None:
                for msg in messages:
                    self._mark_done(msg, source_id, dest_id, stats)
                if isinstance(item, list):
                    stats.albums += 1
                last_error_key = None
                consecutive_errors = 0
            else:
                self._mark_failed(messages, source_id, dest_id, stats, error)
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
            "ChatForwardsRestrictedError",
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
                        payload = await self._prepare(index, item, progress)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception(f"Falha ao preparar item {index}: {exc}")
                    payload = None
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
                    for msg in messages:
                        self.repo.update_status(source_id, dest_id, msg.id, Status.ERROR)
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

                payload.update({
                    "dest": dest,
                    "dest_topic": self._destination_topic(items[index], dest_topic, topic_map),
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
                        except Exception as exc:
                            error = exc
                        break

                    if error is not None:
                        logger.error(f"Erro no envio do item {index}: {error}")
                        self._mark_failed(payload["messages"], source_id, dest_id, stats, error)
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
                finally:
                    self._cleanup(payload.get("plan", []))

                progress.update(stats.done + stats.skipped + stats.errors,
                                self._progress_status(stats))
                if self._stop:
                    break
                await asyncio.sleep(self.delay)

        downloaders = [asyncio.create_task(downloader()) for _ in range(self.cfg.workers)]
        sender_task = asyncio.create_task(sender())
        await sender_task
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
                       progress: ProgressDisplay) -> Dict[str, Any]:
        messages = item if isinstance(item, list) else [item]
        plan: List[Dict[str, Any]] = []

        for msg in messages:
            has_media, media_type, size = media_info(msg)
            plan.append({
                "msg": msg,
                "media_type": media_type,
                "size": size,
                "path": None,
                "downloadable": bool(has_media),
            })

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
            for attempt in (1, 2):
                try:
                    downloaded = await self.client.download_media(
                        msg, file=str(temp_path), progress_callback=callback
                    )
                    break
                except FloodWaitError as exc:
                    await self._handle_flood(exc)
                except (RPCError, OSError) as exc:
                    logger.warning(f"Download msg {msg.id} tentativa {attempt}: {exc}")
                    if attempt == 2:
                        downloaded = None
                    else:
                        await asyncio.sleep(1.5)
            progress.remove_transfer(task_id)

            if downloaded and os.path.exists(str(downloaded)):
                entry["path"] = str(downloaded)
            else:
                # download falhou/expirado: será registrado como erro e reenfileirado
                logger.warning(f"Mídia indisponível (msg {msg.id})")
                entry["downloadable"] = False
                entry["failed"] = True

        return {"index": index, "plan": plan, "messages": messages}

    # ── Envio ───────────────────────────────────────────────────────────────

    async def _send(self, payload: Dict[str, Any], source_id: int, dest_id: int,
                    stats: TransferStats, progress: ProgressDisplay) -> None:
        plan: List[Dict[str, Any]] = payload["plan"]
        messages: List[Any] = payload["messages"]
        dest = payload["dest"]
        dest_topic = payload["dest_topic"]

        media_entries = [e for e in plan if e["path"]]
        failed_entries = [e for e in plan if e.get("failed")]
        text_entries = [
            e for e in plan
            if not e["path"] and not e.get("failed") and not media_info(e["msg"])[0]
        ]

        if len(media_entries) > 1:
            await self._send_album(media_entries, dest, dest_topic, progress, stats)
        elif len(media_entries) == 1:
            await self._send_media(media_entries[0], dest, dest_topic, progress, stats)

        for entry in text_entries:
            await self._send_text(entry["msg"], dest, dest_topic)

        if failed_entries:
            if stats.first_error is None:
                stats.first_error = t("copy.prepare_failure")
            for entry in failed_entries:
                msg = entry["msg"]
                stats.errors += 1
                self.repo.update_status(source_id, dest_id, msg.id, Status.ERROR)
                self.repo.add_failure(source_id, dest_id, msg.id,
                                      "download indisponível")

        for entry in plan:
            if entry.get("failed"):
                continue
            msg = entry["msg"]
            content_hash = hash_message(msg)
            size = entry["size"] if entry["path"] else 0
            self.dedup.register(content_hash, dest_id, getattr(msg, "id", 0), size, source_id)
            self.repo.mark_processed(source_id, msg.id, content_hash, dest_id)
            self.repo.mark_done(source_id, dest_id, msg.id, content_hash, size,
                                entry["media_type"])
            self.repo.clear_failure(source_id, dest_id, msg.id)

        stats.done += len(plan) - len(failed_entries)
        if len(media_entries) > 1:
            stats.albums += 1

    async def _send_media(self, entry: Dict[str, Any], dest, dest_topic,
                          progress: ProgressDisplay, stats: TransferStats) -> None:
        msg = entry["msg"]
        path = entry["path"]
        size = os.path.getsize(path) if os.path.exists(path) else 0
        caption = add_signature(_caption(msg), self.cfg.signature)
        fname = os.path.basename(path)
        task_id = progress.add_transfer(f"[blue]↑ {Fmt.trunc(fname, 20)}")

        def callback(current, total, _tid=task_id, _name=Fmt.trunc(fname, 16)):
            mb = 1048576.0
            progress.update_transfer(
                _tid,
                int(current / total * 100) if total else 0,
                100,
                f"[blue]↑ {_name} {current / mb:.1f}/{(total or 0) / mb:.1f} MB",
            )

        kwargs: Dict[str, Any] = {"caption": caption or None, "reply_to": dest_topic,
                                 "progress_callback": callback}
        media_type = entry["media_type"]
        if media_type in ("document", "sticker"):
            kwargs["force_document"] = True
        elif media_type == "photo" and size > 10 * 1024 * 1024:
            kwargs["force_document"] = True

        try:
            await self.client.send_file(dest, file=path, **kwargs)
        finally:
            progress.remove_transfer(task_id)
        stats.bytes_uploaded += size

    async def _send_album(self, entries: List[Dict[str, Any]], dest, dest_topic,
                          progress: ProgressDisplay, stats: TransferStats) -> None:
        files = [e["path"] for e in entries]
        captions = []
        for i, entry in enumerate(entries):
            base = _caption(entry["msg"])
            captions.append(add_signature(base, self.cfg.signature) if i == 0 else base)

        task_id = progress.add_transfer(f"[magenta]↑ álbum ({len(files)} itens)")

        def callback(current, total, _tid=task_id):
            progress.update_transfer(
                _tid, int(current / total * 100) if total else 0, 100,
                f"[magenta]↑ álbum ({len(files)} itens)",
            )

        try:
            await self.client.send_file(dest, file=files, caption=captions,
                                        reply_to=dest_topic, progress_callback=callback)
        finally:
            progress.remove_transfer(task_id)
        stats.bytes_uploaded += sum(os.path.getsize(f) for f in files if os.path.exists(f))

    async def _send_text(self, msg, dest, dest_topic) -> None:
        text = add_signature(_caption(msg), self.cfg.signature).strip()
        if not text:
            return
        chunks = split_text(text)
        entities = _entities(msg)
        for i, chunk in enumerate(chunks):
            await self.client.send_message(
                dest, chunk,
                formatting_entities=entities if (i == 0 and len(chunks) == 1) else None,
                reply_to=dest_topic,
            )


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
                message = await self.client.get_messages(source, ids=msg_id)
                if message is None:
                    self.repo.clear_failure(source_id, dest_id, msg_id)
                    stats.skipped += 1
                    continue
                payload = await self._prepare(0, message, quiet)
                payload.update({"dest": dest, "dest_topic": dest_topic})
                try:
                    self._check_limit()
                    await self._send(payload, source_id, dest_id, stats, quiet)
                finally:
                    self._cleanup(payload.get("plan", []))
                self.repo.clear_failure(source_id, dest_id, msg_id)
                await asyncio.sleep(self.delay)
            except FloodWaitError as exc:
                await self._handle_flood(exc)
            except Exception as exc:
                logger.error(f"Retry msg {msg_id}: {exc}")
                self.repo.add_failure(source_id, dest_id, msg_id, self._explain(exc))
                stats.errors += 1
        return stats.as_result()

    # ── Espelho em tempo real ───────────────────────────────────────────────

    async def mirror(self, source, dest, filter_code: str = "all",
                     source_topic: Optional[int] = None,
                     dest_topic: Optional[int] = None) -> None:
        source_id = int(getattr(source, "id", 0) or 0)
        dest_id = int(getattr(dest, "id", 0) or 0)
        self.dedup.warm_up(dest_id, limit=200_000)
        stats = TransferStats()

        print(f"\n  {C.GREEN}✓{C.RST}  {t('mirror.waiting')}")
        print(f"  {C.GRAY}{t('mirror.stop')}{C.RST}\n")

        @self.client.on(events.NewMessage(chats=[source]))
        async def handler(event):  # noqa: ANN001
            msg = event.message
            try:
                if source_topic and getattr(msg, "reply_to_msg_id", None) != source_topic:
                    return
                if is_service(msg) or not filter_apply(filter_code, msg):
                    return

                content_hash = hash_message(msg)
                if self.dedup.check(content_hash, dest_id):
                    stats.skipped += 1
                    return

                _, media_type, _ = media_info(msg)
                silent = ProgressDisplay(0, "", False)
                if media_type in ("text", "poll", "other"):
                    await self._send_text(msg, dest, dest_topic)
                    self.dedup.register(content_hash, dest_id, 0, 0, source_id)
                    self.repo.mark_processed(source_id, msg.id, content_hash, dest_id)
                    stats.done += 1
                else:
                    payload = await self._prepare(0, msg, silent)
                    payload.update({"dest": dest, "dest_topic": dest_topic})
                    try:
                        await self._send(payload, source_id, dest_id, stats, silent)
                    finally:
                        self._cleanup(payload.get("plan", []))

                print(f"  {C.GREEN}›{C.RST} {t('mirror.new_msg', id=msg.id)}  "
                      f"{C.GRAY}({stats.done} {t('mirror.sent')}){C.RST}")
            except FloodWaitError as exc:
                await self._handle_flood(exc)
            except Exception as exc:
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

    def _mark_done(self, msg, source_id: int, dest_id: int, stats: TransferStats) -> None:
        content_hash = hash_message(msg)
        self.repo.mark_done(source_id, dest_id, msg.id, content_hash)
        self.repo.mark_processed(source_id, msg.id, content_hash, dest_id)
        self.repo.clear_failure(source_id, dest_id, msg.id)
        self.dedup.register(content_hash, dest_id, msg.id, 0, source_id)
        stats.done += 1

    def _mark_failed(self, messages, source_id: int, dest_id: int,
                     stats: TransferStats, exc: Exception) -> None:
        reason = self._explain(exc)
        if stats.first_error is None:
            stats.first_error = reason
        logger.error(f"Falha ao copiar {len(messages)} msg(s): {exc}")
        for msg in messages:
            stats.errors += 1
            self.repo.update_status(source_id, dest_id, msg.id, Status.ERROR)
            self.repo.add_failure(source_id, dest_id, msg.id, reason)

    def _check_limit(self) -> None:
        level, _, _ = daily_limit.status()
        if level == "limit":
            raise RuntimeError(t("copy.limit_reached", limit=DAILY_LIMIT))

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

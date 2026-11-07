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

from telethon import TelegramClient, events
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

    async def run(
        self,
        source,
        dest,
        filter_code: str = "all",
        resume: Optional[bool] = None,
        source_topic: Optional[int] = None,
        dest_topic: Optional[int] = None,
        forward: Optional[bool] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        source_id = int(getattr(source, "id", 0) or 0)
        dest_id = int(getattr(dest, "id", 0) or 0)
        resume = self.cfg.resume if resume is None else resume
        forward = self.cfg.forward_mode if forward is None else forward
        sess = session_id or f"{source_id}:{dest_id}:{int(time.time())}"

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
                                         dest_topic, stats, progress)
            else:
                await self._copy_loop(items, source, dest, source_id, dest_id,
                                      dest_topic, stats, progress)

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

    # ── Encaminhamento (rápido) ─────────────────────────────────────────────

    async def _forward_loop(self, items, source, dest, source_id, dest_id,
                            dest_topic, stats: TransferStats,
                            progress: ProgressDisplay) -> None:
        for item in items:
            if self._stop:
                break
            messages = item if isinstance(item, list) else [item]
            ids = [m.id for m in messages]
            try:
                self._check_limit()
                await self.client.forward_messages(
                    dest, ids, from_peer=source,
                    as_album=isinstance(item, list), drop_author=True,
                )
                for msg in messages:
                    self._mark_done(msg, source_id, dest_id, stats)
                if isinstance(item, list):
                    stats.albums += 1
            except FloodWaitError as exc:
                await self._handle_flood(exc)
            except RPCError as exc:
                self._mark_failed(messages, source_id, dest_id, stats, exc)
            progress.update(stats.done + stats.skipped + stats.errors,
                            f"{stats.done} {t('copy.sent')} · {stats.per_minute():.0f}/min")
            await asyncio.sleep(self.delay)

    # ── Cópia (download + envio ordenado) ───────────────────────────────────

    async def _copy_loop(self, items, source, dest, source_id, dest_id,
                         dest_topic, stats: TransferStats,
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
                except Exception as exc:
                    logger.exception(f"Falha ao preparar item {index}: {exc}")
                    payload = None
                finally:
                    in_queue.task_done()
                async with cond:
                    ready[index] = payload
                    cond.notify_all()

        async def sender() -> None:
            for index in range(total):
                if self._stop:
                    break
                async with cond:
                    while index not in ready:
                        await cond.wait()
                    payload = ready.pop(index)

                if payload is None:
                    item = items[index]
                    messages = item if isinstance(item, list) else [item]
                    stats.errors += len(messages)
                    for msg in messages:
                        self.repo.update_status(source_id, dest_id, msg.id, Status.ERROR)
                        self.repo.add_failure(source_id, dest_id, msg.id, "preparação falhou")
                    progress.update(stats.done + stats.skipped + stats.errors,
                                    f"{stats.done} {t('copy.sent')}")
                    continue
                if payload.get("skip"):
                    progress.update(stats.done + stats.skipped + stats.errors,
                                    f"{stats.done} {t('copy.sent')} · "
                                    f"{stats.duplicates} {t('copy.duplicates')}")
                    continue

                payload.update({"dest": dest, "dest_topic": dest_topic})
                try:
                    self._check_limit()
                    await self._send(payload, source_id, dest_id, stats, progress)
                except FloodWaitError as exc:
                    await self._handle_flood(exc)
                    messages = payload["messages"]
                    self._mark_failed(messages, source_id, dest_id, stats, exc)
                except RPCError as exc:
                    self._mark_failed(payload["messages"], source_id, dest_id, stats, exc)
                except Exception as exc:
                    logger.exception("Erro no envio")
                    self._mark_failed(payload["messages"], source_id, dest_id, stats, exc)
                finally:
                    self._cleanup(payload.get("plan", []))

                progress.update(stats.done + stats.skipped + stats.errors,
                                f"{stats.done} {t('copy.sent')} · {stats.per_minute():.0f}/min")
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
        if isinstance(exc, ChatAdminRequiredError):
            return t("err.need_admin")
        if isinstance(exc, ChannelPrivateError):
            return t("err.channel_private")
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

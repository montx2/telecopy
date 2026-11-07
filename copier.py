"""
copier.py — Core copy engine com buffer de ordem sequencial.

Melhorias:
  • Download paralelo + Envio sequencial garantido (buffer ordenado)
  • Suporte real a álbuns (grouped_id)
  • FloodWait com back-off exponencial
  • Métricas de velocidade (msgs/min)
"""

import asyncio
import os
import sys
import time
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.tl.types import (
    MessageMediaDocument,
    MessageMediaPhoto,
    DocumentAttributeFilename,
)

from config import MAX_WORKERS_DL, C, PROGRESS_INTERVAL, TEMP_DIR
from database import Repository, Status, db
from dedup import DedupEngine, hash_message
from utils import logger, Fmt
from i18n import t

QueueItem = Union[Any, List[Any]]


def _media_info(msg) -> Tuple[bool, str, int]:
    """(tem_mídia, tipo, tamanho_bytes)"""
    media = getattr(msg, "media", None)
    if not media:
        return False, "text", 0
    if isinstance(media, MessageMediaPhoto):
        sizes = getattr(media.photo, "sizes", [])
        size = max((getattr(s, "size", 0) for s in sizes), default=0)
        return True, "photo", size
    if isinstance(media, MessageMediaDocument):
        doc = media.document
        mime = getattr(doc, "mime_type", "") or ""
        size = getattr(doc, "size", 0) or 0
        if "video" in mime: return True, "video", size
        if "audio" in mime: return True, "audio", size
        if "image" in mime: return True, "photo", size
        return True, "document", size
    return True, "other", 0


def _filename(msg, media_type: str) -> str:
    if getattr(msg, "file", None) and getattr(msg.file, "name", None):
        return msg.file.name
    return {
        "photo": "foto.jpg", "video": "video.mp4",
        "audio": "audio.ogg", "document": "documento.bin",
    }.get(media_type, "arquivo.bin")


@dataclass
class TransferStats:
    start: float = field(default_factory=time.time)
    done: int = 0
    errors: int = 0
    skipped: int = 0
    deduped_id: int = 0
    deduped_hash: int = 0
    bytes_ul: int = 0
    albums_sent: int = 0
    _lock: object = field(default_factory=threading.Lock, repr=False)

    def elapsed(self) -> float:
        return time.time() - self.start

    def msgs_per_min(self) -> float:
        e = self.elapsed()
        return (self.done + self.skipped) / e * 60 if e >= 1 else 0.0

    def inc(self, field_name: str, n: int = 1) -> None:
        with self._lock:
            setattr(self, field_name, getattr(self, field_name) + n)


class ProgressDisplay:
    def __init__(self, total: int, source_name: str) -> None:
        self.total = total
        self._rich_ok = False
        self._prog = None
        self._main_task = None
        self._console = None
        self._last_plain = 0.0

        try:
            from rich.progress import (
                Progress, SpinnerColumn, BarColumn,
                TextColumn, TimeRemainingColumn, MofNCompleteColumn,
            )
            from rich.console import Console
            self._console = Console()
            self._prog = Progress(
                SpinnerColumn(style="bold purple"),
                TextColumn("[bold orange3]{task.description}"),
                BarColumn(complete_style="orange3", finished_style="green", pulse_style="purple"),
                MofNCompleteColumn(),
                TimeRemainingColumn(),
                console=self._console,
                refresh_per_second=10,
            )
            self._main_task = self._prog.add_task(
                f"[{Fmt.trunc(source_name, 30)}]", total=total
            )
            self._rich_ok = True
        except ImportError:
            pass

    def __enter__(self):
        if self._rich_ok:
            self._prog.start()
        return self

    def __exit__(self, *_):
        if self._rich_ok and self._prog:
            self._prog.stop()

    def update(self, done: int, status: str = "") -> None:
        if self._rich_ok and self._prog:
            self._prog.update(
                self._main_task, completed=done,
                description=f"[{status[:50] or 'Copying...'}]",
            )
        else:
            now = time.time()
            if now - self._last_plain < 0.5:
                return
            self._last_plain = now
            pct = int(done / max(1, self.total) * 100)
            filled = pct // 5
            bar = "█" * filled + "░" * (20 - filled)
            print(f"\r  [{bar}] {pct:3d}%  {done}/{self.total}  {status[:30]}     ", end="", flush=True)

    def add_transfer(self, description: str) -> Any:
        if self._rich_ok and self._prog:
            return self._prog.add_task(description, total=100)
        return None

    def update_transfer(self, tid: Any, cur: int, tot: int, description: str = None) -> None:
        if self._rich_ok and self._prog and tid is not None:
            kw: Dict[str, Any] = {"completed": cur}
            if tot: kw["total"] = tot
            if description: kw["description"] = description
            self._prog.update(tid, **kw)

    def remove_transfer(self, tid: Any) -> None:
        if self._rich_ok and self._prog and tid is not None:
            try:
                self._prog.remove_task(tid)
            except Exception:
                pass

    def finish(self, stats: TransferStats) -> None:
        if not self._rich_ok:
            print()
        albums = f"  {C.CYAN}{stats.albums_sent} álbuns{C.RST}" if stats.albums_sent else ""
        mpm = f"  {stats.msgs_per_min():.1f} msg/min" if stats.msgs_per_min() > 0 else ""
        print(
            f"\n  {C.GREEN}✓  Done{C.RST}  "
            f"{C.WHITE}{stats.done:,} {t('copy.sent')}{C.RST}  "
            f"{C.YELLOW}{stats.skipped:,} {t('copy.skipped')}{C.RST}  "
            f"{C.RED}{stats.errors:,} {t('copy.errors')}{C.RST}"
            f"{albums}  {C.GRAY}{Fmt.duration(stats.elapsed())}{mpm}{C.RST}\n"
        )


class Copier:
    def __init__(
        self,
        client: TelegramClient,
        repo: Optional[Repository] = None,
        delay: float = 0.5,
        workers: int = None,
        memory_profile: float = 0.60,  # ADICIONE ESTA LINHA
    ) -> None:
        self.client = client
        self.repo = repo or db
        self.delay = max(0.1, delay)
        self.workers = min(workers or MAX_WORKERS_DL, 10)
        self._memory_profile = memory_profile  # ARMAZENA O VALOR (se precisar usar depois)
        self._dedup = DedupEngine(self.repo)
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._paused = False
        self._stop = False
        self._flood_penalty = 0.0
        
        # Controle de ordem sequencial (buffer)
        self._send_sequence = 0
        self._send_buffer = {}
        self._send_lock = asyncio.Lock()

    async def mirror(
        self,
        source,
        dest,
        filter_code: str = "all",
        source_topic_id: Optional[int] = None,
        dest_topic_id: Optional[int] = None,
    ) -> None:
        from filters import apply as fapply

        source_id = getattr(source, "id", 0)
        dest_id = getattr(dest, "id", 0)
        self._dedup.warm_up(dest_id)
        stats = TransferStats()

        print(f"\n  {C.GREEN}✓{C.RST}  {t('mirror.waiting')}")
        print(f"  {C.GRAY}{t('mirror.stop')}{C.RST}\n")

        @self.client.on(events.NewMessage(chats=source))
        async def mirror_handler(event):
            msg = event.message
            if source_topic_id and getattr(msg, "reply_to_msg_id", None) != source_topic_id:
                return
            if not fapply(filter_code, msg):
                return
            try:
                # Mirror não usa buffer (mensagens chegam em ordem natural)
                has_media, media_type, _ = _media_info(msg)
                if has_media:
                    await self._send_media(msg, dest, source_id, dest_id, media_type, stats, dest_topic_id)
                else:
                    await self._send_text(msg, dest, source_id, dest_id, stats, dest_topic_id)
                note = f" {C.CYAN}[álbum]{C.RST}" if getattr(msg, "grouped_id", None) else ""
                print(f"  {C.GREEN}›{C.RST} {t('mirror.new_msg', id=msg.id)}{note}")
            except Exception as e:
                print(f"  {C.RED}✗{C.RST} Erro MSG {msg.id}: {e}")

        try:
            await self.client.run_until_disconnected()
        except KeyboardInterrupt:
            pass
        finally:
            self.client.remove_event_handler(mirror_handler)

    async def run(
        self,
        source,
        dest,
        filter_code: str = "all",
        session_id: Optional[str] = None,
        resume: bool = True,
        source_topic_id: Optional[int] = None,
        dest_topic_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        source_id = getattr(source, "id", 0)
        dest_id = getattr(dest, "id", 0)
        sess_id = session_id or f"{source_id}:{dest_id}:{int(time.time())}"

        self._dedup.warm_up(dest_id)
        processed_ids = self.repo.get_processed_ids(source_id) if resume else set()

        print(f"\n  {C.CYAN}·{C.RST}  {t('copy.indexing')}", end="", flush=True)
        raw_messages = await self._collect(source, filter_code, source_topic_id)
        print(f"\r  {C.GREEN}✓{C.RST}  {C.WHITE}{len(raw_messages):,}{C.RST} {t('copy.found')}              ")

        if not raw_messages:
            print(f"  {C.YELLOW}!{C.RST}  {t('copy.none')}")
            return {"done": 0, "skipped": 0, "errors": 0}

        items = self._group_messages(raw_messages)

        if resume and processed_ids:
            before = len(items)
            items = self._filter_processed(items, processed_ids)
            already = before - len(items)
            if already > 0:
                print(f"  {C.GRAY}·{C.RST}  {already} {t('copy.skipped')} (checkpoint)")

        if not items:
            print(f"  {C.GREEN}✓{C.RST}  Tudo já processado!")
            return {"done": 0, "skipped": len(raw_messages), "errors": 0}

        album_count = sum(1 for i in items if isinstance(i, list))
        total_msgs = sum(len(i) if isinstance(i, list) else 1 for i in items)

        if album_count:
            print(f"  {C.CYAN}·{C.RST}  {album_count} álbuns detectados")

        # Inicializa controle de sequência para buffer ordenado
        self._send_sequence = 0
        self._send_buffer = {}
        sequence_counter = 0

        self.repo.register_session(
            sess_id, source_id, dest_id,
            source_name=getattr(source, "title", ""),
            dest_name=getattr(dest, "title", ""),
        )
        stats = TransferStats()

        with ProgressDisplay(total_msgs, getattr(source, "title", "Channel")) as prog:
            worker_tasks = [
                asyncio.create_task(
                    self._worker(dest, source_id, dest_id, sess_id, stats, prog, total_msgs, dest_topic_id)
                )
                for _ in range(self.workers)
            ]

            # Envia tupla (índice_sequencial, item) para controle de ordem
            for seq_idx, item in enumerate(items):
                if self._stop:
                    break
                while self._paused and not self._stop:
                    await asyncio.sleep(0.2)
                await self._queue.put((seq_idx, item))
                sequence_counter += 1

            for _ in worker_tasks:
                await self._queue.put(None)

            await asyncio.gather(*worker_tasks, return_exceptions=True)
            prog.finish(stats)

        self.repo.update_stats(
            sess_id,
            success=stats.done,
            errors=stats.errors,
            duplicates=stats.deduped_id + stats.deduped_hash,
            bytes_total=stats.bytes_ul,
        )

        return {
            "done": stats.done,
            "skipped": stats.skipped,
            "errors": stats.errors,
            "albums": stats.albums_sent,
            "elapsed": Fmt.duration(stats.elapsed()),
            "speed": f"{stats.msgs_per_min():.1f} msg/min",
        }

    async def _collect(self, source, filter_code: str, topic_id: Optional[int]) -> List[Any]:
        from filters import apply as fapply
        msgs = []
        kw: Dict[str, Any] = {"reverse": True}
        if topic_id:
            kw["reply_to"] = topic_id
        try:
            async for msg in self.client.iter_messages(source, **kw):
                if fapply(filter_code, msg):
                    msgs.append(msg)
                    if len(msgs) % 2000 == 0:
                        print(f"\r  {C.CYAN}·{C.RST}  {len(msgs):,} indexadas…", end="", flush=True)
        except Exception as exc:
            logger.error(f"collect: {exc}")
        return msgs

    def _group_messages(self, messages: List[Any]) -> List[QueueItem]:
        result: List[QueueItem] = []
        groups: Dict[int, List[Any]] = defaultdict(list)
        seen: set = set()

        for msg in messages:
            gid = getattr(msg, "grouped_id", None)
            if gid:
                groups[gid].append(msg)
                if gid not in seen:
                    seen.add(gid)
                    result.append(gid)
            else:
                result.append(msg)

        final: List[QueueItem] = []
        resolved: set = set()
        for item in result:
            if isinstance(item, int) and item in groups:
                if item not in resolved:
                    resolved.add(item)
                    grp = groups[item]
                    final.append(grp if len(grp) > 1 else grp[0])
            else:
                final.append(item)
        return final

    def _filter_processed(self, items: List[QueueItem], processed_ids: set) -> List[QueueItem]:
        out = []
        for item in items:
            if isinstance(item, list):
                if all(msg.id in processed_ids for msg in item):
                    continue
            else:
                if item.id in processed_ids:
                    continue
            out.append(item)
        return out

    async def _worker(self, dest, source_id, dest_id, sess_id, stats, prog, total, dest_topic_id):
        """Worker com buffer de ordem sequencial (download paralelo + envio ordenado)."""
        while not self._stop:
            while self._paused and not self._stop:
                await asyncio.sleep(0.2)
                
            try:
                data = await asyncio.wait_for(self._queue.get(), timeout=2.0)
            except asyncio.TimeoutError:
                continue
                
            if data is None:
                break
                
            seq_idx, item = data
            
            try:
                # 1. DOWNLOAD (paralelo)
                if isinstance(item, list):
                    download_result = await self._download_album_data(
                        item, source_id, dest_id, stats, dest_topic_id, prog
                    )
                else:
                    download_result = await self._download_single_data(
                        item, source_id, dest_id, stats, dest_topic_id, prog
                    )
                
                if download_result is None:
                    self._queue.task_done()
                    continue
                
                # 2. ENVIO SEQUENCIAL (com buffer)
                async with self._send_lock:
                    self._send_buffer[seq_idx] = {
                        'item': item,
                        'result': download_result,
                        'dest': dest,
                        'source_id': source_id,
                        'dest_id': dest_id,
                        'dest_topic_id': dest_topic_id,
                        'stats': stats,
                        'prog': prog
                    }
                    
                    # Processa buffer em ordem
                    while self._send_sequence in self._send_buffer:
                        buffer_data = self._send_buffer.pop(self._send_sequence)
                        
                        if isinstance(buffer_data['item'], list):
                            await self._send_buffered_album(buffer_data, sess_id)
                        else:
                            await self._send_buffered_single(buffer_data, sess_id)
                        
                        done_total = stats.done + stats.skipped + stats.errors
                        prog.update(done_total, status=f"{stats.done} enviadas · {stats.msgs_per_min():.0f}/min")
                        import sys
                        sys.stdout.flush()
                        
                        self._send_sequence += 1
                
            except Exception as e:
                logger.error(f"Erro no worker seq {seq_idx}: {e}")
                stats.inc("errors")
                
            finally:
                self._queue.task_done()

    # ========== MÉTODOS DO BUFFER ==========
    
    async def _download_single_data(self, msg, source_id, dest_id, stats, dest_topic_id, prog):
        """Baixa mensagem individual e retorna dados prontos (sem enviar)."""
        has_media, media_type, _ = _media_info(msg)
        
        if not has_media:
            return {
                'type': 'text',
                'msg': msg,
                'text': getattr(msg, "text", None) or getattr(msg, "message", None) or ""
            }
        
        fname = _filename(msg, media_type)
        temp_path = str(TEMP_DIR / f"dl_{msg.id}_{int(time.time())}_{fname}")
        
        dl_tid = prog.add_transfer(f"[cyan]↓ {Fmt.trunc(fname, 25)}") if prog else None
        
        async def dl_cb(cur, tot):
            if prog and dl_tid is not None and tot:
                prog.update_transfer(dl_tid, cur, tot, description=f"[cyan]↓ {Fmt.trunc(fname, 20)}")
        
        try:
            await self.client.download_media(msg, file=temp_path, progress_callback=dl_cb)
            if prog: prog.remove_transfer(dl_tid)
            
            return {
                'type': 'media',
                'msg': msg,
                'temp_path': temp_path,
                'media_type': media_type,
                'fname': fname
            }
        except Exception as e:
            if prog: prog.remove_transfer(dl_tid)
            logger.error(f"Erro download msg {msg.id}: {e}")
            return None

    async def _download_album_data(self, msgs, source_id, dest_id, stats, dest_topic_id, prog):
        """Baixa álbum e retorna dados prontos (sem enviar)."""
        lead = msgs[0]
        content_hash = hash_message(lead)
        
        if self._dedup.check(content_hash, dest_id):
            stats.inc("skipped", len(msgs))
            stats.inc("deduped_hash", len(msgs))
            return None
        
        temp_paths = []
        captions = []
        ent_list = []
        force_docs = []
        
        try:
            for msg in msgs:
                _, mt, _ = _media_info(msg)
                fname = _filename(msg, mt)
                temp_path = str(TEMP_DIR / f"alb_{msg.id}_{int(time.time())}_{fname}")
                
                tid = prog.add_transfer(f"[cyan]↓ {Fmt.trunc(fname, 20)}") if prog else None
                
                async def dl_cb(cur, tot, _tid=tid, _fn=fname):
                    if prog and _tid is not None and tot:
                        prog.update_transfer(_tid, cur, tot, description=f"[cyan]↓ {_fn[:18]}")
                
                await self.client.download_media(msg, file=temp_path, progress_callback=dl_cb)
                if prog and tid: prog.remove_transfer(tid)
                
                temp_paths.append(temp_path)
                captions.append(getattr(msg, "text", None) or getattr(msg, "message", None) or "")
                ent_list.append(getattr(msg, "entities", None))
                force_docs.append(mt == "document")
            
            return {
                'type': 'album',
                'msgs': msgs,
                'temp_paths': temp_paths,
                'captions': captions,
                'entities': ent_list,
                'force_docs': force_docs,
                'content_hash': content_hash
            }
            
        except Exception as e:
            for p in temp_paths:
                try:
                    if os.path.exists(p): os.unlink(p)
                except: pass
            logger.error(f"Erro download álbum: {e}")
            return None

    async def _send_buffered_single(self, buffer_data, sess_id):
        """Envia mensagem única que está no buffer."""
        data = buffer_data['result']
        msg = data['msg']
        dest = buffer_data['dest']
        source_id = buffer_data['source_id']
        dest_id = buffer_data['dest_id']
        stats = buffer_data['stats']
        prog = buffer_data['prog']
        dest_topic_id = buffer_data['dest_topic_id']
        
        try:
            if data['type'] == 'text':
                await self._send_text_from_data(data, dest, source_id, dest_id, stats, dest_topic_id)
            else:
                await self._send_media_from_data(data, dest, source_id, dest_id, stats, dest_topic_id, prog)
                
        except FloodWaitError as exc:
            await self._handle_flood(exc, msg)
        except Exception as exc:
            stats.inc("errors")
            logger.error(f"Erro envio seq: {exc}")
            self.repo.update_status(source_id, dest_id, msg.id, Status.ERROR)
        
        await asyncio.sleep(self.delay + self._flood_penalty)

    async def _send_buffered_album(self, buffer_data, sess_id):
        """Envia álbum que está no buffer."""
        data = buffer_data['result']
        dest = buffer_data['dest']
        stats = buffer_data['stats']
        prog = buffer_data['prog']
        dest_topic_id = buffer_data['dest_topic_id']
        source_id = buffer_data['source_id']
        dest_id = buffer_data['dest_id']
        msgs = data['msgs']
        
        try:
            ul_tid = prog.add_transfer(f"[blue]↑ álbum ({len(msgs)} itens)") if prog else None
            
            async def ul_cb(cur, tot, _tid=ul_tid):
                if prog and _tid is not None and tot:
                    prog.update_transfer(_tid, cur, tot, description=f"[blue]↑ álbum")
            
            sent = await self.client.send_file(
                dest,
                file=data['temp_paths'],
                caption=data['captions'],
                formatting_entities=data['entities'],
                reply_to=dest_topic_id,
                progress_callback=ul_cb,
            )
            
            if prog and ul_tid: prog.remove_transfer(ul_tid)
            
            total_size = sum(os.path.getsize(p) for p in data['temp_paths'] if os.path.exists(p))
            stats.inc("bytes_ul", total_size)
            stats.inc("done", len(msgs))
            stats.inc("albums_sent")
            
            sent_list = sent if isinstance(sent, list) else [sent]
            for i, msg in enumerate(msgs):
                sid = sent_list[i].id if i < len(sent_list) else (sent_list[0].id if sent_list else 0)
                self._dedup.register(hash_message(msg), dest_id, sid, 0, source_id)
                self.repo.mark_processed(source_id, msg.id, hash_message(msg))
                self.repo.upsert_checkpoint(source_id, dest_id, msg.id, Status.DONE)
                
        except Exception as exc:
            stats.inc("errors", len(msgs))
            logger.error(f"Erro envio álbum: {exc}")
            for msg in msgs:
                self.repo.update_status(source_id, dest_id, msg.id, Status.ERROR)
        finally:
            for p in data['temp_paths']:
                try:
                    if os.path.exists(p): os.unlink(p)
                except: pass
        
        await asyncio.sleep(self.delay + self._flood_penalty)

    async def _send_text_from_data(self, data, dest, source_id, dest_id, stats, dest_topic_id):
        """Envia texto a partir dos dados bufferizados."""
        msg = data['msg']
        text = data['text']
        
        if not text:
            return
            
        content_hash = hash_message(msg)
        if self._dedup.check(content_hash, dest_id):
            stats.inc("skipped")
            stats.inc("deduped_hash")
            return
        
        entities = getattr(msg, "entities", None)
        sent = await self.client.send_message(dest, text, reply_to=dest_topic_id, formatting_entities=entities)
        
        self._dedup.register(content_hash, dest_id, sent.id if sent else 0, len(text.encode()), source_id)
        self.repo.mark_processed(source_id, msg.id, content_hash)
        self.repo.upsert_checkpoint(source_id, dest_id, msg.id, Status.DONE)
        stats.inc("done")

    async def _send_media_from_data(self, data, dest, source_id, dest_id, stats, dest_topic_id, prog):
        """Envia mídia a partir dos dados bufferizados."""
        msg = data['msg']
        temp_path = data['temp_path']
        media_type = data['media_type']
        
        content_hash = hash_message(msg)
        if self._dedup.check(content_hash, dest_id):
            stats.inc("skipped")
            stats.inc("deduped_hash")
            try:
                os.unlink(temp_path)
            except: pass
            return
        
        caption = getattr(msg, "text", None) or getattr(msg, "message", None) or ""
        entities = getattr(msg, "entities", None)
        fname = data['fname']
        
        ul_tid = prog.add_transfer(f"[blue]↑ {Fmt.trunc(fname, 25)}") if prog else None
        
        async def ul_cb(cur, tot):
            if prog and ul_tid is not None and tot:
                prog.update_transfer(ul_tid, cur, tot, description=f"[blue]↑ {Fmt.trunc(fname, 20)}")
        
        try:
            sent = await self.client.send_file(
                dest,
                file=temp_path,
                caption=caption or None,
                formatting_entities=entities,
                force_document=(media_type == "document"),
                reply_to=dest_topic_id,
                progress_callback=ul_cb,
            )
            
            if prog: prog.remove_transfer(ul_tid)
            
            file_size = os.path.getsize(temp_path) if os.path.exists(temp_path) else 0
            stats.inc("bytes_ul", file_size)
            
            self._dedup.register(content_hash, dest_id, sent.id if sent else 0, file_size, source_id)
            self.repo.mark_processed(source_id, msg.id, content_hash)
            self.repo.upsert_checkpoint(source_id, dest_id, msg.id, Status.DONE, 
                                         content_hash=content_hash, bytes_done=file_size, media_type=media_type)
            stats.inc("done")
            
        finally:
            try:
                if os.path.exists(temp_path): os.unlink(temp_path)
            except: pass

    # ========== MÉTODOS LEGADOS PARA MIRROR ==========
    
    async def _send_text(self, msg, dest, source_id, dest_id, stats, dest_topic_id=None):
        """Envio direto para Mirror Mode (não usa buffer)."""
        text = getattr(msg, "text", None) or getattr(msg, "message", None) or ""
        if not text:
            return
        content_hash = hash_message(msg)
        if self._dedup.check(content_hash, dest_id):
            stats.inc("skipped"); stats.inc("deduped_hash"); return

        entities = getattr(msg, "entities", None)
        sent = await self.client.send_message(dest, text, reply_to=dest_topic_id, formatting_entities=entities)

        self._dedup.register(content_hash, dest_id, sent.id if sent else 0, len(text.encode()), source_id)
        self.repo.mark_processed(source_id, msg.id, content_hash)
        self.repo.upsert_checkpoint(source_id, dest_id, msg.id, Status.DONE)
        stats.inc("done")

    async def _send_media(self, msg, dest, source_id, dest_id, media_type, stats, dest_topic_id=None, prog=None):
        """Envio direto para Mirror Mode (não usa buffer)."""
        content_hash = hash_message(msg)
        if self._dedup.check(content_hash, dest_id):
            stats.inc("skipped"); stats.inc("deduped_hash"); return

        fname = _filename(msg, media_type)
        disp = Fmt.trunc(fname, 25)
        temp_path = str(TEMP_DIR / f"dl_{msg.id}_{int(time.time())}_{fname}")

        dl_tid = prog.add_transfer(f"[cyan]↓ {disp}") if prog else None

        async def dl_cb(cur, tot):
            if prog and dl_tid is not None and tot:
                prog.update_transfer(dl_tid, cur, tot,
                    description=f"[cyan]↓ {disp} ({cur/1048576:.1f}/{tot/1048576:.1f}MB)")

        try:
            await self.client.download_media(msg, file=temp_path, progress_callback=dl_cb)
            if prog: prog.remove_transfer(dl_tid)

            file_size = os.path.getsize(temp_path) if os.path.exists(temp_path) else 0
            caption = getattr(msg, "text", None) or getattr(msg, "message", None) or ""
            entities = getattr(msg, "entities", None)

            ul_tid = prog.add_transfer(f"[blue]↑ {disp}") if prog else None

            async def ul_cb(cur, tot):
                if prog and ul_tid is not None and tot:
                    prog.update_transfer(ul_tid, cur, tot,
                        description=f"[blue]↑ {disp} ({cur/1048576:.1f}/{tot/1048576:.1f}MB)")

            sent = await self.client.send_file(
                dest,
                file=temp_path,
                caption=caption or None,
                formatting_entities=entities,
                force_document=(media_type == "document"),
                reply_to=dest_topic_id,
                progress_callback=ul_cb,
            )

            if prog: prog.remove_transfer(ul_tid)

            stats.inc("bytes_ul", file_size)
            self._dedup.register(content_hash, dest_id, sent.id if sent else 0, file_size, source_id)
            self.repo.mark_processed(source_id, msg.id, content_hash)
            self.repo.upsert_checkpoint(source_id, dest_id, msg.id, Status.DONE,
                content_hash=content_hash, bytes_done=file_size, media_type=media_type)
            stats.inc("done")

        except Exception as exc:
            if prog:
                for tid in [dl_tid]:
                    try: prog.remove_transfer(tid)
                    except Exception: pass
            raise RuntimeError(f"Transfer failed: {exc}") from exc
        finally:
            try:
                if os.path.exists(temp_path): os.unlink(temp_path)
            except OSError:
                pass

    async def _handle_flood(self, exc: FloodWaitError, msg) -> None:
        wait = exc.seconds + 5
        self._flood_penalty = min(self._flood_penalty + 2.0, 30.0)
        logger.warning(f"FloodWait {wait}s (penalty +{self._flood_penalty:.0f}s)")
        print(
            f"\n  {C.YELLOW}!{C.RST}  {t('progress.flood', sec=wait)}"
            f"  (back-off +{self._flood_penalty:.0f}s)",
            flush=True,
        )
        await asyncio.sleep(wait)
        await self._queue.put(msg)
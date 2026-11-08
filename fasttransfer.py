"""
fasttransfer.py — Transferência multi-conexão por arquivo (MTProto).

Reimplementação da técnica de partes paralelas a partir da documentação oficial
https://core.telegram.org/api/files e da API pública/gerada do Telethon. Nenhum
código foi copiado de FastTelethon/mautrix-telegram (ver README, seção Licenças).

O módulo é deliberadamente autocontido: qualquer falha levantar
``FastTransferError`` (ou a exceção original esgotada) para que o copier caia no
caminho padrão ``download_media``/``upload_file`` sem perder o arquivo temporário.

APIs internas do Telethon usadas (por isso requirements.txt fixa a versão):
    client.session.dc_id, client._sender, client._create_exported_sender,
    client._borrow_exported_sender, client._return_exported_sender.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import os
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Set, Tuple

from telethon import functions
from telethon.errors import (
    FileIdInvalidError,
    FileMigrateError,
    FilePartInvalidError,
    FilePartsInvalidError,
    FileReferenceExpiredError,
    FilerefUpgradeNeededError,
    FloodError,
    FloodPremiumWaitError,
    RPCError,
)
from telethon.helpers import generate_random_long
from telethon.tl import types

from config import MAX_CONNECTIONS_PER_FILE
from i18n import t
from utils import logger

PART_SIZE = 512 * 1024
MIN_ALIGN = 4096
MIB = 1024 * 1024
SMALL_FILE_LIMIT = 10 * 1024 * 1024
SMALL_MAX_PARTS = 3000
BIG_MAX_PARTS = 8192
PART_TIMEOUT = 30.0
MAX_BLOCK_ATTEMPTS = 5
MAX_REFRESHES = 2
# FLOOD_PREMIUM_WAIT é teto de velocidade da conta, não excesso de requisições:
# cortar conexões não ajuda (e deixaria tudo mais lento). Só se espera.
MAX_PREMIUM_WAITS = 30
# Recuperação gradual: depois de um flood o pool voltava a ficar lento para o
# resto da execução. Agora a capacidade sobe de volta com envios bem-sucedidos.
RECOVERY_SUCCESSES = 48
TIMEOUTS_BEFORE_PENALTY = 5

ProgressCallback = Callable[[int, int], Any]
RefreshCallback = Callable[[], Awaitable[Any]]


class FastTransferError(RuntimeError):
    """Falha no caminho rápido; o chamador deve usar o transporte padrão."""


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _write_at(path: str, offset: int, data: bytes) -> None:
    # pwrite onde existir; fallback portátil com seek/write para Windows.
    if hasattr(os, "pwrite"):
        fd = os.open(path, os.O_WRONLY)
        try:
            written = os.pwrite(fd, data, offset)
            if written != len(data):
                raise OSError(f"escrita curta em {offset}: {written}/{len(data)}")
        finally:
            os.close(fd)
        return
    with open(path, "r+b") as handle:
        handle.seek(offset)
        handle.write(data)


def _read_part(path: str, offset: int, size: int) -> bytes:
    with open(path, "rb") as handle:
        handle.seek(offset)
        data = handle.read(size)
    if len(data) != size:
        raise OSError(f"leitura curta em {offset}: {len(data)}/{size}")
    return data


def _md5(path: str) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(PART_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def input_location(msg: Any) -> Tuple[Optional[int], Any, int]:
    """(dc_id, InputFileLocation, tamanho) para documento ou foto comum."""
    media = getattr(msg, "media", None)
    name = type(media).__name__
    if name == "MessageMediaDocument":
        doc = media.document
        if doc is None:
            raise FastTransferError("documento ausente")
        return (
            getattr(doc, "dc_id", None),
            types.InputDocumentFileLocation(
                id=doc.id, access_hash=doc.access_hash,
                file_reference=doc.file_reference, thumb_size="",
            ),
            int(doc.size),
        )
    if name == "MessageMediaPhoto":
        photo = media.photo
        if photo is None:
            raise FastTransferError("foto ausente")
        best = None
        best_size = -1
        for size in getattr(photo, "sizes", []) or []:
            if type(size).__name__ in {"PhotoSizeEmpty", "PhotoStrippedSize", "PhotoCachedSize"}:
                continue
            value = (max(getattr(size, "sizes", []) or [0])
                     if type(size).__name__ == "PhotoSizeProgressive"
                     else int(getattr(size, "size", 0) or 0))
            if value > best_size:
                best, best_size = size, value
        if best is None or best_size <= 0:
            raise FastTransferError("foto sem tamanho transferível")
        return (
            getattr(photo, "dc_id", None),
            types.InputPhotoFileLocation(
                id=photo.id, access_hash=photo.access_hash,
                file_reference=photo.file_reference, thumb_size=best.type,
            ),
            best_size,
        )
    raise FastTransferError(f"mídia não suportada pelo caminho rápido: {name}")


@dataclass
class _SenderInfo:
    sender: Any
    dc_id: Optional[int]
    owned: bool
    borrowed: bool = False


class FastTransferPool:
    """Orçamento global de conexões/senders reutilizados entre arquivos.

    ``capacity`` limita requisições de parte simultâneas entre todos os workers.
    Senders exportados ficam em idle por DC e são fechados no shutdown. Para o
    DC da sessão, o sender principal é compartilhado com várias requisições em
    voo (o Telegram não permite exportar autorização para o próprio DC).
    """

    def __init__(self, client: Any, max_total_connections: int = 12,
                 connections_per_file: int = 4) -> None:
        self.client = client
        self.capacity = max(1, int(max_total_connections))
        self.initial_capacity = self.capacity
        self.connections_per_file = max(
            1, min(int(connections_per_file), MAX_CONNECTIONS_PER_FILE))
        self._idle: Dict[Optional[int], Deque[Any]] = defaultdict(deque)
        self._owned: Set[Any] = set()
        self._leased = 0
        self._closed = False
        self._cond = asyncio.Condition()
        self._timeouts = 0
        self._successes = 0
        self.halvings = 0
        self.recoveries = 0

    async def _new_sender(self, dc_id: Optional[int]) -> _SenderInfo:
        home = getattr(getattr(self.client, "session", None), "dc_id", None)
        if dc_id is None or dc_id == home:
            return _SenderInfo(self.client._sender, dc_id, owned=False)
        try:
            sender = await self.client._create_exported_sender(dc_id)
            self._owned.add(sender)
            return _SenderInfo(sender, dc_id, owned=True)
        except Exception as exc:
            logger.warning(f"Sender exportado DC {dc_id} indisponível: {exc}")
            # Fallback interno do Telethon para DCs que recusam exportação nova.
            try:
                sender = await self.client._borrow_exported_sender(dc_id)
                return _SenderInfo(sender, dc_id, owned=False, borrowed=True)
            except Exception:
                raise FastTransferError(f"sem conexão para DC {dc_id}") from exc

    async def acquire(self, dc_id: Optional[int]) -> _SenderInfo:
        async with self._cond:
            await self._cond.wait_for(lambda: self._closed or self._leased < self.capacity)
            if self._closed:
                raise FastTransferError("pool de transferências fechado")
            self._leased += 1
            idle = self._idle.get(dc_id)
            sender = idle.popleft() if idle else None
        if sender is not None:
            return _SenderInfo(sender, dc_id, owned=sender in self._owned)
        try:
            return await self._new_sender(dc_id)
        except BaseException:
            async with self._cond:
                self._leased = max(0, self._leased - 1)
                self._cond.notify_all()
            raise

    async def release(self, info: _SenderInfo, *, broken: bool = False) -> None:
        async with self._cond:
            self._leased = max(0, self._leased - 1)
            if info.borrowed:
                try:
                    await self.client._return_exported_sender(info.sender)
                except Exception:
                    pass
            elif info.owned:
                if broken or self._closed:
                    self._owned.discard(info.sender)
                    await self._disconnect(info.sender)
                else:
                    self._idle[info.dc_id].append(info.sender)
            self._cond.notify_all()

    async def _disconnect(self, sender: Any) -> None:
        try:
            await sender.disconnect()
        except Exception as exc:
            logger.warning(f"Falha ao fechar sender de transferência: {exc}")

    def note_timeout(self) -> None:
        self._timeouts += 1
        if self._timeouts >= TIMEOUTS_BEFORE_PENALTY:
            self._timeouts = 0
            self.penalize("timeouts repetidos")

    def note_success(self) -> None:
        """Zera timeouts e devolve capacidade perdida, degrau por degrau."""
        self._timeouts = 0
        if self.capacity >= self.initial_capacity:
            self._successes = 0
            return
        self._successes += 1
        if self._successes >= RECOVERY_SUCCESSES:
            self._successes = 0
            self.capacity = min(self.initial_capacity, self.capacity + 1)
            self.recoveries += 1
            logger.info(t("transfer.recovered", val=self.capacity))

    def penalize(self, reason: str) -> None:
        self._successes = 0
        new = max(1, self.capacity // 2)
        if new < self.capacity:
            logger.warning(t("transfer.auto_protect", val=new) + f" ({reason})")
            self.capacity = new
            self.halvings += 1

    async def close(self) -> None:
        async with self._cond:
            self._closed = True
            owned = list(self._owned)
            self._owned.clear()
            self._idle.clear()
            self._cond.notify_all()
        for sender in owned:
            await self._disconnect(sender)


async def _call_part(pool: FastTransferPool, dc_id: Optional[int], request: Any) -> Any:
    info = await pool.acquire(dc_id)
    broken = False
    try:
        result = await asyncio.wait_for(info.sender.send(request), timeout=PART_TIMEOUT)
        pool.note_success()
        return result
    except asyncio.TimeoutError:
        pool.note_timeout()
        # Não derrubar o sender principal da sessão por um timeout de parte.
        broken = info.owned
        raise
    except (ConnectionError, OSError):
        broken = True
        raise
    finally:
        await pool.release(info, broken=broken)


async def _retry_block(pool: FastTransferPool, operation: Callable[[], Awaitable[Any]],
                       *, on_flood: Optional[Callable[[FloodError], Awaitable[None]]] = None) -> Any:
    last: Optional[Exception] = None
    attempt = 0
    premium_waits = 0
    while attempt < MAX_BLOCK_ATTEMPTS:
        try:
            return await operation()
        except FloodPremiumWaitError as exc:
            # Limite de velocidade da conta (sem Premium). Não reduz conexões,
            # não conta como tentativa falha: espera e repete a mesma parte.
            last = exc
            premium_waits += 1
            if premium_waits > MAX_PREMIUM_WAITS:
                raise FastTransferError(f"limite de velocidade da conta: {exc}") from exc
            wait = int(getattr(exc, "seconds", 0) or 0)
            logger.warning(t("transfer.premium_wait", sec=wait))
            if on_flood is not None:
                await on_flood(exc)
            else:
                await asyncio.sleep(wait)
            continue
        except FloodError as exc:
            last = exc
            pool.penalize(type(exc).__name__)
            wait = int(getattr(exc, "seconds", 0) or 0)
            if on_flood is not None:
                await on_flood(exc)
            else:
                await asyncio.sleep(wait)
        except (asyncio.TimeoutError, ConnectionError, OSError) as exc:
            last = exc
            if attempt < MAX_BLOCK_ATTEMPTS - 1:
                await asyncio.sleep(min(2 ** attempt, 8))
        except RPCError:
            # Erros de protocolo específicos são tratados pelo chamador antes
            # de chegar aqui (migração/refresh). Outros RPC não são transitórios.
            raise
        attempt += 1
    raise FastTransferError(f"parte esgotou retentativas: {last}") from last


async def _run_workers(workers: List[asyncio.Task]) -> None:
    """Espera todos, mas cancela imediatamente os irmãos se um falhar."""
    try:
        done, pending = await asyncio.wait(workers, return_when=asyncio.FIRST_EXCEPTION)
        for task in done:
            exc = task.exception()
            if exc is not None:
                raise exc
        if pending:
            await asyncio.gather(*pending)
    except BaseException:
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        raise


async def fast_download(
    pool: FastTransferPool,
    msg: Any,
    path: str | Path,
    *,
    refresh: Optional[RefreshCallback] = None,
    progress_callback: Optional[ProgressCallback] = None,
    on_flood: Optional[Callable[[FloodError], Awaitable[None]]] = None,
) -> str:
    """Baixa por blocos de 512 KiB alinhados, gravando por offset."""
    path = str(path)
    dc_id, location, size = input_location(msg)
    if size <= 0:
        raise FastTransferError("tamanho de mídia inválido")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        handle.truncate(size)

    state: Dict[str, Any] = {"dc": dc_id, "location": location, "refreshes": 0}
    lock = asyncio.Lock()
    received = 0
    offsets = list(range(0, size, PART_SIZE))
    queue: asyncio.Queue = asyncio.Queue()
    for offset in offsets:
        queue.put_nowait(offset)

    async def do_refresh() -> None:
        async with lock:
            if refresh is None or state["refreshes"] >= MAX_REFRESHES:
                raise FastTransferError("file reference expirada sem refresh disponível")
            state["refreshes"] += 1
            refreshed = await refresh()
            if refreshed is None or getattr(refreshed, "media", None) is None:
                raise FastTransferError("mensagem não existe mais para refresh")
            new_dc, new_location, new_size = input_location(refreshed)
            if new_size != size:
                raise FastTransferError("mídia mudou de tamanho durante refresh")
            state["dc"], state["location"] = new_dc, new_location

    async def fetch(offset: int) -> bytes:
        limit = PART_SIZE
        for migrate in range(3):
            request = functions.upload.GetFileRequest(
                location=state["location"], offset=offset, limit=limit,
                precise=False, cdn_supported=False,
            )
            try:
                result = await _retry_block(
                    pool, lambda: _call_part(pool, state["dc"], request), on_flood=on_flood,
                )
            except FileMigrateError as exc:
                state["dc"] = int(exc.new_dc)
                continue
            except (FileReferenceExpiredError, FilerefUpgradeNeededError):
                await do_refresh()
                continue
            if isinstance(result, types.upload.FileCdnRedirect):
                raise FastTransferError("CDN redirect não suportado no caminho rápido")
            data = getattr(result, "bytes", b"")
            expected = min(limit, size - offset)
            if len(data) != expected:
                raise FastTransferError(
                    f"bloco {offset} veio com {len(data)} bytes, esperado {expected}"
                )
            return data
        raise FastTransferError(f"migrações de DC demais no bloco {offset}")

    async def worker() -> None:
        nonlocal received
        while True:
            try:
                offset = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                data = await fetch(offset)
                await asyncio.to_thread(_write_at, path, offset, data)
                received += len(data)
                if progress_callback is not None:
                    await _maybe_await(progress_callback(received, size))
            finally:
                queue.task_done()

    workers = [asyncio.create_task(worker())
               for _ in range(min(pool.connections_per_file, len(offsets)))]
    await _run_workers(workers)
    if received != size or os.path.getsize(path) != size:
        raise FastTransferError(f"tamanho final inválido: {received}/{size}")
    return path


async def fast_upload(
    pool: FastTransferPool,
    path: str | Path,
    file_name: str,
    *,
    progress_callback: Optional[ProgressCallback] = None,
    on_flood: Optional[Callable[[FloodError], Awaitable[None]]] = None,
) -> Any:
    """Upload paralelo de partes; retorna InputFile/InputFileBig sem enviar."""
    path = str(path)
    size = os.path.getsize(path)
    if size <= 0:
        raise FastTransferError("arquivo vazio")
    is_big = size > SMALL_FILE_LIMIT
    part_count = (size + PART_SIZE - 1) // PART_SIZE
    max_parts = BIG_MAX_PARTS if is_big else SMALL_MAX_PARTS
    if part_count > max_parts:
        raise FastTransferError(f"FILE_PARTS_INVALID: {part_count} > {max_parts}")
    file_id = generate_random_long()
    md5 = await asyncio.to_thread(_md5, path) if not is_big else ""
    sent = 0
    queue: asyncio.Queue = asyncio.Queue()
    for index in range(part_count):
        queue.put_nowait(index)

    async def save(index: int) -> None:
        offset = index * PART_SIZE
        length = min(PART_SIZE, size - offset)
        data = await asyncio.to_thread(_read_part, path, offset, length)
        if is_big:
            request = functions.upload.SaveBigFilePartRequest(
                file_id=file_id, file_part=index, file_total_parts=part_count, bytes=data,
            )
        else:
            request = functions.upload.SaveFilePartRequest(
                file_id=file_id, file_part=index, bytes=data,
            )

        async def operation() -> Any:
            home = getattr(getattr(pool.client, "session", None), "dc_id", None)
            return await _call_part(pool, home, request)

        try:
            ok = await _retry_block(pool, operation, on_flood=on_flood)
        except (FilePartsInvalidError, FilePartInvalidError, FileIdInvalidError) as exc:
            raise FastTransferError(f"parte rejeitada: {exc}") from exc
        if not ok:
            raise FastTransferError(f"SaveFilePart retornou false na parte {index}")

    async def worker() -> None:
        nonlocal sent
        while True:
            try:
                index = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                await save(index)
                sent += min(PART_SIZE, size - index * PART_SIZE)
                if progress_callback is not None:
                    await _maybe_await(progress_callback(sent, size))
            finally:
                queue.task_done()

    workers = [asyncio.create_task(worker())
               for _ in range(min(pool.connections_per_file, part_count))]
    await _run_workers(workers)
    if sent != size:
        raise FastTransferError(f"upload incompleto: {sent}/{size}")

    if is_big:
        return types.InputFileBig(id=file_id, parts=part_count, name=file_name)
    return types.InputFile(id=file_id, parts=part_count, name=file_name, md5_checksum=md5)

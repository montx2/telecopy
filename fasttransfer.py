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
import math
import os
import random
import time
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
from utils import logger, read_json, write_json

PART_SIZE = 512 * 1024
MIN_ALIGN = 4096
MIB = 1024 * 1024
SMALL_FILE_LIMIT = 10 * 1024 * 1024
SMALL_MAX_PARTS = 3000
BIG_MAX_PARTS = 8192
PART_TIMEOUT = 30.0
MAX_BLOCK_ATTEMPTS = 5
MAX_BLOCK_WAIT_SECONDS = 120.0
MAX_REFRESHES = 2
# FloodPremiumWait também reduz capacidade: é um sinal real de throttle.
# O limite de repetições evita loop infinito; o orçamento de espera decide fallback.
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


@dataclass(frozen=True)
class FloodNotice:
    """Resultado de registrar um erro; first elege um único líder por episódio."""

    episode_id: int
    first: bool
    remaining: float
    parts: int


@dataclass
class _FloodEpisode:
    identity: int
    deadline: float
    parts: int = 0


class FloodController:
    """Único controlador AIMD de partes, compartilhado por arquivos e DCs.

    Flood sobrepõe prazos sem aplicar penalidades duas vezes. A capacidade
    cai uma vez por episódio e só sobe após RECOVERY_SUCCESSES confirmações
    de partes. O histórico é um limite conservador por conta/DC, não uma
    promessa de throughput. Nenhum identificador de conta é salvo em claro.
    """

    def __init__(self, capacity: int, *, rng: Optional[random.Random] = None,
                 controller_path: Optional[Path] = None,
                 account_id: Optional[str] = None,
                 home_dc: Optional[int] = None) -> None:
        self.capacity = max(1, int(capacity))
        self.initial_capacity = self.capacity
        self._rng = rng or random.Random()
        self._cooldown_until = 0.0
        self._episode: Optional[_FloodEpisode] = None
        self._episode_counter = 0
        self._ramp_remaining = 0
        self._ramp_at = 0.0
        self._closed = False
        self._cond = asyncio.Condition()
        self._successes = 0
        self._timeouts = 0
        self.halvings = 0
        self.recoveries = 0
        self.episodes = 0
        self.observer: Optional[Callable[[float, int, bool], None]] = None
        self._home_dc = home_dc
        self._dcs: Set[Optional[int]] = {home_dc}
        self._account_hash = hashlib.sha256(str(account_id).encode()).hexdigest() if account_id else ""
        self.controller_path = Path(controller_path) if controller_path is not None and account_id else None
        self._load_capacity()

    @staticmethod
    def _now() -> float:
        return asyncio.get_running_loop().time()

    @property
    def flood_parts(self) -> int:
        return self._episode.parts if self._episode is not None else 0

    def _key(self, dc_id: Optional[int]) -> str:
        return hashlib.sha256(f"{self._account_hash}:{dc_id}".encode()).hexdigest()

    def _load_capacity(self) -> None:
        if self.controller_path is None:
            return
        data = read_json(self.controller_path, {})
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("limits"), dict):
            logger.warning(t("transfer.state_invalid"))
            return
        saved = data["limits"].get(self._key(self._home_dc))
        if isinstance(saved, int) and not isinstance(saved, bool) and 1 <= saved <= self.initial_capacity:
            self.capacity = saved
            logger.info(t("transfer.state_loaded", val=saved))

    def _save_capacity(self, *, conservative: bool = False) -> None:
        if self.controller_path is None:
            return
        data = read_json(self.controller_path, {})
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("limits"), dict):
            data = {"version": 1, "limits": {}}
        for dc_id in self._dcs:
            key = self._key(dc_id)
            anterior = data["limits"].get(key)
            seguro = (min(self.capacity, anterior) if conservative and isinstance(anterior, int) and anterior > 0
                      else self.capacity)
            data["limits"][key] = seguro
        data["updated_at"] = time.time()
        if not write_json(self.controller_path, data):
            logger.warning(t("transfer.state_save_failed"))

    def _reduce(self, reason: str) -> None:
        self._successes = 0
        nova = max(1, self.capacity // 2)
        if nova < self.capacity:
            self.capacity = nova
            self.halvings += 1
            logger.warning(t("transfer.auto_protect", val=nova) + f" ({reason})")

    def penalize(self, reason: str) -> None:
        self._reduce(reason)
        self._save_capacity()

    def note_timeout(self) -> None:
        self._timeouts += 1
        if self._timeouts >= TIMEOUTS_BEFORE_PENALTY:
            self._timeouts = 0
            self.penalize("timeouts repetidos")

    def note_success(self) -> None:
        self._timeouts = 0
        if self.capacity >= self.initial_capacity:
            self._successes = 0
            return
        self._successes += 1
        if self._successes >= RECOVERY_SUCCESSES:
            self._successes = 0
            self.capacity = min(self.initial_capacity, self.capacity + 1)
            self.recoveries += 1
            self._save_capacity()
            logger.info(t("transfer.recovered", val=self.capacity))

    def note_flood(self, exc: FloodError, dc_id: Optional[int] = None) -> FloodNotice:
        """Registra na thread do event loop; idempotente para a mesma exceção."""
        now = self._now()
        wait = max(0, int(getattr(exc, "seconds", 0) or 0))
        token = getattr(exc, "_telecopy_flood_token", None)
        if token is not None and token[0] == id(self) and self._episode is not None:
            # Callback atrasado da mesma exceção não é um novo episódio, mesmo
            # quando chega depois do prazo. Só uma nova exceção inicia episódio.
            return FloodNotice(token[1], False, max(0.0, self._cooldown_until - now),
                               self._episode.parts)
        first = self._episode is None or now >= self._cooldown_until
        if first:
            self._episode_counter += 1
            self.episodes += 1
            self._episode = _FloodEpisode(self._episode_counter, now + wait)
            self._reduce(type(exc).__name__)
            self._ramp_remaining = self.capacity
            self._ramp_at = self._episode.deadline
        self._episode.deadline = max(self._episode.deadline, now + wait)
        self._cooldown_until = self._episode.deadline
        self._ramp_at = max(self._ramp_at, self._cooldown_until)
        self._episode.parts += 1
        self._dcs.add(dc_id)
        try:
            setattr(exc, "_telecopy_flood_token", (id(self), self._episode.identity))
        except (AttributeError, TypeError):
            pass
        remaining = max(0.0, self._cooldown_until - now)
        if first:
            logger.warning(t("transfer.cooldown.log", sec=math.ceil(remaining),
                             parts=self._episode.parts, episode=self._episode.identity))
            self._save_capacity(conservative=True)
        if self.observer is not None:
            try:
                self.observer(remaining, self._episode.parts, first)
            except Exception as exc_obs:
                logger.debug(t("transfer.observer_error", reason=exc_obs))
        return FloodNotice(self._episode.identity, first, remaining, self._episode.parts)

    def _notify(self, remaining: float, parts: int, first: bool = False) -> None:
        if self.observer is None:
            return
        try:
            self.observer(remaining, parts, first)
        except Exception as exc:
            logger.debug(t("transfer.observer_error", reason=exc))

    async def wait_cooldown(self) -> None:
        """Portão comum: ninguém faz sleep(wait + margem) por conta própria."""
        while True:
            async with self._cond:
                if self._closed:
                    raise FastTransferError(t("transfer.pool_closed"))
                remaining = self._cooldown_until - self._now()
                if remaining <= 0:
                    if self._episode is not None:
                        self._notify(0.0, self._episode.parts)
                    return
                try:
                    await asyncio.wait_for(self._cond.wait(), timeout=min(remaining, 1.0))
                except asyncio.TimeoutError:
                    if self._episode is not None and not self._closed:
                        self._notify(max(0.0, self._cooldown_until - self._now()),
                                     self._episode.parts)

    def lease_delay(self) -> float:
        """Chamado sob o lock do pool: escalona apenas a primeira rodada pós-flood."""
        if self._ramp_remaining <= 0:
            return 0.0
        now = self._now()
        self._ramp_remaining -= 1
        self._ramp_at = max(now, self._ramp_at, self._cooldown_until) + self._rng.uniform(0, 0.25)
        return max(0.0, self._ramp_at - now)

    async def close(self) -> None:
        async with self._cond:
            self._closed = True
            self._cond.notify_all()


class FastTransferPool:
    """Leases de senders + controlador adaptativo compartilhado por arquivo/DC."""

    def __init__(self, client: Any, max_total_connections: int = 4,
                 connections_per_file: int = 2, *,
                 rng: Optional[random.Random] = None,
                 controller_path: Optional[Path] = None,
                 account_id: Optional[str] = None,
                 block_wait_budget: float = MAX_BLOCK_WAIT_SECONDS,
                 bandwidth_limit_bps: int = 0) -> None:
        self.client = client
        home = getattr(getattr(client, "session", None), "dc_id", None)
        self.controller = FloodController(max_total_connections, rng=rng,
                                          controller_path=controller_path,
                                          account_id=account_id, home_dc=home)
        self.block_wait_budget = max(0.0, float(block_wait_budget))
        from utils import LimitadorBanda
        self.limiter = LimitadorBanda(bandwidth_limit_bps)
        self.connections_per_file = max(1, min(int(connections_per_file), MAX_CONNECTIONS_PER_FILE))
        self._idle: Dict[Optional[int], Deque[Any]] = defaultdict(deque)
        self._owned: Set[Any] = set()
        self._leased = 0
        self._closed = False
        self._cond = asyncio.Condition()

    @property
    def capacity(self) -> int:
        return self.controller.capacity

    @property
    def initial_capacity(self) -> int:
        return self.controller.initial_capacity

    @property
    def halvings(self) -> int:
        return self.controller.halvings

    @property
    def recoveries(self) -> int:
        return self.controller.recoveries

    @property
    def flood_parts(self) -> int:
        return self.controller.flood_parts

    @property
    def _cooldown_until(self) -> float:
        return self.controller._cooldown_until

    def note_flood(self, exc: FloodError, dc_id: Optional[int] = None) -> FloodNotice:
        return self.controller.note_flood(exc, dc_id)

    def note_timeout(self) -> None:
        self.controller.note_timeout()

    def note_success(self) -> None:
        self.controller.note_success()

    def penalize(self, reason: str) -> None:
        self.controller.penalize(reason)

    def _notify(self, remaining: float, parts: int, first: bool = False) -> None:
        if self.observer is None:
            return
        try:
            self.observer(remaining, parts, first)
        except Exception as exc:
            logger.debug(t("transfer.observer_error", reason=exc))

    async def wait_cooldown(self) -> None:
        await self.controller.wait_cooldown()

    async def _new_sender(self, dc_id: Optional[int]) -> _SenderInfo:
        home = getattr(getattr(self.client, "session", None), "dc_id", None)
        if dc_id is None or dc_id == home:
            return _SenderInfo(self.client._sender, dc_id, owned=False)
        try:
            sender = await self.client._create_exported_sender(dc_id)
            self._owned.add(sender)
            return _SenderInfo(sender, dc_id, owned=True)
        except FloodError:
            # ExportAuthorization também pode sofrer flood. Não transformar em
            # referência expirada nem tentar outra conexão para contornar o prazo.
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(t("transfer.export_unavailable", dc=dc_id, reason=exc))
            try:
                sender = await self.client._borrow_exported_sender(dc_id)
                return _SenderInfo(sender, dc_id, owned=False, borrowed=True)
            except FloodError:
                raise
            except Exception:
                raise FastTransferError(t("transfer.no_sender", dc=dc_id)) from exc

    async def acquire(self, dc_id: Optional[int]) -> _SenderInfo:
        await self.wait_cooldown()
        async with self._cond:
            await self._cond.wait_for(lambda: self._closed or self._leased < self.capacity)
            if self._closed:
                raise FastTransferError(t("transfer.pool_closed"))
            self._leased += 1
            ramp = self.controller.lease_delay()
            idle = self._idle.get(dc_id)
            sender = idle.popleft() if idle else None
        try:
            if ramp > 0:
                await asyncio.sleep(ramp)
            await self.wait_cooldown()
            if self._closed:
                raise FastTransferError(t("transfer.pool_closed"))
            if sender is not None:
                return _SenderInfo(sender, dc_id, owned=sender in self._owned)
            return await self._new_sender(dc_id)
        except BaseException:
            async with self._cond:
                self._leased = max(0, self._leased - 1)
                if sender is not None:
                    self._idle[dc_id].append(sender)
                self._cond.notify_all()
            raise

    async def release(self, info: _SenderInfo, *, broken: bool = False) -> None:
        async with self._cond:
            self._leased = max(0, self._leased - 1)
            if info.borrowed:
                try:
                    await self.client._return_exported_sender(info.sender)
                except Exception as exc:
                    logger.debug(t("transfer.return_failed", reason=exc))
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
            logger.warning(t("transfer.close_failed", reason=exc))

    async def close(self) -> None:
        async with self._cond:
            self._closed = True
            owned = list(self._owned)
            self._owned.clear()
            self._idle.clear()
            self._cond.notify_all()
        await self.controller.close()
        for sender in owned:
            await self._disconnect(sender)


async def _call_part(pool: FastTransferPool, dc_id: Optional[int], request: Any) -> Any:
    info = await pool.acquire(dc_id)
    broken = False
    try:
        nbytes = int(getattr(request, "limit", 0) or 0)
        if not nbytes:
            nbytes = len(getattr(request, "bytes", b"") or b"")
        await pool.limiter.consumir(nbytes)
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
                       *, on_flood: Optional[Callable[[FloodError], Any]] = None,
                       context: str = "") -> Any:
    """Retenta uma parte sem transformar um episódio em N penalidades/esperas.

    ``on_flood`` apenas registra/observa o episódio; quem espera é o portão do
    pool. Só o líder chama o callback. Orçamento é tempo efetivamente esperado,
    não a soma de deadlines idênticos de todas as partes.
    """
    last: Optional[Exception] = None
    attempt = 0
    premium_waits = 0
    wait_spent = 0.0
    while attempt < MAX_BLOCK_ATTEMPTS:
        try:
            return await operation()
        except FloodError as exc:
            last = exc
            notice = pool.note_flood(exc)
            logger.debug(t("transfer.cooldown.detail", episode=notice.episode_id,
                           context=context, reason=type(exc).__name__))
            if notice.first and on_flood is not None:
                await _maybe_await(on_flood(exc))
            if isinstance(exc, FloodPremiumWaitError):
                premium_waits += 1
                if premium_waits > MAX_PREMIUM_WAITS:
                    raise FastTransferError(t("transfer.premium_budget", reason=exc)) from exc
            else:
                attempt += 1
            clock = asyncio.get_running_loop()
            remaining = max(0.0, pool._cooldown_until - clock.time())
            if wait_spent + remaining > pool.block_wait_budget:
                raise FastTransferError(t("transfer.wait_budget", sec=f"{wait_spent + remaining:.1f}")) from exc
            before = clock.time()
            await pool.wait_cooldown()
            wait_spent += max(0.0, clock.time() - before)
            continue
        except (asyncio.TimeoutError, ConnectionError, OSError) as exc:
            last = exc
            if attempt < MAX_BLOCK_ATTEMPTS - 1:
                delay = min(2 ** attempt, 8)
                if wait_spent + delay > pool.block_wait_budget:
                    raise FastTransferError(t("transfer.wait_budget", sec=f"{wait_spent + delay:.1f}")) from exc
                await asyncio.sleep(delay)
                wait_spent += delay
        except RPCError:
            # Migração/refresh são responsabilidade do chamador. Não retries
            # cegos para erros de protocolo que exigem outra política.
            raise
        attempt += 1
    raise FastTransferError(t("transfer.block_exhausted", reason=last)) from last


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
                    context=f"download offset={offset}",
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
            ok = await _retry_block(pool, operation, on_flood=on_flood,
                                    context=f"upload part={index}")
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

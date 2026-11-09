"""Relógio virtual determinístico para testar esperas sem dormir de verdade.

Não substitui o relógio do sistema: substitui apenas o tempo do event loop.
Operações em executor continuam reais; seus futures impedem avanço artificial
do tempo enquanto o trabalho de disco está pendente.
"""
from __future__ import annotations

import asyncio
import heapq
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any


class EventLoopSimulado(asyncio.SelectorEventLoop):
    def __init__(self) -> None:
        super().__init__()
        self._agora = 1000.0
        self._executores_pendentes = 0

    def time(self) -> float:
        return self._agora

    def call_at(self, when: float, callback: Callable[..., Any], *args: Any,
                context: Any = None) -> asyncio.TimerHandle:
        handle = asyncio.TimerHandle(when, callback, args, self, context)
        heapq.heappush(self._scheduled, handle)
        handle._scheduled = True
        return handle

    def run_in_executor(self, executor: Any, func: Callable[..., Any], *args: Any) -> asyncio.Future:
        future = super().run_in_executor(executor, func, *args)
        self._executores_pendentes += 1

        def concluiu(_: Any) -> None:
            self._executores_pendentes -= 1

        future.add_done_callback(concluiu)
        return future

    def _run_once(self) -> None:
        while self._scheduled and self._scheduled[0].cancelled():
            heapq.heappop(self._scheduled)
        if (not self._ready and not self._stopping and self._scheduled
                and not self._executores_pendentes):
            self._agora = max(self._agora, self._scheduled[0].when())
        super()._run_once()


@contextmanager
def relogio_simulado() -> Iterator[EventLoopSimulado]:
    loop = EventLoopSimulado()
    asyncio.set_event_loop(loop)
    try:
        yield loop
    finally:
        try:
            pendentes = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for tarefa in pendentes:
                tarefa.cancel()
            if pendentes:
                loop.run_until_complete(asyncio.gather(*pendentes, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(loop.shutdown_default_executor())
        finally:
            asyncio.set_event_loop(None)
            loop.close()

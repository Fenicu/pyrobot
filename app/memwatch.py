"""Память процесса: замеры RSS раз в минуту (сутки — в памяти), возврат освобождённой памяти
glibc системе (`malloc_trim`) раз в 5 минут и предупреждение `memory_jump` о скачке RSS со
списком HTTP-запросов в работе."""

from __future__ import annotations

import asyncio
import ctypes
import gc
import itertools
import logging
import time
from collections import Counter, deque
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

log = logging.getLogger(__name__)

SAMPLE_S = 60.0
# Сутки замеров раз в минуту.
MAX_SAMPLES = 1440
TRIM_EVERY = 5
JUMP_KB = 40 * 1024
# Завершённые между замерами запросы дольше порога попадают в `memory_jump`.
SLOW_S = 1.0
MAX_FINISHED = 20
TOP_TYPES = 25


@dataclass(frozen=True)
class ProcStatus:
    rss_kb: int
    hwm_kb: int
    threads: int


def parse_status(text: str) -> ProcStatus:
    fields: dict[str, int] = {}
    for line in text.splitlines():
        key, _, value = line.partition(":")
        if key in ("VmRSS", "VmHWM", "Threads"):
            fields[key] = int(value.split()[0])
    return ProcStatus(fields["VmRSS"], fields["VmHWM"], fields["Threads"])


def read_proc_status() -> str:
    return Path("/proc/self/status").read_text(encoding="ascii")


def load_malloc_trim() -> Callable[[], int] | None:
    """`malloc_trim(0)` из glibc; нет glibc (musl, не Linux) — `None`."""
    try:
        fn = ctypes.CDLL("libc.so.6").malloc_trim
    except (OSError, AttributeError):
        log.debug("malloc_trim unavailable, trimming disabled")
        return None
    fn.argtypes = [ctypes.c_size_t]
    fn.restype = ctypes.c_int
    return lambda: int(fn(0))


def top_types(limit: int = TOP_TYPES) -> list[tuple[str, int]]:
    """Объекты под присмотром сборщика мусора по типам, самые частые."""
    return Counter(type(o).__qualname__ for o in gc.get_objects()).most_common(limit)


@dataclass
class _Request:
    method: str
    path: str
    started: float
    scope: MutableMapping[str, Any] | None


def _path(req: _Request) -> str:
    route = req.scope.get("route") if req.scope is not None else None
    return getattr(route, "path", None) or req.path


class InFlight:
    """HTTP-запросы в работе: метод, шаблон пути (после маршрутизации) и начало; и завершённые
    дольше `SLOW_S` — до `take_finished`."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._requests: dict[int, _Request] = {}
        self._finished: deque[tuple[str, str, float]] = deque(maxlen=MAX_FINISHED)

    def start(
        self, key: int, method: str, path: str, scope: MutableMapping[str, Any] | None = None
    ) -> None:
        self._requests[key] = _Request(method, path, self._clock(), scope)

    def finish(self, key: int) -> None:
        req = self._requests.pop(key, None)
        if req is None:
            return
        took = self._clock() - req.started
        if took >= SLOW_S:
            self._finished.append((req.method, _path(req), took))

    def snapshot(self) -> list[tuple[str, str, float]]:
        now = self._clock()
        return [(r.method, _path(r), now - r.started) for r in list(self._requests.values())]

    def take_finished(self) -> list[tuple[str, str, float]]:
        out = list(self._finished)
        self._finished.clear()
        return out


class InFlightMiddleware:
    def __init__(self, app: ASGIApp, inflight: InFlight) -> None:
        self.app = app
        self.inflight = inflight
        self._ids = itertools.count()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        key = next(self._ids)
        self.inflight.start(key, scope["method"], scope["path"], scope)
        try:
            await self.app(scope, receive, send)
        finally:
            self.inflight.finish(key)


def kb_to_mb(kb: int) -> float:
    return round(kb / 1024, 1)


def _requests(items: list[tuple[str, str, float]]) -> str:
    # Потоки SSE длятся часами, к скачку отношения не имеют.
    return "; ".join(
        f"{method} {path} {took:.1f}s"
        for method, path, took in items
        if not path.endswith("/events")
    )


class MemWatch:
    def __init__(
        self,
        *,
        read_status: Callable[[], str] = read_proc_status,
        trim_fn: Callable[[], int] | None = None,
        max_samples: int = MAX_SAMPLES,
    ) -> None:
        self._read = read_status
        self._trim_fn = trim_fn
        self.inflight = InFlight()
        # (unix time, RSS в КБ)
        self.samples: deque[tuple[float, int]] = deque(maxlen=max_samples)
        # Точка отсчёта скачка: прошлый замер или RSS после malloc_trim.
        self._baseline_kb: int | None = None
        self.trim_last_freed_kb: int | None = None
        self.trim_total_freed_kb = 0
        self.trim_last_at: float | None = None

    def status(self) -> ProcStatus:
        return parse_status(self._read())

    def sample(self) -> None:
        rss = self.status().rss_kb
        prev, self._baseline_kb = self._baseline_kb, rss
        self.samples.append((time.time(), rss))
        finished = self.inflight.take_finished()
        if prev is not None and rss - prev >= JUMP_KB:
            log.warning(
                'memory_jump delta_mb=%.1f rss_mb=%.1f inflight="%s" finished="%s"',
                kb_to_mb(rss - prev),
                kb_to_mb(rss),
                _requests(self.inflight.snapshot()),
                _requests(finished),
            )

    def trim(self) -> None:
        if self._trim_fn is None:
            return
        before = self.status().rss_kb
        self._trim_fn()
        after = self.status().rss_kb
        freed = max(0, before - after)
        if self._baseline_kb is not None:
            self._baseline_kb = min(self._baseline_kb, after)
        self.trim_last_freed_kb = freed
        self.trim_total_freed_kb += freed
        self.trim_last_at = time.time()

    async def run(self) -> None:
        for n in itertools.count(1):
            self.sample()
            if n % TRIM_EVERY == 0:
                self.trim()
            await asyncio.sleep(SAMPLE_S)

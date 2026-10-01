from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

log = logging.getLogger(__name__)

# Задержка цикла больше порога — предупреждение (раздел 4.2 спеки: пауза цикла должна быть короче
# запаса ограды аренды в 3 с); не чаще раза в `WARN_EVERY_S`, подавленные — счётчиком в следующем.
WARN_LAG_MS = 1000.0
WARN_EVERY_S = 60.0


class LoopLagMonitor:
    def __init__(self, interval_s: float = 0.5, window_s: float = 60.0) -> None:
        self._interval = interval_s
        self._window = window_s
        self._samples: deque[tuple[float, float]] = deque()
        self._warned_at: float | None = None
        self._suppressed = 0

    @property
    def lag_ms(self) -> float:
        cutoff = time.monotonic() - self._window
        return max((lag for at, lag in self._samples if at >= cutoff), default=0.0)

    async def run(self) -> None:
        while True:
            start = time.monotonic()
            await asyncio.sleep(self._interval)
            now = time.monotonic()
            self.observe(now, max(0.0, (now - start - self._interval) * 1000))

    def observe(self, now: float, lag_ms: float) -> None:
        """Замер задержки цикла `lag_ms` в момент `now` (time.monotonic())."""
        self._samples.append((now, lag_ms))
        while self._samples and self._samples[0][0] < now - self._window:
            self._samples.popleft()
        if lag_ms <= WARN_LAG_MS:
            return
        if self._warned_at is not None and now - self._warned_at < WARN_EVERY_S:
            self._suppressed += 1
            return
        more = (
            f"; ещё {self._suppressed} таких с прошлого предупреждения" if self._suppressed else ""
        )
        log.warning("задержка цикла событий %.0f мс — больше %.0f мс%s", lag_ms, WARN_LAG_MS, more)
        self._warned_at, self._suppressed = now, 0

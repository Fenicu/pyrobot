from __future__ import annotations

import asyncio
import time
from collections import deque


class LoopLagMonitor:
    def __init__(self, interval_s: float = 0.5, window_s: float = 60.0) -> None:
        self._interval = interval_s
        self._window = window_s
        self._samples: deque[tuple[float, float]] = deque()

    @property
    def lag_ms(self) -> float:
        cutoff = time.monotonic() - self._window
        return max((lag for at, lag in self._samples if at >= cutoff), default=0.0)

    async def run(self) -> None:
        while True:
            start = time.monotonic()
            await asyncio.sleep(self._interval)
            now = time.monotonic()
            self._samples.append((now, max(0.0, (now - start - self._interval) * 1000)))
            while self._samples and self._samples[0][0] < now - self._window:
                self._samples.popleft()

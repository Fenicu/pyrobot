from __future__ import annotations

from collections import deque

from app.engine.bus import Delivery
from app.engine.clock import Clock
from app.engine.events import Unrecognized
from app.engine.notify import NotifierPort


class UnrecognizedWatch:
    def __init__(
        self,
        notifier: NotifierPort,
        clock: Clock,
        *,
        threshold: int = 5,
        window_s: float = 600.0,
    ) -> None:
        self._notifier = notifier
        self._clock = clock
        self._threshold = threshold
        self._window = window_s
        self._hits: deque[float] = deque()
        self._quiet_until = float("-inf")

    async def on_delivery(self, delivery: Delivery) -> None:
        if not any(isinstance(e, Unrecognized) for e in delivery.events):
            return
        now = self._clock.monotonic()
        self._hits.append(now)
        while self._hits and self._hits[0] < now - self._window:
            self._hits.popleft()
        if len(self._hits) >= self._threshold and now >= self._quiet_until:
            self._quiet_until = now + self._window
            await self._notifier.notify(
                "warn",
                "unrecognized_spike",
                f"{len(self._hits)} unrecognized game messages in {int(self._window)}s",
            )

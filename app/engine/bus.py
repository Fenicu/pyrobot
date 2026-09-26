from __future__ import annotations

import asyncio
import itertools
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.engine.events import Event
from app.engine.types import IncomingMessage

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Delivery:
    msg: IncomingMessage
    events: tuple[Event, ...]
    state_version: int
    journal_id: int
    reactable: bool = True


Subscriber = Callable[[Delivery], Awaitable[None]]


class Bus:
    def __init__(self, subscriber_timeout_s: float = 5.0) -> None:
        self._subs: list[tuple[int, int, Subscriber]] = []
        self._seq = itertools.count()
        self._timeout = subscriber_timeout_s

    def subscribe(self, fn: Subscriber, *, priority: int = 100) -> None:
        self._subs.append((priority, next(self._seq), fn))
        self._subs.sort(key=lambda item: (item[0], item[1]))

    async def publish(self, delivery: Delivery) -> None:
        for _, _, fn in self._subs:
            name = getattr(fn, "__qualname__", repr(fn))
            try:
                await asyncio.wait_for(fn(delivery), self._timeout)
            except TimeoutError:
                log.warning("subscriber %s exceeded %.1fs", name, self._timeout)
            except Exception:
                log.exception("subscriber %s failed", name)

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class Sent:
    kind: Literal["send", "click"]
    chat_id: int
    payload: str
    message_id: int | None
    at: float


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[Sent] = []
        self.before_send: Callable[[Sent], None] | None = None
        self.responder: Callable[[Sent], Awaitable[None]] | None = None
        self.fail_with: list[BaseException] = []
        self.toast: str | None = None
        self._next_id = 1000
        self._tasks: set[asyncio.Future[None]] = set()

    def _deliver(self, rec: Sent) -> None:
        if self.fail_with:
            raise self.fail_with.pop(0)
        if self.before_send is not None:
            self.before_send(rec)
        self.sent.append(rec)
        if self.responder is not None:
            task = asyncio.ensure_future(self.responder(rec))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def send_text(self, chat_id: int, text: str, reply_to: int | None = None) -> int:
        self._deliver(Sent("send", chat_id, text, None, time.monotonic()))
        self._next_id += 1
        return self._next_id

    async def click(
        self, chat_id: int, message_id: int, data: str, timeout_s: float
    ) -> str | None:
        self._deliver(Sent("click", chat_id, data, message_id, time.monotonic()))
        return self.toast

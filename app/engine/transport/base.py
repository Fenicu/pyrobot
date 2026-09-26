from __future__ import annotations

from typing import Protocol


class FloodWait(Exception):
    def __init__(self, seconds: float) -> None:
        super().__init__(f"flood wait {seconds}s")
        self.seconds = seconds


class TransportAuthLost(Exception):
    pass


class TransportRejected(Exception):
    pass


class Transport(Protocol):
    async def send_text(self, chat_id: int, text: str, reply_to: int | None = None) -> int: ...

    async def click(
        self, chat_id: int, message_id: int, data: str, timeout_s: float
    ) -> str | None: ...

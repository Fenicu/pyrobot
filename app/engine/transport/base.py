from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from app.engine.types import IncomingMessage


# Проверка чата команды: группа или супергруппа, где аккаунт — участник (`ok`).
GroupCheck = Literal["ok", "not_group", "not_member", "unavailable"]


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

    async def forward(self, from_chat_id: int, message_id: int, to_chat_id: int) -> int:
        """Пересылка одного сообщения одной попыткой; id копии в чате назначения (0 —
        неизвестен)."""
        ...

    async def fetch(self, chat_id: int, message_id: int) -> IncomingMessage | None:
        """Текущая версия сообщения из Telegram; None — сообщения нет."""
        ...

    async def check_group(self, chat_id: int) -> GroupCheck:
        """Чат — группа или супергруппа, и аккаунт в ней состоит."""
        ...

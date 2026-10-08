from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from app.engine.types import IncomingMessage


# Проверка чата команды: группа или супергруппа, где аккаунт — участник (`ok`).
GroupCheck = Literal["ok", "not_group", "not_member", "unavailable"]
# Итог вступления в чат: вступил, уже участник, заявка ждёт одобрения админов.
JoinStatus = Literal["joined", "already_member", "request_sent"]
# Username общего чата игры (`chats.swinfo_chat_id`): по числовому id в чат не вступить.
GAME_CHAT_USERNAME = "startupwarschat"
# Username чата мандаринов (`chats.tangerine_chat_id`): в него вступают перед своим сообщением.
TANGERINE_CHAT_USERNAME = "mandarinkaSW"


@dataclass(frozen=True, slots=True)
class GroupInfo:
    """Итог проверки чата команды и его название в Telegram (None — чат не прочитан): по
    названию в логе и журнале действия видно опечатку в ID."""

    verdict: GroupCheck
    title: str | None = None


@dataclass(frozen=True, slots=True)
class Sender:
    """Автор сообщения в чате — пользователь Telegram."""

    tg_user_id: int
    first_name: str | None
    last_name: str | None = None
    username: str | None = None


class FloodWait(Exception):
    def __init__(self, seconds: float) -> None:
        super().__init__(f"flood wait {seconds}s")
        self.seconds = seconds


class TransportAuthLost(Exception):
    pass


class TransportRejected(Exception):
    pass


class ChatUnavailable(Exception):
    """Чат не читается: пир неизвестен (аккаунт не состоит в чате), чат закрыт или аккаунт из
    него исключён; `reason` — имя ошибки Telegram."""

    def __init__(self, chat_id: int, reason: str) -> None:
        super().__init__(f"chat {chat_id} unavailable: {reason}")
        self.chat_id = chat_id
        self.reason = reason


class Transport(Protocol):
    async def resolve(self, chat_id: int) -> None:
        """Разрешить peer чата заранее (с кешем): send_text и click после этого вызывают RPC без
        ожидания, и шлюз проверяет команду вплотную перед отправкой."""
        ...

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

    async def check_group(self, chat_id: int) -> GroupInfo:
        """Чат — группа или супергруппа, и аккаунт в ней состоит; название чата."""
        ...

    async def join_chat(self, username: str, expect_id: int) -> JoinStatus:
        """Вступление в публичный чат по username; чат с другим id — отказ `chat_mismatch`
        без вступления."""
        ...

    async def message_sender(self, chat_id: int, message_id: int) -> Sender | None:
        """Автор сообщения в чате; None — сообщения нет (удалено) или его автор не
        пользователь. Чат не читается — `ChatUnavailable`."""
        ...

    async def send_saved(self, text: str) -> None:
        """Отправка сообщения в «Избранное» (Saved Messages) текущего аккаунта."""
        ...

    async def send_chat_message(self, chat_id: int, text: str) -> int:
        """Сообщение в чат одной попыткой, мимо шлюза команд; id отправленного сообщения."""
        ...

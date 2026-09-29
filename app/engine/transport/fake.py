from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from app.engine.tg_auth import InvalidCode, InvalidPassword, PasswordRequired
from app.engine.transport.base import GroupCheck
from app.engine.types import IncomingMessage


@dataclass(frozen=True, slots=True)
class Sent:
    kind: Literal["send", "click", "forward"]
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
        self.messages: dict[tuple[int, int], IncomingMessage] = {}
        # Перечитывания сообщений (`fetch`) и ошибки, которыми падают очередные из них.
        self.fetches: list[tuple[int, int]] = []
        self.fetch_fail_with: list[BaseException] = []
        # Выполняется посреди чтения: что меняется, пока ответ Telegram в пути.
        self.on_fetch: Callable[[], Awaitable[None]] | None = None
        # Проверка чатов команды: итог по чату (по умолчанию — участник группы) и вызовы.
        self.groups: dict[int, GroupCheck] = {}
        self.group_error: BaseException | None = None
        # Ошибки, которыми падают очередные проверки (раньше `group_error`), и моменты проверок.
        self.group_fail_with: list[BaseException] = []
        self.group_checks: list[int] = []
        self.group_checked_at: list[float] = []
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

    async def forward(self, from_chat_id: int, message_id: int, to_chat_id: int) -> int:
        self._deliver(Sent("forward", to_chat_id, str(from_chat_id), message_id, time.monotonic()))
        self._next_id += 1
        return self._next_id

    async def fetch(self, chat_id: int, message_id: int) -> IncomingMessage | None:
        self.fetches.append((chat_id, message_id))
        if self.on_fetch is not None:
            await self.on_fetch()
        if self.fetch_fail_with:
            raise self.fetch_fail_with.pop(0)
        return self.messages.get((chat_id, message_id))

    async def check_group(self, chat_id: int) -> GroupCheck:
        self.group_checks.append(chat_id)
        self.group_checked_at.append(time.monotonic())
        if self.group_fail_with:
            raise self.group_fail_with.pop(0)
        if self.group_error is not None:
            raise self.group_error
        return self.groups.get(chat_id, "ok")


class FakeTgBackend:
    def __init__(
        self,
        *,
        authorized: bool = False,
        user_id: int = 267519921,
        password: str | None = None,
        code: str = "12345",
    ) -> None:
        self.authorized = authorized
        self.user_id = user_id
        self.password = password
        self.code = code
        self.logged_out = False
        self.online = False
        self._code_ok = False

    async def connect(self) -> bool:
        return self.authorized

    async def send_code(self, phone: str) -> str:
        return "hash"

    async def sign_in(self, phone: str, code_hash: str, code: str) -> int:
        if code != self.code:
            raise InvalidCode
        self._code_ok = True
        if self.password is not None:
            raise PasswordRequired
        self.authorized = True
        return self.user_id

    async def check_password(self, password: str) -> int:
        if not self._code_ok or password != self.password:
            raise InvalidPassword
        self.authorized = True
        return self.user_id

    async def identify(self) -> int:
        return self.user_id

    async def go_online(self) -> None:
        self.online = True

    async def log_out(self) -> None:
        self.logged_out = True
        self.authorized = False
        self.online = False

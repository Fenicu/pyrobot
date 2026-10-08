from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from app.engine.tg_auth import (
    InvalidCode,
    InvalidPassword,
    PasswordRequired,
    SentCodeInfo,
)
from app.engine.transport.base import (
    GroupCheck,
    GroupInfo,
    JoinStatus,
    Sender,
    TransportRejected,
)
from app.engine.types import IncomingMessage


@dataclass(frozen=True, slots=True)
class Sent:
    kind: Literal["send", "click", "forward", "inline"]
    chat_id: int
    payload: str
    message_id: int | None
    at: float


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[Sent] = []
        self.saved: list[str] = []
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
        self.titles: dict[int, str] = {}
        self.group_error: BaseException | None = None
        # Ошибки, которыми падают очередные проверки (раньше `group_error`), и моменты проверок.
        self.group_fail_with: list[BaseException] = []
        self.group_checks: list[int] = []
        self.group_checked_at: list[float] = []
        # Разрешения peer перед отправкой и что меняется, пока peer разрешается.
        self.resolved: list[int] = []
        self.on_resolve: Callable[[int], Awaitable[None]] | None = None
        # Вступления в чаты (username, ожидаемый id), их итог и ошибки очередных вступлений.
        self.joins: list[tuple[str, int]] = []
        self.join_status: JoinStatus = "joined"
        self.join_fail_with: list[BaseException] = []
        # Настоящий id чата по username: другой ожидаемый id — отказ `chat_mismatch`.
        self.chat_ids: dict[str, int] = {}
        # Сообщения в чаты мимо шлюза (`send_chat_message`): чат и текст.
        self.posted: list[tuple[int, str]] = []
        # Авторы сообщений по (чат, id), запросы автора и ошибки очередных запросов.
        self.senders: dict[tuple[int, int], Sender] = {}
        self.sender_lookups: list[tuple[int, int]] = []
        self.sender_fail_with: list[BaseException] = []
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

    async def resolve(self, chat_id: int) -> None:
        self.resolved.append(chat_id)
        if self.on_resolve is not None:
            await self.on_resolve(chat_id)

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

    async def send_inline(self, bot_id: int, chat_id: int, query: str) -> int:
        self._deliver(Sent("inline", chat_id, query, None, time.monotonic()))
        self._next_id += 1
        return self._next_id

    async def fetch(self, chat_id: int, message_id: int) -> IncomingMessage | None:
        self.fetches.append((chat_id, message_id))
        if self.on_fetch is not None:
            await self.on_fetch()
        if self.fetch_fail_with:
            raise self.fetch_fail_with.pop(0)
        return self.messages.get((chat_id, message_id))

    async def message_sender(self, chat_id: int, message_id: int) -> Sender | None:
        self.sender_lookups.append((chat_id, message_id))
        if self.sender_fail_with:
            raise self.sender_fail_with.pop(0)
        return self.senders.get((chat_id, message_id))

    async def check_group(self, chat_id: int) -> GroupInfo:
        self.group_checks.append(chat_id)
        self.group_checked_at.append(time.monotonic())
        if self.group_fail_with:
            raise self.group_fail_with.pop(0)
        if self.group_error is not None:
            raise self.group_error
        return GroupInfo(self.groups.get(chat_id, "ok"), self.titles.get(chat_id))

    async def join_chat(self, username: str, expect_id: int) -> JoinStatus:
        if self.join_fail_with:
            raise self.join_fail_with.pop(0)
        if self.chat_ids.get(username, expect_id) != expect_id:
            raise TransportRejected("chat_mismatch")
        self.joins.append((username, expect_id))
        return self.join_status

    async def send_saved(self, text: str) -> None:
        if self.fail_with:
            raise self.fail_with.pop(0)
        self.saved.append(text)

    async def send_chat_message(self, chat_id: int, text: str) -> int:
        if self.fail_with:
            raise self.fail_with.pop(0)
        self.posted.append((chat_id, text))
        self._next_id += 1
        return self._next_id


class FakeTgBackend:
    def __init__(
        self,
        *,
        authorized: bool = False,
        user_id: int = 267519921,
        password: str | None = None,
        code: str = "12345",
        sent_code_info: SentCodeInfo | None = None,
        email_code: str = "54321",
    ) -> None:
        self.authorized = authorized
        self.user_id = user_id
        self.password = password
        self.code = code
        self.sent_code_info = sent_code_info
        self.email_code = email_code
        self.logged_out = False
        self.online = False
        self.connected = False
        self._code_ok = False
        self.resend_calls: list[tuple[str, str]] = []
        self.send_email_calls: list[tuple[str, str, str]] = []
        self.verify_email_calls: list[tuple[str, str, str]] = []
        self.sign_in_calls: list[tuple[str, str, str, bool]] = []
        # Ответ на подтверждение почты и маска адреса, которую вернёт Telegram.
        self.after_email: int | SentCodeInfo = SentCodeInfo(
            phone_code_hash="hash_after_email", type="app"
        )
        self.email_pattern: str | None = "t***@e***.com"
        # Сбой очередного вызова по имени метода (`resend_code`, `sign_in`, …).
        self.errors: dict[str, BaseException] = {}

    def _fail(self, name: str) -> None:
        err = self.errors.pop(name, None)
        if err is not None:
            raise err

    async def connect(self) -> bool:
        self.connected = True
        return self.authorized

    async def send_code(self, phone: str) -> SentCodeInfo | str:
        if self.sent_code_info is not None:
            return self.sent_code_info
        return SentCodeInfo(phone_code_hash="hash", type="app")

    async def resend_code(self, phone: str, code_hash: str) -> SentCodeInfo:
        self.resend_calls.append((phone, code_hash))
        self._fail("resend_code")
        return SentCodeInfo(phone_code_hash="hash_resent", type="sms", timeout=60)

    async def send_verify_email_code(self, phone: str, code_hash: str, email: str) -> str | None:
        self.send_email_calls.append((phone, code_hash, email))
        self._fail("send_verify_email_code")
        return self.email_pattern

    async def verify_email(self, phone: str, code_hash: str, code: str) -> int | SentCodeInfo:
        self.verify_email_calls.append((phone, code_hash, code))
        self._fail("verify_email")
        if code != self.email_code:
            raise InvalidCode
        return self.after_email

    async def sign_in(
        self, phone: str, code_hash: str, code: str, *, is_email: bool = False
    ) -> int:
        self.sign_in_calls.append((phone, code_hash, code, is_email))
        self._fail("sign_in")
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

    async def disconnect(self) -> None:
        self.connected = False
        self.online = False

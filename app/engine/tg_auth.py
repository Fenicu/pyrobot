from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from app.engine.notify import NotifierPort
from app.engine.transport.base import FloodWait, TransportAuthLost

if TYPE_CHECKING:
    from app.engine.host.codes import CodeLimiter

log = logging.getLogger(__name__)


class TgAuthError(Exception):
    code = "tg_auth_error"


class PasswordRequired(TgAuthError):
    code = "password_required"


class InvalidCode(TgAuthError):
    code = "invalid_code"


class CodeExpired(TgAuthError):
    code = "code_expired"


class InvalidPassword(TgAuthError):
    code = "invalid_password"


class SignUpRequired(TgAuthError):
    code = "signup_required"


class InvalidPhone(TgAuthError):
    code = "invalid_phone"


class SendCodeRejected(TgAuthError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class AttemptMismatch(TgAuthError):
    code = "attempt_mismatch"


class TgBackendError(TgAuthError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class TgUserTaken(TgAuthError):
    """Пользователь Telegram уже привязан к другому аккаунту."""

    code = "tg_user_taken"


class TgLoggedIn(TgAuthError):
    code = "tg_logged_in"


class CodeRateLimited(TgAuthError):
    """Запросов кода входа больше лимита хоста или аккаунта: следующий — через
    `retry_after_s` секунд."""

    code = "tg_code_rate_limited"

    def __init__(self, retry_after_s: float) -> None:
        super().__init__(f"retry after {retry_after_s:.0f} s")
        self.retry_after_s = retry_after_s


class TgState(StrEnum):
    UNAUTHORIZED = "unauthorized"
    AWAITING_CODE = "awaiting_code"
    AWAITING_PASSWORD = "awaiting_password"
    AWAITING_EMAIL = "awaiting_email"
    AWAITING_EMAIL_CODE = "awaiting_email_code"
    ONLINE = "online"
    # Перегрузка обновлениями: транспорт остановил приём и подключится сам, вход сохранён.
    OVERLOAD = "overload"
    ERROR = "error"
    # Движок аккаунта не запущен: состояние входа неизвестно (только в API).
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class SentCodeInfo:
    phone_code_hash: str
    type: str = "app"
    email_pattern: str | None = None
    next_type: str | None = None
    timeout: int | None = None


@dataclass(frozen=True, slots=True)
class TgStatus:
    state: TgState
    user_id: int | None = None
    attempt_id: str | None = None
    error: str | None = None
    # Пользователь Telegram, к которому привязан аккаунт (`accounts.tg_user_id`); выход не снимает.
    bound_user_id: int | None = None
    delivery_type: str | None = None
    delivery_email_pattern: str | None = None
    delivery_next_type: str | None = None
    delivery_timeout: int | None = None
    delivery_expires_at: float | None = None


class TgAuthBackend(Protocol):
    async def connect(self) -> bool: ...
    async def send_code(self, phone: str) -> SentCodeInfo | str: ...
    async def sign_in(
        self, phone: str, code_hash: str, code: str, *, is_email: bool = False
    ) -> int: ...
    async def check_password(self, password: str) -> int: ...
    async def identify(self) -> int: ...
    async def go_online(self) -> None: ...
    async def log_out(self) -> None: ...
    # Отключение без выхода: сессия остаётся, следующий `connect()` подключает заново.
    async def disconnect(self) -> None: ...
    async def resend_code(self, phone: str, code_hash: str) -> SentCodeInfo: ...
    async def send_verify_email_code(
        self, phone: str, code_hash: str, email: str
    ) -> str | None: ...
    async def verify_email(self, phone: str, code_hash: str, code: str) -> int | SentCodeInfo: ...


@dataclass
class _Attempt:
    id: str
    owner: str
    phone: str
    code_hash: str
    expires: float
    code_type: str = "app"
    email_pattern: str | None = None
    next_type: str | None = None
    timeout: int | None = None
    timeout_at: float | None = None


class TgAuthManager:
    """Вход аккаунта `account_id` в Telegram. Перед каждым выходом в онлайн (`_accept`) —
    привязка (`bind` пишет её и отвечает привязкой из базы, пользователь другого аккаунта —
    `TgUserTaken`) и свой чат в настройках (`self_chat` — поля `chats.*`, равные пользователю).
    Запросы кода — через лимит процесса `codes`."""

    def __init__(
        self,
        backend: TgAuthBackend,
        *,
        expected_user_id: int | None,
        bind: Callable[[int], Awaitable[int]],
        self_chat: Callable[[int], list[str]],
        codes: CodeLimiter | None = None,
        account_id: int,
        attempt_ttl_s: float = 600.0,
        notifier: NotifierPort | None = None,
    ) -> None:
        self._backend = backend
        self._notifier = notifier
        # None — аккаунт не привязан: первый вход привязывает, дальше пускается только он.
        self._expected = expected_user_id
        self._bind = bind
        self._self_chat = self_chat
        self._codes = codes
        self._account_id = account_id
        self._ttl = attempt_ttl_s
        self._lock = asyncio.Lock()
        self._state = TgState.UNAUTHORIZED
        self._user_id: int | None = None
        self._error: str | None = None
        self._attempt: _Attempt | None = None
        self._callbacks: list[Callable[[], Awaitable[None]]] = []

    def status(self) -> TgStatus:
        att = self._attempt
        return TgStatus(
            self._state,
            self._user_id,
            att.id if att is not None else None,
            self._error,
            self._expected,
            delivery_type=att.code_type if att is not None else None,
            delivery_email_pattern=att.email_pattern if att is not None else None,
            delivery_next_type=att.next_type if att is not None else None,
            delivery_timeout=att.timeout if att is not None else None,
            delivery_expires_at=att.timeout_at if att is not None else None,
        )

    def on_online(self, cb: Callable[[], Awaitable[None]]) -> None:
        self._callbacks.append(cb)

    async def boot(self) -> TgStatus:
        # TransportAuthLost обрабатываем сами, а не через on_auth_lost/mark_lost: тот
        # захватывает этот же self._lock, а мы уже держим его здесь — реентерабельности
        # у asyncio.Lock нет, повторный захват — дедлок.
        auth_lost = False
        async with self._lock:
            try:
                authorized = await self._backend.connect()
                if authorized:
                    await self._accept(await self._backend.identify())
                else:
                    self._set(TgState.UNAUTHORIZED)
            except TransportAuthLost:
                auth_lost = True
                self._user_id = None
                self._set(TgState.UNAUTHORIZED, error="session_revoked")
            except Exception:
                log.exception("telegram boot failed")
                self._set(TgState.ERROR, error="connect_failed")
        if auth_lost and self._notifier is not None:
            await self._notifier.notify(
                "error",
                "tg_auth_lost",
                "telegram session was revoked while pyrobot was stopped; login again in admin",
            )
        return self.status()

    async def start(self, phone: str, owner: str) -> TgStatus:
        async with self._lock:
            if self._state in (TgState.ONLINE, TgState.OVERLOAD):
                raise AttemptMismatch("already online")
            active = self._attempt
            if active is not None and active.owner != owner and active.expires > time.monotonic():
                raise AttemptMismatch("another login in progress")
            self._take_code()
            try:
                await self._backend.connect()
                sent = await self._backend.send_code(phone)
            except TgAuthError as exc:
                self._attempt = None
                self._set(TgState.ERROR, error=exc.code)
                raise
            except FloodWait:
                self._attempt = None
                self._set(TgState.ERROR, error="flood_wait")
                raise
            except Exception as exc:
                log.exception("telegram send_code failed")
                self._attempt = None
                self._set(TgState.ERROR, error="send_code_failed")
                raise TgBackendError("send_code_failed") from exc

            if isinstance(sent, SentCodeInfo):
                code_hash = sent.phone_code_hash
                code_type = sent.type
                email_pattern = sent.email_pattern
                next_type = sent.next_type
                timeout = sent.timeout
            else:
                code_hash = str(sent)
                code_type = "app"
                email_pattern = None
                next_type = None
                timeout = None

            timeout_at = time.time() + timeout if timeout is not None else None
            self._attempt = _Attempt(
                uuid.uuid4().hex,
                owner,
                phone,
                code_hash,
                time.monotonic() + self._ttl,
                code_type=code_type,
                email_pattern=email_pattern,
                next_type=next_type,
                timeout=timeout,
                timeout_at=timeout_at,
            )
            if code_type == "setup_email":
                self._set(TgState.AWAITING_EMAIL)
            else:
                self._set(TgState.AWAITING_CODE)
            return self.status()

    async def submit_code(self, attempt_id: str, owner: str, code: str) -> TgStatus:
        async with self._lock:
            attempt = self._check(attempt_id, owner, TgState.AWAITING_CODE)
            is_email = attempt.code_type == "email"
            try:
                user_id = await self._backend.sign_in(
                    attempt.phone, attempt.code_hash, code, is_email=is_email
                )
            except PasswordRequired:
                self._set(TgState.AWAITING_PASSWORD)
                return self.status()
            except InvalidCode:
                self._error = "invalid_code"
                return self.status()
            except CodeExpired:
                self._attempt = None
                self._set(TgState.UNAUTHORIZED, error="code_expired")
                return self.status()
            except SignUpRequired:
                self._attempt = None
                self._set(TgState.ERROR, error="signup_required")
                return self.status()
            except TgAuthError:
                raise
            except Exception as exc:
                log.exception("telegram sign_in failed")
                raise TgBackendError("sign_in_failed") from exc
            self._attempt = None
            await self._accept(user_id)
            return self.status()

    async def resend_code(self, attempt_id: str, owner: str) -> TgStatus:
        async with self._lock:
            attempt = self._check(attempt_id, owner, TgState.AWAITING_CODE)
            self._take_code()
            try:
                sent = await self._backend.resend_code(attempt.phone, attempt.code_hash)
            except FloodWait as exc:
                raise exc
            except TgAuthError as exc:
                self._error = exc.code
                return self.status()
            except Exception as exc:
                log.exception("telegram resend_code failed")
                raise TgBackendError("resend_code_failed") from exc

            timeout = sent.timeout
            timeout_at = time.time() + timeout if timeout is not None else None
            attempt.code_hash = sent.phone_code_hash
            attempt.code_type = sent.type
            attempt.email_pattern = sent.email_pattern
            attempt.next_type = sent.next_type
            attempt.timeout = timeout
            attempt.timeout_at = timeout_at
            self._error = None
            return self.status()

    async def send_email(self, attempt_id: str, owner: str, email: str) -> TgStatus:
        async with self._lock:
            attempt = self._check(attempt_id, owner, TgState.AWAITING_EMAIL)
            self._take_code()
            try:
                pattern = await self._backend.send_verify_email_code(
                    attempt.phone, attempt.code_hash, email
                )
            except FloodWait as exc:
                raise exc
            except TgAuthError as exc:
                self._error = exc.code
                return self.status()
            except Exception as exc:
                log.exception("telegram send_verify_email_code failed")
                raise TgBackendError("send_verify_email_code_failed") from exc

            attempt.email_pattern = pattern or email
            self._error = None
            self._set(TgState.AWAITING_EMAIL_CODE)
            return self.status()

    async def submit_email_code(self, attempt_id: str, owner: str, code: str) -> TgStatus:
        async with self._lock:
            attempt = self._check(attempt_id, owner, TgState.AWAITING_EMAIL_CODE)
            try:
                res = await self._backend.verify_email(attempt.phone, attempt.code_hash, code)
            except InvalidCode:
                self._error = "invalid_code"
                return self.status()
            except CodeExpired:
                self._attempt = None
                self._set(TgState.UNAUTHORIZED, error="code_expired")
                return self.status()
            except TgAuthError as exc:
                self._error = exc.code
                return self.status()
            except Exception as exc:
                log.exception("telegram verify_email failed")
                raise TgBackendError("verify_email_failed") from exc

            if isinstance(res, int):
                self._attempt = None
                await self._accept(res)
                return self.status()

            timeout = res.timeout
            timeout_at = time.time() + timeout if timeout is not None else None
            attempt.code_hash = res.phone_code_hash
            attempt.code_type = res.type
            attempt.email_pattern = res.email_pattern
            attempt.next_type = res.next_type
            attempt.timeout = timeout
            attempt.timeout_at = timeout_at
            self._error = None
            self._set(TgState.AWAITING_CODE)
            return self.status()

    async def submit_password(self, attempt_id: str, owner: str, password: str) -> TgStatus:
        async with self._lock:
            self._check(attempt_id, owner, TgState.AWAITING_PASSWORD)
            try:
                user_id = await self._backend.check_password(password)
            except InvalidPassword:
                self._error = "invalid_password"
                return self.status()
            except TgAuthError:
                raise
            except Exception as exc:
                log.exception("telegram check_password failed")
                raise TgBackendError("check_password_failed") from exc
            self._attempt = None
            await self._accept(user_id)
            return self.status()

    async def logout(self) -> TgStatus:
        async with self._lock:
            self._attempt = None
            self._user_id = None
            try:
                await self._backend.log_out()
            except Exception:
                log.exception("logout failed")
                self._set(TgState.ERROR, error="logout_failed")
                return self.status()
            self._set(TgState.UNAUTHORIZED)
            return self.status()

    async def drop_attempt(self) -> None:
        async with self._lock:
            self._attempt = None
            self._user_id = None
            self._set(TgState.UNAUTHORIZED)

    async def mark_lost(self) -> None:
        async with self._lock:
            was_online = self._state in (TgState.ONLINE, TgState.OVERLOAD)
            self._user_id = None
            self._set(TgState.UNAUTHORIZED, error="session_revoked")
        if was_online and self._notifier is not None:
            await self._notifier.notify(
                "error", "tg_auth_lost", "telegram session revoked; login again in admin"
            )

    async def mark_overload(self) -> None:
        """Перегрузка обновлениями (раздел 4.2 спеки): транспорт остановил приём, вход сохранён.
        Только из онлайна — статус `overload` и одно предупреждение на перегрузку; перегрузка
        сразу после выхода в онлайн ждёт, пока вход его закончит."""
        async with self._lock:
            if self._state is not TgState.ONLINE:
                return
            self._set(TgState.OVERLOAD)
        if self._notifier is not None:
            await self._notifier.notify(
                "warn",
                "account_overload",
                "too many telegram updates queued; intake paused until the backlog is processed",
            )

    async def mark_resumed(self) -> None:
        """Транспорт вышел из перегрузки: клиент снова онлайн."""
        async with self._lock:
            if self._state is TgState.OVERLOAD:
                self._set(TgState.ONLINE)

    def _take_code(self) -> None:
        """Запрос кода (SMS, звонок, письмо) — через общий лимит: сверх — `CodeRateLimited`."""
        if self._codes is not None:
            wait = self._codes.take(self._account_id)
            if wait is not None:
                raise CodeRateLimited(wait)

    def _check(self, attempt_id: str, owner: str, state: TgState) -> _Attempt:
        attempt = self._attempt
        if attempt is None or attempt.id != attempt_id or attempt.owner != owner:
            raise AttemptMismatch("unknown attempt")
        if self._state is not state:
            raise AttemptMismatch(f"state is {self._state}")
        return attempt

    async def _accept(self, user_id: int) -> None:
        """Вошедший пользователь `user_id` (раздел 4.3 спеки): всё — до `go_online`, отказ в
        онлайн не выпускает, и обновления не обрабатываются. Другой пользователь (и по привязке
        в базе) или пользователь другого аккаунта — выход из сессии; свой чат в настройках или
        сбой записи привязки — сессия остаётся, клиент отключается: исправить настройки и
        перезапустить аккаунт."""
        self._user_id = None
        if self._expected is not None and user_id != self._expected:
            await self._refuse("unexpected_user")
            return
        if self._expected is None:
            try:
                bound = await self._bind(user_id)
            except TgUserTaken:
                await self._refuse("tg_user_taken")
                return
            except Exception:
                # Не записалась — не проверено, что пользователь не занят: в онлайн нельзя.
                log.exception("telegram account binding not persisted")
                await self._hold("bind_failed")
                return
            # В базе аккаунт уже привязан к другому: снимок аккаунта при старте устарел.
            self._expected = bound
            if bound != user_id:
                await self._refuse("unexpected_user")
                return
        fields = self._self_chat(user_id)
        if fields:
            await self._hold("chat_is_self")
            if self._notifier is not None:
                await self._notifier.notify(
                    "warn",
                    "chat_is_self",
                    f"telegram user {user_id} is set as a chat in settings "
                    f"({', '.join(fields)}); fix settings and restart the account",
                )
            return
        try:
            await self._backend.go_online()
        except Exception:
            log.exception("go_online failed")
            self._set(TgState.ERROR, error="online_failed")
            return
        self._user_id = user_id
        self._set(TgState.ONLINE)
        for cb in self._callbacks:
            try:
                await cb()
            except Exception:
                log.exception("online callback failed")

    async def _hold(self, error: str) -> None:
        """Отказ в онлайне без выхода: сессия остаётся, клиент отключается — подключённый, он
        копил бы обновления, которые некому разбирать."""
        self._set(TgState.ERROR, error=error)
        try:
            await self._backend.disconnect()
        except Exception:
            log.exception("telegram client not disconnected")

    async def _refuse(self, error: str) -> None:
        """Отказ во входе с выходом из сессии: обновления этого пользователя не обработаются."""
        try:
            await self._backend.log_out()
        except Exception:
            log.exception("log_out of refused telegram user failed")
        self._set(TgState.ERROR, error=error)

    def _set(self, state: TgState, *, error: str | None = None) -> None:
        self._state = state
        self._error = error

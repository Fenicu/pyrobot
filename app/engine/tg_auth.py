from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.engine.notify import NotifierPort
from app.engine.transport.base import FloodWait, TransportAuthLost

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


class TgState(StrEnum):
    UNAUTHORIZED = "unauthorized"
    AWAITING_CODE = "awaiting_code"
    AWAITING_PASSWORD = "awaiting_password"
    ONLINE = "online"
    # Перегрузка обновлениями: транспорт остановил приём и подключится сам, вход сохранён.
    OVERLOAD = "overload"
    ERROR = "error"
    # Движок аккаунта не запущен: состояние входа неизвестно (только в API).
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class TgStatus:
    state: TgState
    user_id: int | None = None
    attempt_id: str | None = None
    error: str | None = None
    # Пользователь Telegram, к которому привязан аккаунт (`accounts.tg_user_id`); выход не снимает.
    bound_user_id: int | None = None


class TgAuthBackend(Protocol):
    async def connect(self) -> bool: ...
    async def send_code(self, phone: str) -> str: ...
    async def sign_in(self, phone: str, code_hash: str, code: str) -> int: ...
    async def check_password(self, password: str) -> int: ...
    async def identify(self) -> int: ...
    async def go_online(self) -> None: ...
    async def log_out(self) -> None: ...


@dataclass
class _Attempt:
    id: str
    owner: str
    phone: str
    code_hash: str
    expires: float


class TgAuthManager:
    def __init__(
        self,
        backend: TgAuthBackend,
        *,
        expected_user_id: int | None,
        on_bind: Callable[[int], Awaitable[None]] | None = None,
        attempt_ttl_s: float = 600.0,
        notifier: NotifierPort | None = None,
    ) -> None:
        self._backend = backend
        self._notifier = notifier
        # None — аккаунт не привязан: первый вход привязывает, дальше пускается только он.
        self._expected = expected_user_id
        self._on_bind = on_bind
        self._ttl = attempt_ttl_s
        self._lock = asyncio.Lock()
        self._state = TgState.UNAUTHORIZED
        self._user_id: int | None = None
        self._error: str | None = None
        self._attempt: _Attempt | None = None
        self._callbacks: list[Callable[[], Awaitable[None]]] = []

    def status(self) -> TgStatus:
        attempt_id = self._attempt.id if self._attempt else None
        return TgStatus(self._state, self._user_id, attempt_id, self._error, self._expected)

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
            try:
                await self._backend.connect()
                code_hash = await self._backend.send_code(phone)
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
            self._attempt = _Attempt(
                uuid.uuid4().hex, owner, phone, code_hash, time.monotonic() + self._ttl
            )
            self._set(TgState.AWAITING_CODE)
            return self.status()

    async def submit_code(self, attempt_id: str, owner: str, code: str) -> TgStatus:
        async with self._lock:
            attempt = self._check(attempt_id, owner, TgState.AWAITING_CODE)
            try:
                user_id = await self._backend.sign_in(attempt.phone, attempt.code_hash, code)
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

    def _check(self, attempt_id: str, owner: str, state: TgState) -> _Attempt:
        attempt = self._attempt
        if attempt is None or attempt.id != attempt_id or attempt.owner != owner:
            raise AttemptMismatch("unknown attempt")
        if self._state is not state:
            raise AttemptMismatch(f"state is {self._state}")
        return attempt

    async def _accept(self, user_id: int) -> None:
        if self._expected is not None and user_id != self._expected:
            try:
                await self._backend.log_out()
            except Exception:
                log.exception("log_out of unexpected user failed")
            self._user_id = None
            self._set(TgState.ERROR, error="unexpected_user")
            return
        try:
            await self._backend.go_online()
        except Exception:
            log.exception("go_online failed")
            self._user_id = None
            self._set(TgState.ERROR, error="online_failed")
            return
        self._user_id = user_id
        self._set(TgState.ONLINE)
        if self._expected is None:
            await self._bind(user_id)
        for cb in self._callbacks:
            try:
                await cb()
            except Exception:
                log.exception("online callback failed")

    async def _bind(self, user_id: int) -> None:
        # В памяти — сразу: не сохранилась привязка — до перезапуска всё равно пускается только он.
        self._expected = user_id
        if self._on_bind is None:
            return
        try:
            await self._on_bind(user_id)
        except Exception:
            log.exception("telegram account binding not persisted")

    def _set(self, state: TgState, *, error: str | None = None) -> None:
        self._state = state
        self._error = error

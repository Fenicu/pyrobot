from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Coroutine
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.api.confirm import ConfirmTokens
from app.api.security import LoginRateLimiter, WindowLimiter
from app.api.sse_slots import SseSlots
from app.config import AppConfig
from app.db.accounts import AccountRepo
from app.db.auth_repo import AuthRepo
from app.db.base import Database
from app.db.users import UserRepo
from app.engine.clock import Clock, SystemClock

if TYPE_CHECKING:
    from app.api.scope import EngineRegistry
    from app.db.audit import AuditLog
    from app.db.invites import InviteRepo
    from app.db.notifications import ServerNotifier
    from app.db.recovery import RecoveryCodes, RecoveryRequests
    from app.db.server_settings import ServerSettingsRepo

log = logging.getLogger(__name__)


@dataclass
class Container:
    config: AppConfig
    auth: AuthRepo
    limiter: LoginRateLimiter
    db: Database
    accounts: AccountRepo
    engines: EngineRegistry
    users: UserRepo
    server_settings: ServerSettingsRepo
    invites: InviteRepo
    recovery_codes: RecoveryCodes
    audit: AuditLog | None = None
    server_notifier: ServerNotifier | None = None
    confirm: ConfirmTokens = field(default_factory=ConfirmTokens)
    sse_slots: SseSlots = field(default_factory=SseSlots)
    # Сколько запрос ручной команды ждёт итога шлюза, прежде чем ответить 202 pending.
    command_wait_s: float = 30.0
    # Пинг SSE; на каждом пинге сессия перепроверяется (отозванная закрывает поток).
    sse_heartbeat_s: float = 15.0
    # Часы выборок по суткам («Итоги дня»).
    clock: Clock = field(default_factory=SystemClock)
    # Правка настроек при занятой аренде ждёт регистрации движка аккаунта не дольше этого.
    engine_wait_s: float = 5.0
    recovery_key: bytes | None = None
    recovery_requests: RecoveryRequests = field(default=None)  # type: ignore[assignment]
    recover_limiter: WindowLimiter = field(default_factory=lambda: WindowLimiter(3, 3600.0))
    recovery_code_limiter: WindowLimiter = field(default_factory=lambda: WindowLimiter(10, 3600.0))
    _tasks: set[asyncio.Task[Any]] = field(default_factory=set, init=False)

    def __post_init__(self) -> None:
        if self.recovery_key is None:
            self.recovery_key = os.urandom(32)
        if self.recovery_requests is None:
            from app.db.recovery import RecoveryRequests

            self.recovery_requests = RecoveryRequests(self.db, self.recovery_key)

    def spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        async def _runner() -> None:
            try:
                await coro
            except Exception:
                log.exception("background task failed")

        task = asyncio.create_task(_runner())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

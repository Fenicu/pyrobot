from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.api.confirm import ConfirmTokens
from app.api.security import LoginRateLimiter
from app.config import AppConfig
from app.db.auth_repo import AuthRepo
from app.db.reads import DbReads
from app.engine.clock import Clock, SystemClock

if TYPE_CHECKING:
    from app.engine.facade import EngineFacade


@dataclass
class Container:
    config: AppConfig
    auth: AuthRepo
    limiter: LoginRateLimiter
    reads: DbReads
    facade: EngineFacade | None = None
    confirm: ConfirmTokens = field(default_factory=ConfirmTokens)
    # Сколько запрос ручной команды ждёт итога шлюза, прежде чем ответить 202 pending.
    command_wait_s: float = 30.0
    # Пинг SSE; на каждом пинге сессия перепроверяется (отозванная закрывает поток).
    sse_heartbeat_s: float = 15.0
    # Часы выборок по суткам («Итоги дня»).
    clock: Clock = field(default_factory=SystemClock)

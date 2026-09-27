from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.api.security import LoginRateLimiter
from app.config import AppConfig
from app.db.auth_repo import AuthRepo
from app.db.reads import DbReads

if TYPE_CHECKING:
    from app.engine.facade import EngineFacade


@dataclass
class Container:
    config: AppConfig
    auth: AuthRepo
    limiter: LoginRateLimiter
    reads: DbReads
    facade: EngineFacade | None = None

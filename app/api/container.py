from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.api.security import LoginRateLimiter
from app.config import AppConfig
from app.db.auth_repo import AuthRepo

if TYPE_CHECKING:
    from app.engine.facade import EngineFacade


@dataclass
class Container:
    config: AppConfig
    auth: AuthRepo
    limiter: LoginRateLimiter
    facade: EngineFacade | None = None

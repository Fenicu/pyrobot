from typing import Any

from app.api.app import create_api
from app.api.container import Container
from app.api.security import LoginRateLimiter
from app.config import AppConfig
from app.db.accounts import AccountRepo
from app.db.audit import AuditLog
from app.db.auth_repo import AuthRepo
from app.db.base import Database
from app.db.invites import InviteRepo
from app.db.notifications import ServerNotifier
from app.db.recovery import RecoveryCodes
from app.db.server_settings import ServerSettingsRepo
from app.db.users import UserRepo
from app.engine.host.account import AccountRuntime
from app.engine.host.host import HostStatus


class _NoEngines:
    def get(self, account_id: int) -> AccountRuntime | None:
        return None

    async def wait_registered(self, account_id: int, timeout_s: float) -> AccountRuntime | None:
        return None

    def host_reason(self, account_id: int) -> str | None:
        return None

    def status(self) -> HostStatus:
        return HostStatus("", False, [], {}, 0.0, False)

    def poke(self) -> None:
        pass


def build_schema() -> dict[str, Any]:
    """OpenAPI без запуска сервиса и БД (движок ленивый, соединений не открывает): в самом
    сервисе `/openapi.json` выключен, схема нужна для TS-типов админки."""
    db = Database(AppConfig.model_fields["database_url"].default)
    audit = AuditLog(db)
    container = Container(
        config=AppConfig(_env_file=None, transport="fake"),  # type: ignore[call-arg]
        auth=AuthRepo(db),
        limiter=LoginRateLimiter(),
        db=db,
        accounts=AccountRepo(db),
        engines=_NoEngines(),
        users=UserRepo(db),
        server_settings=ServerSettingsRepo(db, audit),
        invites=InviteRepo(db, audit),
        recovery_codes=RecoveryCodes(db),
        audit=audit,
        server_notifier=ServerNotifier(db),
    )
    return create_api(container).openapi()

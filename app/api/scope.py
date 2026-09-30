"""Аккаунт в пути (раздел 4.4 спеки): все пути аккаунта — под `/api/v1/accounts/{account_id}`,
доступ проверяет одна зависимость `account_scope`: чужой и несуществующий аккаунт — одинаково 404.
Движок аккаунта может быть не запущен: тогда чтения идут из базы, а живые пути отвечают 503."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.container import Container
from app.api.deps import SessionContext, container, current_session
from app.api.errors import ACCOUNT_NOT_FOUND, ENGINE_NOT_RUNNING, error
from app.db.accounts import AccountInfo
from app.db.reads import DbReads
from app.engine.facade import EngineFacade
from app.engine.host.account import AccountRuntime
from app.logctx import current_account

ACCOUNT_PREFIX = "/api/v1/accounts/{account_id}"


class EngineRegistry(Protocol):
    """Движки аккаунтов, запущенные в этом процессе."""

    def get(self, account_id: int) -> AccountRuntime | None: ...

    async def wait_registered(self, account_id: int, timeout_s: float) -> AccountRuntime | None:
        """Движок аккаунта, как только он зарегистрирован; None — не дождались за `timeout_s`."""
        ...

    def host_reason(self, account_id: int) -> str | None:
        """Почему движок аккаунта не запущен здесь: `locked_elsewhere`, `lease_active`; None —
        запущен или причины нет."""
        ...


@dataclass(frozen=True)
class AccountScope:
    account: AccountInfo
    reads: DbReads
    # Движок, зарегистрированный на момент запроса; None — не запущен.
    engine: AccountRuntime | None

    @property
    def facade(self) -> EngineFacade | None:
        return self.engine.facade if self.engine is not None else None


async def account_scope(
    account_id: int,
    ctx: Annotated[SessionContext, Depends(current_session)],
    c: Annotated[Container, Depends(container)],
) -> AsyncIterator[AccountScope]:
    """Аккаунт текущей учётки. Запрос идёт в контексте аккаунта: строки лога и задачи, созданные
    внутри (клиент kurigram при входе в Telegram), получают `account=<id>`."""
    account = await c.accounts.get(account_id)
    if account is None or account.owner_id != ctx.admin_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, ACCOUNT_NOT_FOUND)
    token = current_account.set(account.id)
    try:
        yield AccountScope(account, DbReads(c.db, account.id), c.engines.get(account.id))
    finally:
        current_account.reset(token)


async def running(scope: Annotated[AccountScope, Depends(account_scope)]) -> EngineFacade:
    """Фасад запущенного движка аккаунта; не запущен — 503."""
    if scope.facade is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, ENGINE_NOT_RUNNING)
    return scope.facade


def account_router(tag: str) -> APIRouter:
    """Роутер путей аккаунта: доступ проверяется у каждого маршрута, даже не берущего scope."""
    return APIRouter(
        prefix=ACCOUNT_PREFIX,
        tags=[tag],
        dependencies=[Depends(account_scope)],
        responses={404: error(ACCOUNT_NOT_FOUND)},
    )

"""Обзор аккаунтов учётки: массовые действия."""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.container import Container
from app.api.deps import SessionContext, container, require_csrf
from app.api.errors import CSRF
from app.db.accounts import AccountInfo
from app.engine.facade import LockLostError
from app.engine.fence import LeaseLost

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/overview", tags=["overview"])

MAX_BULK_IDS = 50


class EngineBulkIn(BaseModel):
    action: Literal["pause", "resume"]
    # null - все аккаунты учётки в статусе enabled
    account_ids: list[int] | None = Field(default=None, max_length=MAX_BULK_IDS)


class EngineBulkResultOut(BaseModel):
    account_id: int
    status: Literal["done", "unchanged", "skipped", "error"]
    reason: str | None = None
    # Почему движок не запущен здесь (только для skipped)
    host: str | None = None


class EngineBulkOut(BaseModel):
    results: list[EngineBulkResultOut]


def _unique(ids: list[int]) -> list[int]:
    return list(dict.fromkeys(ids))


async def _toggle(
    c: Container, info: AccountInfo | None, account_id: int, paused: bool, by: str
) -> EngineBulkResultOut:
    def row(
        status: Literal["done", "unchanged", "skipped", "error"],
        reason: str | None = None,
        host: str | None = None,
    ) -> EngineBulkResultOut:
        return EngineBulkResultOut(account_id=account_id, status=status, reason=reason, host=host)

    if info is None:
        return row("error", "account_not_found")
    if info.status == "deleting":
        return row("error", "account_deleting")
    runtime = c.engines.get(account_id)
    facade = runtime.facade if runtime is not None else None
    if facade is None:
        return row("skipped", "engine_not_running", c.engines.host_reason(account_id))
    try:
        changed = await facade.set_paused(paused, by)
    except (LockLostError, LeaseLost):
        return row("error", "lock_lost")
    except Exception:
        log.exception("bulk pause failed for account %s", account_id)
        return row("error", "internal_error")
    if changed:
        return row("done")
    return row("unchanged", "already_paused" if paused else "already_running")


@router.post("/engine", response_model=EngineBulkOut, responses=CSRF)
async def overview_engine(
    body: EngineBulkIn,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    c: Annotated[Container, Depends(container)],
) -> EngineBulkOut:
    """Пауза или возобновление нескольких аккаунтов. Остановленный здесь движок пропускается
    (`skipped`): флаг в базу не пишется, у чужого хоста запись затёрлась бы его настройками.
    Чужой и несуществующий аккаунт неразличимы."""
    owned = {a.id: a for a in await c.accounts.owned(ctx.user_id)}
    if body.account_ids is None:
        ids = [i for i, a in owned.items() if a.status == "enabled"]
    else:
        ids = _unique(body.account_ids)
    paused = body.action == "pause"
    results = [await _toggle(c, owned.get(i), i, paused, ctx.login) for i in ids]
    return EngineBulkOut(results=results)

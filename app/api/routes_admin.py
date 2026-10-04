from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.api.container import Container
from app.api.deps import (
    SessionContext,
    container,
    require_csrf,
    require_owner,
)
from app.api.errors import (
    AUTH,
    CONFIRM_LOGIN_MISMATCH,
    CSRF,
    LAST_OWNER,
    USER_NOT_FOUND,
    error,
)
from app.db.audit import Actor
from app.db.users import LastOwner, Role

router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin"],
    dependencies=[Depends(require_owner)],
    responses={404: error("not found")},
)


class AdminUserOut(BaseModel):
    id: int
    login: str
    role: Role
    created_at: datetime
    last_login_at: datetime | None
    accounts: int
    max_accounts: int
    disabled: bool
    disabled_reason: str | None
    deleting: bool


class AdminUserPatchIn(BaseModel):
    max_accounts: int | None = Field(default=None, ge=1, le=1000)
    disabled: bool | None = None
    reason: str | None = Field(default=None, max_length=256)


class AdminUserDeleteIn(BaseModel):
    confirm_login: str


@router.get("/users", response_model=list[AdminUserOut], responses=AUTH)
async def list_users(
    c: Annotated[Container, Depends(container)],
) -> list[AdminUserOut]:
    """Список пользователей со служебными полями для консоли владельца."""
    rows = await c.admin_reads.users()
    return [AdminUserOut.model_validate(asdict(r)) for r in rows]


@router.patch(
    "/users/{user_id}",
    response_model=AdminUserOut,
    responses={
        **CSRF,
        404: error(USER_NOT_FOUND),
        409: error(LAST_OWNER),
    },
)
async def patch_user(
    user_id: int,
    body: AdminUserPatchIn,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    c: Annotated[Container, Depends(container)],
) -> AdminUserOut:
    """Изменение лимита аккаунтов, отключение или включение пользователя."""
    user = await c.users.get(user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, USER_NOT_FOUND)

    if body.max_accounts is not None:
        old_limit = user.max_accounts
        if body.max_accounts != old_limit:
            await c.users.set_limit(user_id, body.max_accounts)
            if c.audit is not None:
                await c.audit.write(
                    Actor.of(ctx),
                    "user_limit_changed",
                    target_type="user",
                    target_id=user_id,
                    details={"from": old_limit, "to": body.max_accounts},
                )

    if body.disabled is not None:
        if body.disabled:
            try:
                await c.users.disable(user_id, body.reason)
            except LastOwner as exc:
                raise HTTPException(status.HTTP_409_CONFLICT, LAST_OWNER) from exc
            if c.audit is not None:
                details = {"reason": body.reason} if body.reason is not None else {}
                await c.audit.write(
                    Actor.of(ctx),
                    "user_disabled",
                    target_type="user",
                    target_id=user_id,
                    details=details,
                )
            c.engines.poke()
        else:
            await c.users.enable(user_id)
            if c.audit is not None:
                await c.audit.write(
                    Actor.of(ctx),
                    "user_enabled",
                    target_type="user",
                    target_id=user_id,
                    details={},
                )

    row = await c.admin_reads.user(user_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, USER_NOT_FOUND)
    return AdminUserOut.model_validate(asdict(row))


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_202_ACCEPTED,
    response_class=Response,
    responses={
        **CSRF,
        404: error(USER_NOT_FOUND),
        409: error(LAST_OWNER),
        422: error(CONFIRM_LOGIN_MISMATCH),
    },
)
async def delete_user(
    user_id: int,
    body: AdminUserDeleteIn,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    c: Annotated[Container, Depends(container)],
) -> Response:
    """Удаление пользователя и перевод его аккаунтов на чистку."""
    user = await c.users.get(user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, USER_NOT_FOUND)
    if body.confirm_login != user.login:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, CONFIRM_LOGIN_MISMATCH)

    try:
        await c.users.mark_deleting(user_id)
    except LastOwner as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, LAST_OWNER) from exc

    if c.audit is not None:
        await c.audit.write(
            Actor.of(ctx),
            "user_deleted",
            target_type="user",
            target_id=user_id,
            details={},
        )
    c.engines.poke()
    return Response(status_code=status.HTTP_202_ACCEPTED)

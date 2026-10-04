from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, ValidationError

from app.api.container import Container
from app.api.deps import (
    SessionContext,
    container,
    require_csrf,
    require_owner,
)
from app.api.errors import (
    ACCOUNT_DELETING,
    ACCOUNT_NOT_FOUND,
    AUTH,
    CONFIRM_LOGIN_MISMATCH,
    CONFIRM_NAME_MISMATCH,
    CSRF,
    ENGINE_NOT_RUNNING,
    INVITE_GONE,
    INVITE_NOT_FOUND,
    LAST_OWNER,
    REASON_REQUIRED,
    USER_NOT_FOUND,
    ValidationErrorOut,
    VersionConflictOut,
    error,
)
from app.db.admin_reads import AdminAccountRow
from app.db.audit import Actor
from app.db.invites import InviteGone, InviteInfo, InviteNotFound
from app.db.users import LastOwner, Role
from app.engine.host.host import EngineStats
from app.engine.server_settings import ServerSettings
from app.engine.settings import SettingsConflict, SettingsPatchError

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


class AdminAccountOut(BaseModel):
    id: int
    name: str
    owner_id: int | None
    owner_login: str | None
    status: str
    status_reason: str | None
    blocked: bool
    blocked_reason: str | None
    running: bool
    tg_online: bool
    restarts_24h: int
    last_error_code: str | None
    last_error_at: datetime | None
    messages_1h: int
    actions_1h: int
    rows: dict[str, int]


class AdminAccountPatchIn(BaseModel):
    blocked: bool
    reason: str | None = Field(default=None, max_length=256)


class AdminAccountDeleteIn(BaseModel):
    confirm_name: str


def _admin_account_out(r: AdminAccountRow, st: EngineStats) -> AdminAccountOut:
    return AdminAccountOut(
        id=r.id,
        name=r.name,
        owner_id=r.owner_id,
        owner_login=r.owner_login,
        status=r.status,
        status_reason=r.status_reason,
        blocked=r.blocked,
        blocked_reason=r.blocked_reason,
        running=st.running,
        tg_online=st.tg_online,
        restarts_24h=st.restarts_24h,
        last_error_code=st.last_error_code,
        last_error_at=st.last_error_at,
        messages_1h=r.messages_1h,
        actions_1h=r.actions_1h,
        rows=r.rows,
    )


@router.get("/accounts", response_model=list[AdminAccountOut], responses=AUTH)
async def list_accounts(
    c: Annotated[Container, Depends(container)],
) -> list[AdminAccountOut]:
    """Список аккаунтов со служебными полями и нагрузкой для консоли владельца."""
    now = datetime.now(UTC)
    rows = await c.admin_reads.accounts(now)
    return [_admin_account_out(r, c.engines.stats(r.id)) for r in rows]


@router.patch(
    "/accounts/{account_id}",
    response_model=AdminAccountOut,
    responses={
        **CSRF,
        404: error(ACCOUNT_NOT_FOUND),
        422: error(REASON_REQUIRED),
    },
)
async def patch_account(
    account_id: int,
    body: AdminAccountPatchIn,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    c: Annotated[Container, Depends(container)],
) -> AdminAccountOut:
    """Блокировка с обязательной причиной или разблокировка аккаунта."""
    if body.blocked and (not body.reason or not body.reason.strip()):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, REASON_REQUIRED)

    acc = await c.accounts.get(account_id)
    if acc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, ACCOUNT_NOT_FOUND)

    if body.blocked:
        assert body.reason is not None
        try:
            await c.accounts.block(account_id, body.reason.strip())
        except KeyError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, ACCOUNT_NOT_FOUND) from exc
        if c.audit is not None:
            await c.audit.write(
                Actor.of(ctx),
                "account_blocked",
                target_type="account",
                target_id=account_id,
                details={"reason": body.reason.strip()},
            )
    else:
        try:
            await c.accounts.unblock(account_id)
        except KeyError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, ACCOUNT_NOT_FOUND) from exc
        if c.audit is not None:
            await c.audit.write(
                Actor.of(ctx),
                "account_unblocked",
                target_type="account",
                target_id=account_id,
                details={},
            )
    c.engines.poke()

    now = datetime.now(UTC)
    row = await c.admin_reads.account(account_id, now)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, ACCOUNT_NOT_FOUND)
    return _admin_account_out(row, c.engines.stats(account_id))


@router.post(
    "/accounts/{account_id}/restart",
    status_code=status.HTTP_202_ACCEPTED,
    response_class=Response,
    responses={
        **CSRF,
        404: error(ACCOUNT_NOT_FOUND),
        409: error(ACCOUNT_DELETING),
        503: error(ENGINE_NOT_RUNNING),
    },
)
async def restart_account(
    account_id: int,
    _: Annotated[SessionContext, Depends(require_csrf)],
    c: Annotated[Container, Depends(container)],
) -> Response:
    """Перезапуск движка аккаунта хостом."""
    acc = await c.accounts.get(account_id)
    if acc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, ACCOUNT_NOT_FOUND)
    if acc.status == "deleting":
        raise HTTPException(status.HTTP_409_CONFLICT, ACCOUNT_DELETING)

    st = c.engines.stats(account_id)
    if not st.running:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, ENGINE_NOT_RUNNING)

    await c.accounts.restart(account_id)
    c.engines.poke()
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.delete(
    "/accounts/{account_id}",
    status_code=status.HTTP_202_ACCEPTED,
    response_class=Response,
    responses={
        **CSRF,
        404: error(ACCOUNT_NOT_FOUND),
        409: error(ACCOUNT_DELETING),
        422: error(CONFIRM_NAME_MISMATCH),
    },
)
async def delete_account(
    account_id: int,
    body: AdminAccountDeleteIn,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    c: Annotated[Container, Depends(container)],
) -> Response:
    """Удаление аккаунта владельцем сервера с подтверждением имени."""
    acc = await c.accounts.get(account_id)
    if acc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, ACCOUNT_NOT_FOUND)
    if body.confirm_name != acc.name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, CONFIRM_NAME_MISMATCH)
    if acc.status == "deleting":
        raise HTTPException(status.HTTP_409_CONFLICT, ACCOUNT_DELETING)

    await c.accounts.mark_deleting(account_id)
    if c.audit is not None:
        await c.audit.write(
            Actor.of(ctx),
            "account_deleted",
            target_type="account",
            target_id=account_id,
            details={},
        )
    c.engines.poke()
    return Response(status_code=status.HTTP_202_ACCEPTED)


class AdminInviteOut(BaseModel):
    id: int
    created_at: datetime
    expires_at: datetime
    max_accounts: int
    note: str | None
    expired: bool


class AdminInviteCreateIn(BaseModel):
    max_accounts: int | None = Field(default=None, ge=1, le=1000)
    ttl_h: int | None = Field(default=None, ge=1, le=720)
    note: str | None = Field(default=None, max_length=128)


class AdminInviteCreatedOut(BaseModel):
    invite: AdminInviteOut
    token: str
    path: str


def _invite_out(info: InviteInfo, now: datetime) -> AdminInviteOut:
    return AdminInviteOut(
        id=info.id,
        created_at=info.created_at,
        expires_at=info.expires_at,
        max_accounts=info.max_accounts,
        note=info.note,
        expired=info.expires_at <= now,
    )


@router.get("/invites", response_model=list[AdminInviteOut], responses=AUTH)
async def list_invites(c: Annotated[Container, Depends(container)]) -> list[AdminInviteOut]:
    """Неиспользованные и неотозванные приглашения без токенов."""
    now = datetime.now(UTC)
    return [_invite_out(info, now) for info in await c.invites.unused()]


@router.post(
    "/invites",
    status_code=status.HTTP_201_CREATED,
    response_model=AdminInviteCreatedOut,
    responses=CSRF,
)
async def create_invite(
    body: AdminInviteCreateIn,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    c: Annotated[Container, Depends(container)],
) -> AdminInviteCreatedOut:
    """Создание приглашения; токен выдаётся только в этом ответе."""
    settings, _ = await c.server_settings.load()
    defaults = settings.invites
    token, info = await c.invites.create(
        Actor.of(ctx),
        ttl_h=body.ttl_h if body.ttl_h is not None else defaults.default_ttl_h,
        max_accounts=(
            body.max_accounts if body.max_accounts is not None else defaults.default_max_accounts
        ),
        note=body.note,
    )
    return AdminInviteCreatedOut(
        invite=_invite_out(info, datetime.now(UTC)), token=token, path=f"/invite/{token}"
    )


@router.delete(
    "/invites/{invite_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={**CSRF, 404: error(INVITE_NOT_FOUND), 410: error(INVITE_GONE)},
)
async def revoke_invite(
    invite_id: int,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    c: Annotated[Container, Depends(container)],
) -> Response:
    try:
        await c.invites.revoke(invite_id, Actor.of(ctx))
    except InviteNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, INVITE_NOT_FOUND) from exc
    except InviteGone as exc:
        raise HTTPException(status.HTTP_410_GONE, INVITE_GONE) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class AdminServerSettingsOut(BaseModel):
    version: int
    values: dict[str, Any]
    defaults: dict[str, Any]
    json_schema: dict[str, Any] = Field(serialization_alias="schema")


class AdminServerSettingsPatchIn(BaseModel):
    version: int = Field(ge=0)
    changes: dict[str, Any]


class AdminServerSettingsPatchOut(BaseModel):
    version: int
    values: dict[str, Any]
    changed: dict[str, list[Any]]


@router.get("/server-settings", response_model=AdminServerSettingsOut, responses=AUTH)
async def get_server_settings(
    c: Annotated[Container, Depends(container)],
) -> AdminServerSettingsOut:
    settings, version = await c.server_settings.load()
    return AdminServerSettingsOut(
        version=version,
        values=settings.model_dump(mode="json"),
        defaults=ServerSettings().model_dump(mode="json"),
        json_schema=ServerSettings.model_json_schema(),
    )


@router.patch(
    "/server-settings",
    response_model=AdminServerSettingsPatchOut,
    responses={**CSRF, 409: {"model": VersionConflictOut}, 422: {"model": ValidationErrorOut}},
)
async def patch_server_settings(
    body: AdminServerSettingsPatchIn,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    c: Annotated[Container, Depends(container)],
) -> AdminServerSettingsPatchOut:
    await c.server_settings.load()
    try:
        settings, version, changed = await c.server_settings.update(
            body.changes, version=body.version, actor=Actor.of(ctx)
        )
    except SettingsConflict as exc:
        _, current_version = await c.server_settings.load()
        raise HTTPException(
            status.HTTP_409_CONFLICT, {"code": "version_conflict", "version": current_version}
        ) from exc
    except SettingsPatchError as exc:
        issue = {
            "loc": ["body", "changes", *exc.path.split(".")],
            "msg": exc.code,
            "type": exc.code,
        }
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, [issue]) from exc
    except ValidationError as exc:
        issues = [
            {"loc": ["body", "changes", *e["loc"]], "msg": e["msg"], "type": e["type"]}
            for e in exc.errors(include_url=False)
        ]
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, issues) from exc
    return AdminServerSettingsPatchOut(
        version=version, values=settings.model_dump(mode="json"), changed=changed
    )


class AdminAuditOut(BaseModel):
    id: int
    at: datetime
    actor_user_id: int | None
    actor_login: str
    action: str
    target_type: str | None
    target_id: int | None
    details: dict[str, Any]


class AdminAuditPageOut(BaseModel):
    items: list[AdminAuditOut]
    next_before: int | None


@router.get("/audit", response_model=AdminAuditPageOut, responses=AUTH)
async def list_audit(
    c: Annotated[Container, Depends(container)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    before: Annotated[int | None, Query(ge=1)] = None,
) -> AdminAuditPageOut:
    assert c.audit is not None
    items, next_before = await c.audit.page(limit=limit, before=before)
    return AdminAuditPageOut(
        items=[AdminAuditOut.model_validate(asdict(item)) for item in items],
        next_before=next_before,
    )


class AdminNotificationOut(BaseModel):
    id: int
    created_at: datetime
    level: str
    code: str
    text: str
    read: bool


class AdminNotificationReadIn(BaseModel):
    up_to_id: int = Field(ge=1)


@router.get("/notifications", response_model=list[AdminNotificationOut], responses=AUTH)
async def list_server_notifications(
    c: Annotated[Container, Depends(container)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[AdminNotificationOut]:
    assert c.server_notifier is not None
    rows = await c.server_notifier.recent(limit=limit)
    return [
        AdminNotificationOut(
            id=row.id,
            created_at=row.created_at,
            level=row.level,
            code=row.code,
            text=row.text,
            read=row.read,
        )
        for row in rows
    ]


@router.post(
    "/notifications/read",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=CSRF,
)
async def read_server_notifications(
    body: AdminNotificationReadIn,
    _: Annotated[SessionContext, Depends(require_csrf)],
    c: Annotated[Container, Depends(container)],
) -> Response:
    assert c.server_notifier is not None
    await c.server_notifier.mark_read(body.up_to_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

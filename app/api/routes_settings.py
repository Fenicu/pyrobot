from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, ValidationError

from app.api.container import Container
from app.api.deps import SessionContext, container, require_csrf
from app.api.errors import (
    ACCOUNT_DELETING,
    AUTH,
    CSRF,
    ENGINE_STARTING,
    ChatIsSelfOut,
    ErrorOut,
    ValidationErrorOut,
    VersionConflictOut,
    error,
)
from app.api.scope import AccountScope, account_router, account_scope
from app.db.accounts import AccountDeleting
from app.db.notifications import DbNotifier
from app.db.settings_store import LeaseHeld, direct_update
from app.engine.facade import EngineFacade, SettingsUpdate
from app.engine.settings import (
    Settings,
    SettingsConflict,
    SettingsPatch,
    SettingsPatchError,
    restart_required,
    settings_diff,
)

router = account_router("settings")
# Через столько клиенту стоит повторить правку, пока движок аккаунта регистрируется.
ENGINE_STARTING_RETRY_S = 2


class SettingsOut(BaseModel):
    version: int
    values: dict[str, Any]
    defaults: dict[str, Any]
    json_schema: dict[str, Any] = Field(serialization_alias="schema")


class SettingsPatchIn(BaseModel):
    version: int = Field(ge=0)
    changes: dict[str, Any]
    confirm_live: bool = False


class SettingsPatchOut(BaseModel):
    version: int
    values: dict[str, Any]
    changed: dict[str, list[Any]]
    restart_required: list[str]


class SettingsVersionOut(BaseModel):
    version: int
    changed_by: str
    changed_at: datetime
    changes: dict[str, list[Any]]


class SettingsHistoryOut(BaseModel):
    items: list[SettingsVersionOut]
    next_before: int | None


def _unprocessable(errors: list[dict[str, Any]]) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, errors)


@router.get("/settings", response_model=SettingsOut, responses=AUTH)
async def get_settings(scope: Annotated[AccountScope, Depends(account_scope)]) -> SettingsOut:
    """Настройки движка; без движка — из базы."""
    f = scope.facade
    if f is not None:
        current, version = f.settings.current, f.settings.version
    else:
        current, version = await scope.reads.settings()
    return SettingsOut(
        version=version,
        values=current.model_dump(mode="json"),
        defaults=Settings().model_dump(mode="json"),
        json_schema=Settings.model_json_schema(),
    )


async def _checked(
    update: Awaitable[SettingsUpdate], version: Callable[[], Awaitable[int]]
) -> SettingsPatchOut:
    try:
        upd = await update
    except SettingsConflict as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, {"code": "version_conflict", "version": await version()}
        ) from exc
    except SettingsPatchError as exc:
        loc = ["body", "confirm_live"] if exc.code == "live_requires_confirm" else None
        loc = loc or ["body", "changes", *exc.path.split(".")]
        raise _unprocessable([{"loc": loc, "msg": exc.code, "type": exc.code}]) from exc
    except ValidationError as exc:
        raise _unprocessable(
            [
                {"loc": ["body", "changes", *e["loc"]], "msg": e["msg"], "type": e["type"]}
                for e in exc.errors(include_url=False)
            ]
        ) from exc
    return SettingsPatchOut(
        version=upd.version,
        values=upd.settings.model_dump(mode="json"),
        changed=upd.changed,
        restart_required=restart_required(upd.changed),
    )


async def _via_engine(f: EngineFacade, body: SettingsPatchIn, by: str) -> SettingsPatchOut:
    async def version() -> int:
        return f.settings.version

    return await _checked(
        f.patch_settings(
            body.changes, version=body.version, by=by, confirm_live=body.confirm_live
        ),
        version,
    )


async def _direct(
    c: Container, scope: AccountScope, body: SettingsPatchIn, by: str
) -> SettingsPatchOut:
    """Правка без движка — прямо в базу; применится при следующем старте движка. Аренда
    занята — `LeaseHeld`: правку делает её держатель."""
    account_id = scope.account.id

    async def update() -> SettingsUpdate:
        patch = SettingsPatch(body.changes, confirm_live=body.confirm_live)
        new, saved = await direct_update(
            c.db, account_id, patch, changed_by=by, expected_version=body.version
        )
        old = patch.before
        assert old is not None
        if old.engine.mode != new.engine.mode:
            # Как у правки через движок: смена режима остаётся в уведомлениях.
            await DbNotifier(c.db, account_id).notify(
                "info", "engine_mode", f"mode {old.engine.mode} -> {new.engine.mode} by {by}"
            )
        changed = settings_diff(old.model_dump(mode="json"), new.model_dump(mode="json"))
        return SettingsUpdate(new, saved, changed)

    async def version() -> int:
        return (await scope.reads.settings())[1]

    return await _checked(update(), version)


@router.patch(
    "/settings",
    response_model=SettingsPatchOut,
    responses={
        **CSRF,
        409: {
            "model": VersionConflictOut | ErrorOut,
            "description": f"settings changed since `version` | {ACCOUNT_DELETING}",
        },
        422: {
            "model": ValidationErrorOut | ChatIsSelfOut,
            "description": "invalid body or changes | chat_is_self: `chats.*` fields equal to "
            "the account's Telegram user",
        },
        503: error(ENGINE_STARTING),
    },
)
async def patch_settings(
    body: SettingsPatchIn,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    scope: Annotated[AccountScope, Depends(account_scope)],
    c: Annotated[Container, Depends(container)],
) -> SettingsPatchOut:
    """Удаляемый аккаунт не правится — и тогда, когда его движок ещё зарегистрирован. С
    движком — через него. Без движка — прямая запись в базу, если аренда аккаунта свободна;
    занята — правку делает движок, который её взял: он ещё не зарегистрирован — ожидание до
    `engine_wait_s`, затем 503 `engine_starting`. У привязанного аккаунта поле `chats.*`,
    равное его пользователю Telegram, — 422 `chat_is_self` с этими полями."""
    if scope.account.status == "deleting":
        raise HTTPException(status.HTTP_409_CONFLICT, ACCOUNT_DELETING)
    f = scope.facade
    if f is None:
        try:
            return await _direct(c, scope, body, ctx.login)
        except AccountDeleting as exc:
            # Пометка удаления пришла после чтения аккаунта.
            raise HTTPException(status.HTTP_409_CONFLICT, ACCOUNT_DELETING) from exc
        except LeaseHeld:
            engine = await c.engines.wait_registered(scope.account.id, c.engine_wait_s)
            f = engine.facade if engine is not None else None
        if f is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                ENGINE_STARTING,
                headers={"Retry-After": str(ENGINE_STARTING_RETRY_S)},
            )
    return await _via_engine(f, body, ctx.login)


@router.get("/settings/history", response_model=SettingsHistoryOut, responses=AUTH)
async def settings_history(
    scope: Annotated[AccountScope, Depends(account_scope)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    before: Annotated[int | None, Query(ge=1)] = None,
) -> SettingsHistoryOut:
    page, next_before = await scope.reads.settings_history(limit, before)
    return SettingsHistoryOut(
        items=[
            SettingsVersionOut(
                version=r.version,
                changed_by=r.changed_by,
                changed_at=r.changed_at,
                changes=r.changes,
            )
            for r in page
        ],
        next_before=next_before,
    )

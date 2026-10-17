import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any, cast

from fastapi import Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, ValidationError

from app.api.container import Container
from app.api.deps import SessionContext, container, require_csrf
from app.api.errors import (
    ACCOUNT_DELETING,
    AUTH,
    CSRF,
    ENGINE_NOT_RUNNING,
    ENGINE_STARTING,
    ChatIsSelfOut,
    ErrorOut,
    OutOfBoundsOut,
    ValidationErrorOut,
    VersionConflictOut,
    error,
)
from app.api.scope import AccountScope, account_router, account_scope
from app.db.accounts import AccountDeleting
from app.db.notifications import DbNotifier
from app.db.settings_store import LeaseHeld, direct_update
from app.engine.facade import SettingsUpdate
from app.engine.settings import (
    Settings,
    SettingsConflict,
    SettingsLeafError,
    SettingsPatch,
    SettingsPatchError,
    restart_required,
    settings_diff,
)

log = logging.getLogger(__name__)
router = account_router("settings")
# Через столько клиенту стоит повторить правку, пока движок аккаунта регистрируется.
ENGINE_STARTING_RETRY_S = 2


class BoundOut(BaseModel):
    min: float | None = None
    max: float | None = None


class SettingsOut(BaseModel):
    version: int
    values: dict[str, Any]
    defaults: dict[str, Any]
    json_schema: dict[str, Any] = Field(serialization_alias="schema")
    bounds: dict[str, BoundOut]


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
async def get_settings(
    scope: Annotated[AccountScope, Depends(account_scope)],
    c: Annotated[Container, Depends(container)],
) -> SettingsOut:
    """Настройки движка; без движка — из базы, и те, что текущая сборка не принимает: неверное
    значение видно в `values` и исправляется правкой."""
    f = scope.facade
    if f is not None:
        values, version = f.settings.current.model_dump(mode="json"), f.settings.version
    else:
        values, version = await scope.reads.settings()
    engine_bounds = c.server_settings.current.engine_bounds
    bounds_out = {b.path: BoundOut(min=b.min, max=b.max) for b in engine_bounds.bounds()}
    return SettingsOut(
        version=version,
        values=values,
        defaults=Settings().model_dump(mode="json"),
        json_schema=Settings.model_json_schema(),
        bounds=bounds_out,
    )


async def _current_version(c: Container, scope: AccountScope) -> int:
    """Версия настроек сейчас: у запущенного движка - его, иначе из базы."""
    runtime = c.engines.get(scope.account.id)
    if runtime is not None and runtime.facade is not None:
        return runtime.facade.settings.version
    return (await scope.reads.settings())[1]


class EngineStarting(Exception):
    """Аренда аккаунта занята, а её держатель (движок) ещё не зарегистрирован."""


def patch_http_error(exc: Exception) -> HTTPException | None:
    """Ошибки правки настроек, которые одиночная ручка отдаёт как 422."""
    if isinstance(exc, SettingsPatchError):
        loc = ["body", "confirm_live"] if exc.code == "live_requires_confirm" else None
        loc = loc or ["body", "changes", *exc.path.split(".")]
        return _unprocessable([{"loc": loc, "msg": exc.code, "type": exc.code}])
    if isinstance(exc, SettingsLeafError):
        return _unprocessable(
            [
                {"loc": ["body", "changes", *e["loc"]], "msg": e["msg"], "type": e["type"]}
                for e in exc.errors
            ]
        )
    if isinstance(exc, ValidationError):
        return _unprocessable(
            [
                {"loc": ["body", "changes", *e["loc"]], "msg": e["msg"], "type": e["type"]}
                for e in exc.errors(include_url=False)
            ]
        )
    return None


async def _direct(
    c: Container,
    scope: AccountScope,
    changes: Mapping[str, Any],
    *,
    confirm_live: bool,
    version: int,
    by: str,
    tolerate_audit_failure: bool,
) -> SettingsUpdate:
    """Правка без движка - прямо в базу; применится при следующем старте движка. Аренда
    занята - `LeaseHeld`: правку делает её держатель."""
    account_id = scope.account.id
    patch = SettingsPatch(
        changes,
        confirm_live=confirm_live,
        bounds=c.server_settings.current.engine_bounds,
    )
    new, saved = await direct_update(
        c.db, account_id, patch, changed_by=by, expected_version=version
    )
    # «До» - JSON и тех сохранённых настроек, что сборка не принимает (поверх умолчаний).
    old = patch.before
    assert old is not None
    old_mode = old["engine"]["mode"]
    audit_failed = False
    if old_mode != new.engine.mode:
        # Как у правки через движок: смена режима остаётся в уведомлениях.
        try:
            await DbNotifier(c.db, account_id).notify(
                "info", "engine_mode", f"mode {old_mode} -> {new.engine.mode} by {by}"
            )
        except Exception:
            if not tolerate_audit_failure:
                raise
            log.warning("engine_mode audit failed after settings were saved", exc_info=True)
            audit_failed = True
    changed = settings_diff(old, new.model_dump(mode="json"))
    return SettingsUpdate(new, saved, changed, audit_failed)


async def apply_account_patch(
    c: Container,
    scope: AccountScope,
    changes: Mapping[str, Any],
    *,
    confirm_live: bool,
    version: int,
    by: str,
    tolerate_audit_failure: bool = False,
    wait_engine: bool = True,
) -> SettingsUpdate:
    """Правка настроек аккаунта: с движком - через него, без движка - прямо в базу, если аренда
    свободна; занята - ожидание регистрации движка до `engine_wait_s`, затем `EngineStarting`
    (`wait_engine=False` - без ожидания). `version` - ожидаемая версия настроек. Ошибки правки
    (`SettingsConflict`, `SettingsPatchError`, `ValidationError`, `SettingsOutOfBounds`,
    `ChatIsSelf`, `AccountDeleting`, `LeaseLost`) не переводятся."""
    f = scope.facade
    if f is None:
        try:
            return await _direct(
                c,
                scope,
                changes,
                confirm_live=confirm_live,
                version=version,
                by=by,
                tolerate_audit_failure=tolerate_audit_failure,
            )
        except LeaseHeld:
            engine = (
                await c.engines.wait_registered(scope.account.id, c.engine_wait_s)
                if wait_engine
                else None
            )
            f = engine.facade if engine is not None else None
        if f is None:
            raise EngineStarting
    return await f.patch_settings(
        changes,
        version=version,
        by=by,
        confirm_live=confirm_live,
        tolerate_audit_failure=tolerate_audit_failure,
    )


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
            "model": ValidationErrorOut | ChatIsSelfOut | OutOfBoundsOut,
            "description": "invalid body or changes | chat_is_self: `chats.*` fields equal to "
            "the account's Telegram user | setting_out_of_bounds",
        },
        503: error(ENGINE_STARTING, ENGINE_NOT_RUNNING),
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
    `engine_wait_s`, затем 503 `engine_starting`; аренда потеряна посреди правки через движок —
    503 `engine not running`. У привязанного аккаунта поле `chats.*`, равное его пользователю
    Telegram, — 422 `chat_is_self` с этими полями."""
    if scope.account.status == "deleting":
        raise HTTPException(status.HTTP_409_CONFLICT, ACCOUNT_DELETING)
    try:
        upd = await apply_account_patch(
            c,
            scope,
            body.changes,
            confirm_live=body.confirm_live,
            version=body.version,
            by=ctx.login,
        )
    except AccountDeleting as exc:
        # Пометка удаления пришла после чтения аккаунта.
        raise HTTPException(status.HTTP_409_CONFLICT, ACCOUNT_DELETING) from exc
    except EngineStarting as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            ENGINE_STARTING,
            headers={"Retry-After": str(ENGINE_STARTING_RETRY_S)},
        ) from exc
    except SettingsConflict as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "version_conflict", "version": await _current_version(c, scope)},
        ) from exc
    except (SettingsPatchError, ValidationError) as exc:
        raise cast(HTTPException, patch_http_error(exc)) from exc
    return SettingsPatchOut(
        version=upd.version,
        values=upd.settings.model_dump(mode="json"),
        changed=upd.changed,
        restart_required=restart_required(upd.changed),
    )


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

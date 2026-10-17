"""Обзор аккаунтов учётки: массовые действия."""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.api.container import Container
from app.api.deps import SessionContext, container, require_csrf
from app.api.errors import CSRF, ValidationErrorOut
from app.api.routes_settings import EngineStarting, apply_account_patch, patch_http_error
from app.api.scope import AccountScope
from app.db.accounts import AccountDeleting, AccountInfo
from app.db.reads import DbReads
from app.engine.facade import LockLostError
from app.engine.fence import LeaseLost
from app.engine.settings import (
    ChatIsSelf,
    SettingsConflict,
    SettingsLeafError,
    SettingsOutOfBounds,
    SettingsPatch,
    SettingsPatchError,
    restart_required,
    settings_diff,
    validate_changes_shape,
)

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
    # Сейчас только `audit_failed`: пауза записана, запись аудита упала
    warnings: list[str] = []


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
        warnings: list[str] | None = None,
    ) -> EngineBulkResultOut:
        return EngineBulkResultOut(
            account_id=account_id, status=status, reason=reason, host=host, warnings=warnings or []
        )

    if info is None:
        return row("error", "account_not_found")
    if info.status == "deleting":
        return row("error", "account_deleting")
    runtime = c.engines.get(account_id)
    facade = runtime.facade if runtime is not None else None
    if facade is None:
        return row("skipped", "engine_not_running", c.engines.host_reason(account_id))
    try:
        update = await facade.set_paused(paused, by)
    except (LockLostError, LeaseLost):
        return row("error", "lock_lost")
    except SettingsConflict:
        return row("error", "version_conflict")
    except Exception:
        log.exception("bulk pause failed for account %s", account_id)
        return row("error", "internal_error")
    if update is None:
        return row("unchanged", "already_paused" if paused else "already_running")
    return row("done", warnings=["audit_failed"] if update.audit_failed else None)


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


# Попыток записи при конфликте версий: движок сам правит настройки (артефакт, гаджеты, `startup`)
SETTINGS_ATTEMPTS = 3


class SettingsBulkIn(BaseModel):
    account_ids: list[int] = Field(min_length=1, max_length=MAX_BULK_IDS)
    changes: dict[str, Any] = Field(min_length=1)
    confirm_live: bool = False

    @field_validator("account_ids")
    @classmethod
    def _unique(cls, ids: list[int]) -> list[int]:
        if len(set(ids)) != len(ids):
            raise ValueError("account_ids must be unique")
        return ids


class SettingsBulkErrorOut(BaseModel):
    code: str
    path: str | None = None
    loc: list[str | int] | None = None


class SettingsBulkResultOut(BaseModel):
    account_id: int
    status: Literal["applied", "unchanged", "error"]
    version: int | None
    changed: dict[str, list[Any]]
    restart_required: list[str]
    error: SettingsBulkErrorOut | None
    # Сейчас только `audit_failed`: настройки записаны, запись аудита смены режима упала
    warnings: list[str] = []


class SettingsBulkOut(BaseModel):
    results: list[SettingsBulkResultOut]


def _failure(account_id: int, error: SettingsBulkErrorOut) -> SettingsBulkResultOut:
    return SettingsBulkResultOut(
        account_id=account_id,
        status="error",
        version=None,
        changed={},
        restart_required=[],
        error=error,
    )


def _error_of(exc: Exception, account_id: int) -> SettingsBulkErrorOut:
    """Ошибка правки одного аккаунта: общие обработчики приложения оборвали бы весь запрос."""
    if isinstance(exc, AccountDeleting):
        return SettingsBulkErrorOut(code="account_deleting")
    if isinstance(exc, KeyError):
        return SettingsBulkErrorOut(code="account_not_found")
    if isinstance(exc, EngineStarting):
        return SettingsBulkErrorOut(code="engine_starting")
    if isinstance(exc, SettingsConflict):
        return SettingsBulkErrorOut(code="version_conflict")
    if isinstance(exc, SettingsPatchError):
        if exc.code == "live_requires_confirm":
            return SettingsBulkErrorOut(code="live_requires_confirm", path=exc.path)
        return SettingsBulkErrorOut(code="validation_error", loc=[*exc.path.split(".")])
    if isinstance(exc, SettingsLeafError):
        return SettingsBulkErrorOut(code="validation_error", loc=list(exc.errors[0]["loc"]))
    if isinstance(exc, ValidationError):
        return SettingsBulkErrorOut(code="validation_error", loc=list(exc.errors()[0]["loc"]))
    if isinstance(exc, SettingsOutOfBounds):
        return SettingsBulkErrorOut(code="setting_out_of_bounds", path=exc.path)
    if isinstance(exc, ChatIsSelf):
        return SettingsBulkErrorOut(code="chat_is_self", path=", ".join(exc.fields))
    if isinstance(exc, (LeaseLost, LockLostError)):
        return SettingsBulkErrorOut(code="lock_lost")
    log.error("bulk settings failed for account %s", account_id, exc_info=exc)
    return SettingsBulkErrorOut(code="internal_error")


async def _patch_one(
    c: Container, info: AccountInfo, body: SettingsBulkIn, by: str
) -> SettingsBulkResultOut:
    for _ in range(SETTINGS_ATTEMPTS):
        scope = AccountScope(info, DbReads(c.db, info.id), c.engines.get(info.id))
        facade = scope.facade
        if facade is not None:
            values, version = (
                facade.settings.current.model_dump(mode="json"),
                facade.settings.version,
            )
        else:
            values, version = await scope.reads.settings()
        # Пустое изменение не пишет версию: в массовой операции шум в истории не нужен
        new = SettingsPatch(body.changes, confirm_live=True).apply(values)
        if not settings_diff(values, new.model_dump(mode="json")):
            return SettingsBulkResultOut(
                account_id=info.id,
                status="unchanged",
                version=version,
                changed={},
                restart_required=[],
                error=None,
            )
        try:
            upd = await apply_account_patch(
                c,
                scope,
                body.changes,
                confirm_live=body.confirm_live,
                version=version,
                by=by,
                tolerate_audit_failure=True,
                # Аренда у другого хоста: здесь движок не появится, ждать незачем
                wait_engine=c.engines.host_reason(info.id) != "locked_elsewhere",
            )
        except SettingsConflict:
            continue
        return SettingsBulkResultOut(
            account_id=info.id,
            status="applied",
            version=upd.version,
            changed=upd.changed,
            restart_required=restart_required(upd.changed),
            error=None,
            warnings=["audit_failed"] if upd.audit_failed else [],
        )
    return _failure(info.id, SettingsBulkErrorOut(code="version_conflict"))


async def _settings_of(
    c: Container, info: AccountInfo | None, account_id: int, body: SettingsBulkIn, by: str
) -> SettingsBulkResultOut:
    if info is None:
        return _failure(account_id, SettingsBulkErrorOut(code="account_not_found"))
    if info.status == "deleting":
        return _failure(account_id, SettingsBulkErrorOut(code="account_deleting"))
    try:
        return await _patch_one(c, info, body, by)
    except Exception as exc:
        return _failure(account_id, _error_of(exc, account_id))


@router.post(
    "/settings",
    response_model=SettingsBulkOut,
    responses={
        **CSRF,
        422: {
            "model": ValidationErrorOut,
            "description": "invalid body or changes (nothing is written)",
        },
    },
)
async def overview_settings(
    body: SettingsBulkIn,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    c: Annotated[Container, Depends(container)],
) -> SettingsBulkOut:
    """Одно изменение настроек у нескольких аккаунтов (семантика `PATCH /accounts/{id}/settings`
    без версии: путь пишется поверх текущего значения каждого). Тело проверяется целиком до
    записи; дальше аккаунты по одному, без общей транзакции, сбой одного остальных не
    останавливает. Чужой и несуществующий аккаунт неразличимы."""
    try:
        validate_changes_shape(body.changes)
    except (SettingsPatchError, SettingsLeafError) as exc:
        raise cast(HTTPException, patch_http_error(exc)) from exc
    engine = body.changes.get("engine")
    if isinstance(engine, dict) and engine.get("mode") == "live" and not body.confirm_live:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            [
                {
                    "loc": ["body", "confirm_live"],
                    "msg": "live_requires_confirm",
                    "type": "live_requires_confirm",
                }
            ],
        )
    owned = {a.id: a for a in await c.accounts.owned(ctx.user_id)}
    results = [await _settings_of(c, owned.get(i), i, body, ctx.login) for i in body.account_ids]
    return SettingsBulkOut(results=results)

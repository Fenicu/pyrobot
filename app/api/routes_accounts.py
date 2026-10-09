"""Аккаунты учётки (раздел 4.4 спеки): список со статусами, создание, переименование, включение
и выключение, удаление и перезапуск движка аккаунта, а также здоровье хоста движков. Каждая
запись в `accounts` будит сверку хоста: движок стартует, останавливается или чистится сразу, а не
через `reconcile_s`."""

from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, StringConstraints

from app.api.container import Container
from app.api.deps import (
    SessionContext,
    container,
    current_session,
    require_csrf,
    require_owner,
)
from app.api.errors import (
    ACCOUNT_DELETING,
    ACCOUNT_NOT_FOUND,
    AUTH,
    BLOCKED_BY_OWNER,
    CAPACITY_REACHED,
    CONFIRM_NAME_MISMATCH,
    CSRF,
    ENGINE,
    LIMIT_REACHED,
    NAME_TAKEN,
    NOT_AUTHENTICATED,
    SERVER_FULL,
    error,
)
from app.api.routes_engine import IsoDatetime
from app.api.scope import AccountScope, account_router, account_scope, running
from app.db.accounts import (
    AccountDeleting,
    AccountOverview,
    AccountStatus,
    BlockedByOwner,
    CapacityReached,
    LimitReached,
    NameTaken,
    ServerFull,
    UserInactive,
)
from app.engine.tg_auth import TgState

router = APIRouter(prefix="/api/v1", tags=["accounts"])
account = account_router("accounts")

AccountName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


class AccountTgOut(BaseModel):
    # Пользователь Telegram, к которому аккаунт привязан на всю жизнь; None — ещё не входил.
    user_id: int | None
    # Движок аккаунта запущен, и Telegram в онлайне.
    online: bool


class UnreadOut(BaseModel):
    """Непрочитанные уведомления аккаунта по уровням."""

    warn: int
    error: int


class AccountBusyOut(BaseModel):
    activity: str
    until: IsoDatetime


class AccountAlertOut(BaseModel):
    level: Literal["error", "warn"]
    text: str


class AccountOut(BaseModel):
    id: int
    name: str
    # Желаемое состояние (`enabled`, `disabled`, `error`, `deleting`) и причина.
    status: AccountStatus
    status_reason: str | None
    blocked: bool
    blocked_reason: str | None
    tg: AccountTgOut
    # У запущенного движка — его, иначе — из настроек в базе.
    mode: Literal["dry_run", "live"]
    paused: bool
    killed: bool
    last_action_at: IsoDatetime | None
    unread: UnreadOut
    # Код компании (`piper`, `hooli`, `stark`, `umbrl`, `wayne`, `bmesa`) и тег команды из
    # последнего снимка состояния; null — снимка нет или поле ещё не наблюдалось.
    company: str | None
    team_tag: str | None
    # Уровень и занятость из последнего снимка; `until` отдаётся как есть, в том числе прошедший
    # (свободен ли персонаж сейчас, решает клиент). null — снимка нет или поле не прочиталось.
    level: int | None
    busy: AccountBusyOut | None
    # Идёт забег метро по последнему снимку.
    in_metro: bool
    # Самое важное непрочитанное уведомление: ошибка, а без ошибок — самое новое предупреждение.
    alert: AccountAlertOut | None


class AccountCreateIn(BaseModel):
    name: AccountName


class AccountPatchIn(BaseModel):
    name: AccountName | None = None
    enabled: bool | None = None


class AccountDeleteIn(BaseModel):
    # Имя аккаунта как есть: удаление необратимо.
    confirm_name: str


class HostStatusOut(BaseModel):
    """Здоровье процесса и хоста движков — отдельно от статусов аккаунтов."""

    holder: str
    # Соединение блокировок открыто: без него аренды не захватываются и не продлеваются.
    lock_connection_ok: bool
    # Аккаунты с запущенным движком.
    engines: list[int]
    # Аккаунты, которые хост не смог захватить: `locked_elsewhere` или `lease_active`.
    busy: dict[int, str]
    loop_lag_ms: float
    # Задачи процесса идут, ни одна не ждёт перезапуска.
    tasks_ok: bool


def _out(c: Container, o: AccountOverview) -> AccountOut:
    info = o.account
    engine = c.engines.get(info.id)
    f = engine.facade if engine is not None else None
    if f is not None:
        st = f.status()
        mode, paused, killed = st.mode, st.paused, st.killed
        online = st.tg.state is TgState.ONLINE
    else:
        mode, paused, killed = o.engine.mode, o.engine.paused, o.engine.killed
        online = False
    return AccountOut.model_validate(
        {
            "id": info.id,
            "name": info.name,
            "status": info.status,
            "status_reason": info.status_reason,
            "blocked": info.blocked,
            "blocked_reason": info.blocked_reason,
            "tg": AccountTgOut(user_id=info.tg_user_id, online=online),
            "mode": mode,
            "paused": paused,
            "killed": killed,
            "last_action_at": o.last_action_at,
            "unread": UnreadOut(warn=o.unread_warn, error=o.unread_error),
            "company": o.company,
            "team_tag": o.team_tag,
            "level": o.level,
            "busy": (
                AccountBusyOut(activity=o.busy.activity, until=o.busy.until)
                if o.busy is not None
                else None
            ),
            "in_metro": o.in_metro,
            "alert": (
                AccountAlertOut(level=o.alert.level, text=o.alert.text)
                if o.alert is not None
                else None
            ),
        }
    )


async def _one(c: Container, owner_id: int, account_id: int) -> AccountOut:
    for o in await c.accounts.overview(owner_id):
        if o.account.id == account_id:
            return _out(c, o)
    # Аккаунт удалили между записью и чтением.
    raise HTTPException(status.HTTP_404_NOT_FOUND, ACCOUNT_NOT_FOUND)


@router.get("/accounts", response_model=list[AccountOut], responses=AUTH)
async def list_accounts(
    ctx: Annotated[SessionContext, Depends(current_session)],
    c: Annotated[Container, Depends(container)],
) -> list[AccountOut]:
    """Аккаунты текущей учётки, удаляемые — до конца чистки."""
    return [_out(c, o) for o in await c.accounts.overview(ctx.user_id)]


@router.post(
    "/accounts",
    response_model=AccountOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        **CSRF,
        409: error(NAME_TAKEN, LIMIT_REACHED, SERVER_FULL, CAPACITY_REACHED),
    },
)
async def create_account(
    body: AccountCreateIn,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    c: Annotated[Container, Depends(container)],
) -> AccountOut:
    """Новый аккаунт — `enabled`, настройки по умолчанию (`dry_run`): движок поднимет хост, он
    работает без Telegram до первого входа. Учётку отключили или удалили посреди запроса — 401,
    как у отозванной сессии."""
    try:
        created = await c.accounts.create(
            ctx.user_id,
            body.name,
            total_max=c.server_settings.current.limits.max_accounts_total,
            capacity=c.config.max_engines,
        )
    except UserInactive as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, NOT_AUTHENTICATED) from exc
    except LimitReached as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, LIMIT_REACHED) from exc
    except ServerFull as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, SERVER_FULL) from exc
    except NameTaken as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, NAME_TAKEN) from exc
    except CapacityReached as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, CAPACITY_REACHED) from exc
    c.engines.poke()
    return await _one(c, ctx.user_id, created.id)


@router.get("/host/status", response_model=HostStatusOut, responses=AUTH)
async def host_status(
    _: Annotated[SessionContext, Depends(require_owner)],
    c: Annotated[Container, Depends(container)],
) -> HostStatusOut:
    return HostStatusOut.model_validate(asdict(c.engines.status()))


@account.patch(
    "",
    response_model=AccountOut,
    responses={
        **CSRF,
        403: error(BLOCKED_BY_OWNER),
        409: error(NAME_TAKEN, CAPACITY_REACHED, ACCOUNT_DELETING),
    },
)
async def patch_account(
    body: AccountPatchIn,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    scope: Annotated[AccountScope, Depends(account_scope)],
    c: Annotated[Container, Depends(container)],
) -> AccountOut:
    """Переименование, включение (снимает причину `error`) и выключение; удаляемый аккаунт не
    правится. Включение у учётки, отключённой посреди запроса, — 401."""
    try:
        await c.accounts.update(
            scope.account.id,
            name=body.name,
            enabled=body.enabled,
            capacity=c.config.max_engines,
        )
    except UserInactive as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, NOT_AUTHENTICATED) from exc
    except BlockedByOwner as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, BLOCKED_BY_OWNER) from exc
    except NameTaken as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, NAME_TAKEN) from exc
    except CapacityReached as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, CAPACITY_REACHED) from exc
    except AccountDeleting as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, ACCOUNT_DELETING) from exc
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, ACCOUNT_NOT_FOUND) from exc
    c.engines.poke()
    return await _one(c, ctx.user_id, scope.account.id)


@account.delete(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_class=Response,
    responses={**CSRF, 422: {"description": f"invalid body | {CONFIRM_NAME_MISMATCH}"}},
)
async def delete_account(
    body: AccountDeleteIn,
    _: Annotated[SessionContext, Depends(require_csrf)],
    scope: Annotated[AccountScope, Depends(account_scope)],
    c: Annotated[Container, Depends(container)],
) -> Response:
    """Аккаунт становится `deleting`; хост останавливает его движок с выходом из Telegram и
    удаляет данные в фоне — до конца чистки аккаунт виден в списке."""
    if body.confirm_name != scope.account.name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, CONFIRM_NAME_MISMATCH)
    await c.accounts.mark_deleting(scope.account.id)
    c.engines.poke()
    return Response(status_code=status.HTTP_202_ACCEPTED)


@account.post(
    "/engine/restart",
    status_code=status.HTTP_202_ACCEPTED,
    response_class=Response,
    responses={**CSRF, **ENGINE, 409: error(ACCOUNT_DELETING)},
)
async def restart_engine(
    _: Annotated[SessionContext, Depends(require_csrf)],
    scope: Annotated[AccountScope, Depends(account_scope)],
    c: Annotated[Container, Depends(container)],
) -> Response:
    """Новое поколение движка: хост штатно остановит запущенный и поднимет новый (настройки с
    `restart_required` применяются так). Удаляемый аккаунт не перезапускается — и тогда, когда
    его движок ещё зарегистрирован."""
    if scope.account.status == "deleting":
        raise HTTPException(status.HTTP_409_CONFLICT, ACCOUNT_DELETING)
    # Движок не запущен — 503.
    await running(scope)
    await c.accounts.restart(scope.account.id)
    c.engines.poke()
    return Response(status_code=status.HTTP_202_ACCEPTED)

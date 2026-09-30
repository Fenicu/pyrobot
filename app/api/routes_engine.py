from collections.abc import Awaitable
from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, status
from pydantic import BaseModel, Field, PlainSerializer

from app.api.container import Container
from app.api.deps import SessionContext, container, require_csrf
from app.api.errors import AUTH, CSRF, ENGINE, Responses, error
from app.api.scope import AccountScope, account_router, account_scope, running
from app.db.accounts import AccountStatus
from app.engine.facade import EngineFacade, LockLostError
from app.engine.tg_auth import AttemptMismatch, TgAuthError, TgBackendError, TgState, TgStatus
from app.engine.transport.base import FloodWait

router = account_router("engine")
# Даты — через isoformat(), как в прежнем ответе (jsonable_encoder) и `now` в /state: `+00:00`.
IsoDatetime = Annotated[datetime, PlainSerializer(datetime.isoformat, when_used="json")]


_WRITE: Responses = {**CSRF, **ENGINE}
# Вход в Telegram: ошибки попытки и Telegram; неверный код или пароль — 200 с `error`.
_TG_LOGIN: Responses = {
    **_WRITE,
    400: error("invalid_phone", "password_required", "<код TgAuthError>"),
    409: error("already online", "another login in progress", "unknown attempt", "state is …"),
    429: error("flood_wait"),
    502: error("send_code_failed", "sign_in_failed", "check_password_failed"),
}


class KillIn(BaseModel):
    reason: str = Field(min_length=1, max_length=200)


class PhoneIn(BaseModel):
    phone: str


class CodeIn(BaseModel):
    attempt_id: str
    code: str


class TgPasswordIn(BaseModel):
    attempt_id: str
    password: str


class TgStatusOut(BaseModel):
    state: TgState
    user_id: int | None
    attempt_id: str | None
    # Код последней ошибки входа (`invalid_code`, `session_revoked`, …).
    error: str | None
    # Пользователь Telegram, к которому аккаунт привязан на всю жизнь (`accounts.tg_user_id`);
    # None — привязывает первый вход. Выход из Telegram привязку не снимает.
    bound_user_id: int | None


class EngineStatusOut(BaseModel):
    # Движок аккаунта запущен в этом процессе. Без него режим, пауза и kill — из настроек в
    # базе, `tg.state` — `stopped`, счётчики — 0, проверки здоровья — false.
    running: bool
    # Желаемое состояние аккаунта (`accounts.status`) и причина `disabled`/`error`.
    status: AccountStatus
    status_reason: str | None
    # Почему движок не запущен на этом хосте: `locked_elsewhere` (аккаунт у другого хоста),
    # `lease_active` (ждёт срока прежней аренды); null — запущен или хост не пытался.
    host_reason: str | None
    mode: Literal["dry_run", "live"]
    paused: bool
    # Сценарий, который сейчас исполняет планировщик.
    scenario: str | None
    next_wake: IsoDatetime | None
    killed: bool
    kill_reason: str | None
    spending_blocked: str | None
    tg: TgStatusOut
    queue: int
    # Текст или callback_data действия, которое шлюз сейчас отправляет.
    in_flight: str | None
    pipeline_backlog: int
    pipeline_healthy: bool
    workers_ok: bool
    lock_ok: bool
    loop_lag_ms: float


def _tg(st: TgStatus) -> TgStatusOut:
    return TgStatusOut(
        state=st.state,
        user_id=st.user_id,
        attempt_id=st.attempt_id,
        error=st.error,
        bound_user_id=st.bound_user_id,
    )


def _stopped_tg(scope: AccountScope) -> TgStatusOut:
    # Привязка — из `accounts`: движка, который держит её копию, нет.
    return TgStatusOut(
        state=TgState.STOPPED,
        user_id=None,
        attempt_id=None,
        error=None,
        bound_user_id=scope.account.tg_user_id,
    )


@router.get("/engine/status", response_model=EngineStatusOut, responses=AUTH)
async def engine_status(
    scope: Annotated[AccountScope, Depends(account_scope)],
    c: Annotated[Container, Depends(container)],
) -> EngineStatusOut:
    account = scope.account
    host_reason = c.engines.host_reason(account.id)
    f = scope.facade
    if f is not None:
        st = f.status()
        data = asdict(st)
        data["tg"] = _tg(st.tg)
        return EngineStatusOut.model_validate(
            {
                **data,
                "running": True,
                "status": account.status,
                "status_reason": account.status_reason,
                "host_reason": host_reason,
            }
        )
    settings, _ = await scope.reads.settings()
    engine = settings.engine
    return EngineStatusOut(
        running=False,
        status=account.status,
        status_reason=account.status_reason,
        host_reason=host_reason,
        mode=engine.mode,
        paused=engine.paused,
        scenario=None,
        next_wake=None,
        killed=engine.killed,
        kill_reason=engine.kill_reason if engine.killed else None,
        spending_blocked=None,
        tg=_stopped_tg(scope),
        queue=0,
        in_flight=None,
        pipeline_backlog=0,
        pipeline_healthy=False,
        workers_ok=False,
        lock_ok=False,
        loop_lag_ms=0.0,
    )


@router.post("/engine/kill", status_code=status.HTTP_204_NO_CONTENT, responses=_WRITE)
async def engine_kill(
    body: KillIn,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    f: Annotated[EngineFacade, Depends(running)],
) -> None:
    await f.kill(body.reason, by=ctx.login)


@router.post(
    "/engine/unkill",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**_WRITE, 409: error("lock_lost")},
)
async def engine_unkill(
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    f: Annotated[EngineFacade, Depends(running)],
) -> None:
    try:
        await f.unkill(by=ctx.login)
    except LockLostError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "lock_lost") from exc


@router.post("/engine/pause", status_code=status.HTTP_204_NO_CONTENT, responses=_WRITE)
async def engine_pause(
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    f: Annotated[EngineFacade, Depends(running)],
) -> None:
    await f.pause(by=ctx.login)


@router.post("/engine/resume", status_code=status.HTTP_204_NO_CONTENT, responses=_WRITE)
async def engine_resume(
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    f: Annotated[EngineFacade, Depends(running)],
) -> None:
    await f.resume(by=ctx.login)


@router.post("/engine/reconciled", status_code=status.HTTP_204_NO_CONTENT, responses=_WRITE)
async def engine_reconciled(
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    f: Annotated[EngineFacade, Depends(running)],
) -> None:
    await f.reconciled(by=ctx.login)


@router.get("/tg/status", response_model=TgStatusOut, responses=AUTH)
async def tg_status(scope: Annotated[AccountScope, Depends(account_scope)]) -> TgStatusOut:
    f = scope.facade
    return _tg(f.tg.status()) if f is not None else _stopped_tg(scope)


async def _guard(coro: Awaitable[TgStatus]) -> TgStatusOut:
    try:
        return _tg(await coro)
    except AttemptMismatch as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except FloodWait as exc:
        retry_after = max(1, int(exc.seconds) + 1)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "flood_wait",
            headers={"Retry-After": str(retry_after)},
        ) from exc
    except TgBackendError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, exc.code) from exc
    except TgAuthError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.code) from exc


@router.post("/tg/login/start", response_model=TgStatusOut, responses=_TG_LOGIN)
async def tg_start(
    body: PhoneIn,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    f: Annotated[EngineFacade, Depends(running)],
) -> TgStatusOut:
    return await _guard(f.tg.start(body.phone, owner=str(ctx.session_id)))


@router.post("/tg/login/code", response_model=TgStatusOut, responses=_TG_LOGIN)
async def tg_code(
    body: CodeIn,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    f: Annotated[EngineFacade, Depends(running)],
) -> TgStatusOut:
    return await _guard(f.tg.submit_code(body.attempt_id, str(ctx.session_id), body.code))


@router.post("/tg/login/password", response_model=TgStatusOut, responses=_TG_LOGIN)
async def tg_password(
    body: TgPasswordIn,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    f: Annotated[EngineFacade, Depends(running)],
) -> TgStatusOut:
    return await _guard(f.tg.submit_password(body.attempt_id, str(ctx.session_id), body.password))


@router.post("/tg/logout", response_model=TgStatusOut, responses=_WRITE)
async def tg_logout(
    _: Annotated[SessionContext, Depends(require_csrf)],
    f: Annotated[EngineFacade, Depends(running)],
) -> TgStatusOut:
    return _tg(await f.tg.logout())

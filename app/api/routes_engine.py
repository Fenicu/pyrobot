from collections.abc import Awaitable
from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.container import Container
from app.api.deps import SessionContext, container, current_session, require_csrf
from app.engine.facade import EngineFacade, LockLostError
from app.engine.tg_auth import AttemptMismatch, TgAuthError, TgBackendError, TgState, TgStatus
from app.engine.transport.base import FloodWait

router = APIRouter(prefix="/api/v1", tags=["engine"])


def facade(c: Annotated[Container, Depends(container)]) -> EngineFacade:
    if c.facade is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "engine not started")
    return c.facade


class KillIn(BaseModel):
    reason: str = Field(min_length=1, max_length=200)


class PhoneIn(BaseModel):
    phone: str


class CodeIn(BaseModel):
    attempt_id: str
    code: str


class PasswordIn(BaseModel):
    attempt_id: str
    password: str


class TgStatusOut(BaseModel):
    state: TgState
    user_id: int | None
    attempt_id: str | None
    # Код последней ошибки входа (`invalid_code`, `session_revoked`, …).
    error: str | None


class EngineStatusOut(BaseModel):
    mode: Literal["dry_run", "live"]
    paused: bool
    # Сценарий, который сейчас исполняет планировщик.
    scenario: str | None
    next_wake: datetime | None
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
        state=st.state, user_id=st.user_id, attempt_id=st.attempt_id, error=st.error
    )


@router.get("/engine/status", response_model=EngineStatusOut)
async def engine_status(
    _: Annotated[SessionContext, Depends(current_session)],
    f: Annotated[EngineFacade, Depends(facade)],
) -> EngineStatusOut:
    st = f.status()
    data = asdict(st)
    data["tg"] = _tg(st.tg)
    return EngineStatusOut.model_validate(data)


@router.post("/engine/kill", status_code=status.HTTP_204_NO_CONTENT)
async def engine_kill(
    body: KillIn,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    f: Annotated[EngineFacade, Depends(facade)],
) -> None:
    await f.kill(body.reason, by=ctx.login)


@router.post("/engine/unkill", status_code=status.HTTP_204_NO_CONTENT)
async def engine_unkill(
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    f: Annotated[EngineFacade, Depends(facade)],
) -> None:
    try:
        await f.unkill(by=ctx.login)
    except LockLostError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "lock_lost") from exc


@router.post("/engine/pause", status_code=status.HTTP_204_NO_CONTENT)
async def engine_pause(
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    f: Annotated[EngineFacade, Depends(facade)],
) -> None:
    await f.pause(by=ctx.login)


@router.post("/engine/resume", status_code=status.HTTP_204_NO_CONTENT)
async def engine_resume(
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    f: Annotated[EngineFacade, Depends(facade)],
) -> None:
    await f.resume(by=ctx.login)


@router.post("/engine/reconciled", status_code=status.HTTP_204_NO_CONTENT)
async def engine_reconciled(
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    f: Annotated[EngineFacade, Depends(facade)],
) -> None:
    await f.reconciled(by=ctx.login)


@router.get("/tg/status", response_model=TgStatusOut)
async def tg_status(
    _: Annotated[SessionContext, Depends(current_session)],
    f: Annotated[EngineFacade, Depends(facade)],
) -> TgStatusOut:
    return _tg(f.tg.status())


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


@router.post("/tg/login/start", response_model=TgStatusOut)
async def tg_start(
    body: PhoneIn,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    f: Annotated[EngineFacade, Depends(facade)],
) -> TgStatusOut:
    return await _guard(f.tg.start(body.phone, owner=str(ctx.session_id)))


@router.post("/tg/login/code", response_model=TgStatusOut)
async def tg_code(
    body: CodeIn,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    f: Annotated[EngineFacade, Depends(facade)],
) -> TgStatusOut:
    return await _guard(f.tg.submit_code(body.attempt_id, str(ctx.session_id), body.code))


@router.post("/tg/login/password", response_model=TgStatusOut)
async def tg_password(
    body: PasswordIn,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    f: Annotated[EngineFacade, Depends(facade)],
) -> TgStatusOut:
    return await _guard(f.tg.submit_password(body.attempt_id, str(ctx.session_id), body.password))


@router.post("/tg/logout", response_model=TgStatusOut)
async def tg_logout(
    _: Annotated[SessionContext, Depends(require_csrf)],
    f: Annotated[EngineFacade, Depends(facade)],
) -> TgStatusOut:
    return _tg(await f.tg.logout())

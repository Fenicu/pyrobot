from collections.abc import Awaitable
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.container import Container
from app.api.deps import SessionContext, container, current_session, require_csrf
from app.engine.facade import EngineFacade
from app.engine.tg_auth import AttemptMismatch, TgStatus

router = APIRouter(prefix="/api/v1", tags=["engine"])


def facade(c: Annotated[Container, Depends(container)]) -> EngineFacade:
    if c.facade is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "engine not started")
    return c.facade


class KillIn(BaseModel):
    reason: str


class PhoneIn(BaseModel):
    phone: str


class CodeIn(BaseModel):
    attempt_id: str
    code: str


class PasswordIn(BaseModel):
    attempt_id: str
    password: str


def _tg(st: TgStatus) -> dict[str, Any]:
    return {
        "state": st.state.value,
        "user_id": st.user_id,
        "attempt_id": st.attempt_id,
        "error": st.error,
    }


@router.get("/engine/status")
async def engine_status(
    f: Annotated[EngineFacade, Depends(facade)],
    _: Annotated[SessionContext, Depends(current_session)],
) -> dict[str, Any]:
    st = f.status()
    data = asdict(st)
    data["tg"] = _tg(st.tg)
    return data


@router.post("/engine/kill", status_code=status.HTTP_204_NO_CONTENT)
async def engine_kill(
    body: KillIn,
    f: Annotated[EngineFacade, Depends(facade)],
    ctx: Annotated[SessionContext, Depends(require_csrf)],
) -> None:
    await f.kill(body.reason, by=ctx.login)


@router.post("/engine/unkill", status_code=status.HTTP_204_NO_CONTENT)
async def engine_unkill(
    f: Annotated[EngineFacade, Depends(facade)],
    ctx: Annotated[SessionContext, Depends(require_csrf)],
) -> None:
    await f.unkill(by=ctx.login)


@router.post("/engine/reconciled", status_code=status.HTTP_204_NO_CONTENT)
async def engine_reconciled(
    f: Annotated[EngineFacade, Depends(facade)],
    ctx: Annotated[SessionContext, Depends(require_csrf)],
) -> None:
    await f.reconciled(by=ctx.login)


@router.get("/tg/status")
async def tg_status(
    f: Annotated[EngineFacade, Depends(facade)],
    _: Annotated[SessionContext, Depends(current_session)],
) -> dict[str, Any]:
    return _tg(f.tg.status())


async def _guard(coro: Awaitable[TgStatus]) -> dict[str, Any]:
    try:
        return _tg(await coro)
    except AttemptMismatch as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/tg/login/start")
async def tg_start(
    body: PhoneIn,
    f: Annotated[EngineFacade, Depends(facade)],
    ctx: Annotated[SessionContext, Depends(require_csrf)],
) -> dict[str, Any]:
    return await _guard(f.tg.start(body.phone, owner=str(ctx.session_id)))


@router.post("/tg/login/code")
async def tg_code(
    body: CodeIn,
    f: Annotated[EngineFacade, Depends(facade)],
    ctx: Annotated[SessionContext, Depends(require_csrf)],
) -> dict[str, Any]:
    return await _guard(f.tg.submit_code(body.attempt_id, str(ctx.session_id), body.code))


@router.post("/tg/login/password")
async def tg_password(
    body: PasswordIn,
    f: Annotated[EngineFacade, Depends(facade)],
    ctx: Annotated[SessionContext, Depends(require_csrf)],
) -> dict[str, Any]:
    return await _guard(f.tg.submit_password(body.attempt_id, str(ctx.session_id), body.password))


@router.post("/tg/logout")
async def tg_logout(
    f: Annotated[EngineFacade, Depends(facade)],
    _: Annotated[SessionContext, Depends(require_csrf)],
) -> dict[str, Any]:
    return _tg(await f.tg.logout())

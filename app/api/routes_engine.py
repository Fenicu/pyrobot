import math
from collections.abc import Awaitable
from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, status
from pydantic import BaseModel, Field, PlainSerializer

from app.api.container import Container
from app.api.deps import SessionContext, container, require_csrf
from app.api.errors import (
    ACCOUNT_DELETING,
    AUTH,
    CSRF,
    ENGINE,
    FLOOD_WAIT,
    GAME_CHAT_MISMATCH,
    TG_CODE_RATE_LIMITED,
    TG_NOT_ONLINE,
    Responses,
    error,
)
from app.api.scope import AccountScope, account_router, account_scope, running
from app.db.accounts import AccountStatus
from app.engine.facade import EngineFacade, LockLostError, TgNotOnline
from app.engine.tg_auth import (
    AttemptMismatch,
    CodeRateLimited,
    TgAuthError,
    TgBackendError,
    TgState,
    TgStatus,
)
from app.engine.transport.base import (
    FloodWait,
    JoinStatus,
    TransportAuthLost,
    TransportRejected,
)

router = account_router("engine")
# Даты — через isoformat(), как в прежнем ответе (jsonable_encoder) и `now` в /state: `+00:00`.
IsoDatetime = Annotated[datetime, PlainSerializer(datetime.isoformat, when_used="json")]


_WRITE: Responses = {**CSRF, **ENGINE}
# Вход в Telegram: ошибки попытки и Telegram; неверный код или пароль — 200 с `error`.
_TG_LOGIN: Responses = {
    **_WRITE,
    400: error("invalid_phone", "password_required", "<код TgAuthError>"),
    409: error("already online", "another login in progress", "unknown attempt", "state is …"),
    429: error(FLOOD_WAIT),
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
    # Код последней ошибки входа (`invalid_code`, `session_revoked`, …); отказ в онлайне после
    # входа — `unexpected_user`, `tg_user_taken`, `chat_is_self`, `bind_failed`.
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
    # Аренда аккаунта у движка действует. Задержка цикла событий — здоровье хоста, а не аккаунта.
    lease_ok: bool
    # Аккаунт состоит в общем чате игры (@startupwarschat, там пишет SWINFO): false — сверка
    # истории его не читает, вступить — `POST …/tg/game-chat/join`; null — движок не запущен или
    # чат ещё не читался.
    game_chat_member: bool | None


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
    engine = await scope.reads.engine()
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
        lease_ok=False,
        game_chat_member=None,
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


def _flood_wait(exc: FloodWait) -> HTTPException:
    retry_after = max(1, int(exc.seconds) + 1)
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS, FLOOD_WAIT, headers={"Retry-After": str(retry_after)}
    )


async def _guard(coro: Awaitable[TgStatus]) -> TgStatusOut:
    try:
        return _tg(await coro)
    except AttemptMismatch as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except FloodWait as exc:
        raise _flood_wait(exc) from exc
    except CodeRateLimited as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            TG_CODE_RATE_LIMITED,
            headers={"Retry-After": str(max(1, math.ceil(exc.retry_after_s)))},
        ) from exc
    except TgBackendError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, exc.code) from exc
    except TgAuthError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.code) from exc


@router.post(
    "/tg/login/start",
    response_model=TgStatusOut,
    responses={**_TG_LOGIN, 429: error(FLOOD_WAIT, TG_CODE_RATE_LIMITED)},
)
async def tg_start(
    body: PhoneIn,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    f: Annotated[EngineFacade, Depends(running)],
) -> TgStatusOut:
    """Запрос кода входа: не больше `PYROBOT_TG_CODES_PER_HOUR` на хост и 3 в час на аккаунт,
    сверх — 429 `tg_code_rate_limited` с `Retry-After`."""
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


class GameChatJoinOut(BaseModel):
    # `request_sent` — заявка ждёт одобрения админов чата, членство не меняется.
    status: JoinStatus
    game_chat_member: bool | None


@router.post(
    "/tg/game-chat/join",
    response_model=GameChatJoinOut,
    responses={
        **_WRITE,
        409: error(ACCOUNT_DELETING, TG_NOT_ONLINE, GAME_CHAT_MISMATCH),
        429: error(FLOOD_WAIT),
        502: error("<код ошибки Telegram>"),
    },
)
async def tg_game_chat_join(
    _: Annotated[SessionContext, Depends(require_csrf)],
    scope: Annotated[AccountScope, Depends(account_scope)],
) -> GameChatJoinOut:
    """Вступление аккаунта в общий чат игры @startupwarschat: перед вступлением id чата по
    username сверяется с `chats.swinfo_chat_id`. После вступления сверка истории сразу
    перечитывает чат."""
    if scope.account.status == "deleting":
        raise HTTPException(status.HTTP_409_CONFLICT, ACCOUNT_DELETING)
    f = await running(scope)
    try:
        joined = await f.join_game_chat()
    except (TgNotOnline, TransportAuthLost) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, TG_NOT_ONLINE) from exc
    except FloodWait as exc:
        raise _flood_wait(exc) from exc
    except TransportRejected as exc:
        if str(exc) == "chat_mismatch":
            raise HTTPException(status.HTTP_409_CONFLICT, GAME_CHAT_MISMATCH) from exc
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return GameChatJoinOut(status=joined, game_chat_member=f.game_chat_member())

import asyncio
import logging
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.api.container import Container
from app.api.deps import (
    COOKIE,
    SessionContext,
    container,
    current_session,
    require_csrf,
    set_session_cookie,
)
from app.api.errors import (
    AUTH,
    CSRF,
    CSRF_MISMATCH,
    INVALID_CODE,
    INVALID_PASSWORD,
    Responses,
    error,
)
from app.api.security import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    dummy_hash,
    hash_password,
    verify_password,
)
from app.db.audit import Actor
from app.db.notifications import DbNotifier
from app.db.users import Role
from app.engine.facade import EngineFacade
from app.engine.tg_auth import TgState

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginIn(BaseModel):
    login: str = Field(max_length=64)
    password: str = Field(max_length=PASSWORD_MAX_LENGTH)


class MeOut(BaseModel):
    login: str
    role: Role
    csrf_token: str


class PasswordChangeIn(BaseModel):
    current: str = Field(max_length=1024)
    new: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)


class RecoveryCodesIn(BaseModel):
    password: str = Field(max_length=PASSWORD_MAX_LENGTH)


class RecoveryCodesOut(BaseModel):
    codes: list[str]


class RecoverStartIn(BaseModel):
    login: str = Field(max_length=64)


class RecoverFinishIn(BaseModel):
    login: str = Field(max_length=64)
    code: str | None = Field(default=None, max_length=16)
    recovery_code: str | None = Field(default=None, max_length=64)
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)


def _raise_if_blocked(c: Container, key: str) -> None:
    wait = c.limiter.blocked_for(key)
    if wait > 0:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "too many attempts",
            headers={"Retry-After": str(int(wait) + 1)},
        )


# Слишком много неудачных попыток: ждать `Retry-After` секунд.
_LIMITED: Responses = {429: error("too many attempts")}


@router.post(
    "/login", response_model=MeOut, responses={401: error("invalid credentials"), **_LIMITED}
)
async def login(
    body: LoginIn,
    request: Request,
    response: Response,
    c: Annotated[Container, Depends(container)],
) -> MeOut:
    key = request.client.host if request.client else "unknown"
    async with c.limiter.lock_for(key):
        _raise_if_blocked(c, key)
        user = await c.auth.get_user(body.login)
        async with c.limiter.slots:
            valid = await verify_password(
                user.password_hash if user else await dummy_hash(), body.password
            )
        if (
            user is None
            or not valid
            or user.disabled_at is not None
            or user.deleting_at is not None
        ):
            c.limiter.failure(key)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
        c.limiter.success(key)
    await c.users.touch_login(user.id)
    token, session = await c.auth.create_session(user.id, key, request.headers.get("user-agent"))
    set_session_cookie(response, token, c.config, int(c.auth.ttl.total_seconds()))
    return MeOut(login=user.login, role=cast(Role, user.role), csrf_token=session.csrf_token)


@router.get("/me", response_model=MeOut, responses=AUTH)
async def me(ctx: Annotated[SessionContext, Depends(current_session)]) -> MeOut:
    return MeOut(login=ctx.login, role=ctx.role, csrf_token=ctx.csrf_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, responses=CSRF)
async def logout(
    response: Response,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    c: Annotated[Container, Depends(container)],
) -> None:
    await c.auth.revoke(ctx.session_id)
    response.delete_cookie(COOKIE, path="/")


@router.post(
    "/password",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **CSRF,
        403: error(CSRF_MISMATCH, "invalid current password"),
        **_LIMITED,
    },
)
async def change_password(
    body: PasswordChangeIn,
    response: Response,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    c: Annotated[Container, Depends(container)],
) -> None:
    key = f"session:{ctx.session_id}"
    async with c.limiter.lock_for(key):
        _raise_if_blocked(c, key)
        user = await c.auth.get_user(ctx.login)
        async with c.limiter.slots:
            if user is None or not await verify_password(user.password_hash, body.current):
                c.limiter.failure(key)
                raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid current password")
            new_hash = await hash_password(body.new)
        c.limiter.success(key)
    await c.auth.change_password(user.id, new_hash)
    response.delete_cookie(COOKIE, path="/")


@router.post(
    "/recovery-codes",
    response_model=RecoveryCodesOut,
    responses={
        **CSRF,
        403: error(CSRF_MISMATCH, INVALID_PASSWORD),
        **_LIMITED,
    },
)
async def reissue_recovery_codes(
    body: RecoveryCodesIn,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    c: Annotated[Container, Depends(container)],
) -> RecoveryCodesOut:
    key = f"session:{ctx.session_id}"
    async with c.limiter.lock_for(key):
        _raise_if_blocked(c, key)
        user = await c.auth.get_user(ctx.login)
        async with c.limiter.slots:
            if user is None or not await verify_password(user.password_hash, body.password):
                c.limiter.failure(key)
                raise HTTPException(status.HTTP_403_FORBIDDEN, INVALID_PASSWORD)
        c.limiter.success(key)

    codes = await c.recovery_codes.issue(ctx.user_id)
    if c.audit is not None:
        await c.audit.write(
            Actor.of(ctx),
            "recovery_codes_reissued",
            target_type="user",
            target_id=ctx.user_id,
        )
    return RecoveryCodesOut(codes=codes)


@router.post(
    "/recover/start",
    status_code=status.HTTP_202_ACCEPTED,
    responses=_LIMITED,
)
async def recover_start(
    body: RecoverStartIn,
    request: Request,
    c: Annotated[Container, Depends(container)],
) -> Response:
    ip = request.client.host if request.client else "unknown"
    ip_key = f"recover:{ip}"
    async with c.limiter.lock_for(ip_key):
        _raise_if_blocked(c, ip_key)
        c.limiter.failure(ip_key)

    wait = c.recover_limiter.hit(body.login)
    if wait is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "too many attempts",
            headers={"Retry-After": str(int(wait) + 1)},
        )

    # Поиск учётки, запрос и отправка — в фоне: ответ одинаков и по времени, есть такой логин
    # или нет.
    c.spawn(_send_recovery_code(c, body.login))
    return Response(status_code=status.HTTP_202_ACCEPTED)


async def _send_recovery_code(c: Container, login: str) -> None:
    """Код в «Избранное» всех аккаунтов учётки, онлайн в Telegram; нет такой активной учётки —
    ничего."""
    user = await c.auth.get_user(login)
    if user is None or not user.active:
        return
    code = await c.recovery_requests.start(user.id)
    msg_text = (
        f"Код восстановления пароля pyrobot: {code}\n"
        "Действует 10 минут. Если код запрашивали не вы — ничего не делайте."
    )
    sends: dict[int, Coroutine[Any, Any, Any]] = {}
    for acc in await c.accounts.list_for_user(user.id):
        if acc.status != "enabled":
            continue
        engine = c.engines.get(acc.id)
        if engine is None:
            continue
        facade = engine if isinstance(engine, EngineFacade) else getattr(engine, "facade", None)
        if not isinstance(facade, EngineFacade):
            continue
        try:
            is_online = facade.tg.status().state is TgState.ONLINE
        except Exception:
            continue
        if is_online:
            sends[acc.id] = facade.send_saved(msg_text)
    results = await asyncio.gather(*sends.values(), return_exceptions=True)
    for account_id, result in zip(sends, results, strict=True):
        if isinstance(result, Exception):
            log.error(
                "recovery code not sent to account %d: %r", account_id, result, exc_info=result
            )


@router.post(
    "/recover/finish",
    response_model=MeOut,
    responses={
        403: error(INVALID_CODE),
        **_LIMITED,
    },
)
async def recover_finish(
    body: RecoverFinishIn,
    request: Request,
    response: Response,
    c: Annotated[Container, Depends(container)],
) -> MeOut:
    has_code = bool(body.code)
    has_recovery_code = bool(body.recovery_code)
    if has_code == has_recovery_code:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "exactly one of code or recovery_code required",
        )

    ip = request.client.host if request.client else "unknown"
    ip_key = f"recover:{ip}"
    async with c.limiter.lock_for(ip_key):
        _raise_if_blocked(c, ip_key)

        if has_recovery_code:
            wait = c.recovery_code_limiter.blocked_for(body.login)
            if wait > 0:
                raise HTTPException(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    "too many attempts",
                    headers={"Retry-After": str(int(wait) + 1)},
                )

        user = await c.auth.get_user(body.login)
        if user is None or not user.active:
            # Та же работа, что у существующей учётки с неверным кодом: время ответа не выдаёт
            # логин.
            if has_code:
                assert body.code is not None
                await c.recovery_requests.miss(body.code)
            else:
                assert body.recovery_code is not None
                await verify_password(await dummy_hash(), body.recovery_code)
            c.limiter.failure(ip_key)
            if has_recovery_code:
                c.recovery_code_limiter.hit(body.login)
            raise HTTPException(status.HTTP_403_FORBIDDEN, INVALID_CODE)

        if has_code:
            assert body.code is not None
            valid = await c.recovery_requests.check(user.id, body.code)
            method = "telegram"
        else:
            assert body.recovery_code is not None
            valid = await c.recovery_codes.use(user.id, body.recovery_code)
            method = "recovery_code"

        if not valid:
            c.limiter.failure(ip_key)
            if has_recovery_code:
                c.recovery_code_limiter.hit(body.login)
            raise HTTPException(status.HTTP_403_FORBIDDEN, INVALID_CODE)

        c.limiter.success(ip_key)

    new_hash = await hash_password(body.password)
    await c.auth.change_password(user.id, new_hash)

    await c.users.touch_login(user.id)
    token, session = await c.auth.create_session(user.id, ip, request.headers.get("user-agent"))
    set_session_cookie(response, token, c.config, int(c.auth.ttl.total_seconds()))

    now_iso = datetime.now(UTC).isoformat()
    notification_text = f"password recovered at {now_iso} via {method}"
    accounts = await c.accounts.list_for_user(user.id)
    for acc in accounts:
        await DbNotifier(c.db, acc.id).notify("warn", "password_recovered", notification_text)

    if c.audit is not None:
        await c.audit.write(
            Actor(user.id, user.login),
            "password_recovered",
            target_type="user",
            target_id=user.id,
            details={"method": method},
        )

    return MeOut(login=user.login, role=cast(Role, user.role), csrf_token=session.csrf_token)

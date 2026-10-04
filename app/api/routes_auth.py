from typing import Annotated, cast

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
from app.api.errors import AUTH, CSRF, CSRF_MISMATCH, Responses, error
from app.api.security import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    dummy_hash,
    hash_password,
    verify_password,
)
from app.db.users import Role

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

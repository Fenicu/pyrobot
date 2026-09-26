from typing import Annotated

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
from app.api.security import dummy_hash, hash_password, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginIn(BaseModel):
    login: str
    password: str


class MeOut(BaseModel):
    login: str
    csrf_token: str


class PasswordIn(BaseModel):
    current: str
    new: str = Field(min_length=12)


@router.post("/login", response_model=MeOut)
async def login(
    body: LoginIn,
    request: Request,
    response: Response,
    c: Annotated[Container, Depends(container)],
) -> MeOut:
    key = request.client.host if request.client else "unknown"
    async with c.limiter.lock_for(key):
        wait = c.limiter.blocked_for(key)
        if wait > 0:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "too many attempts",
                headers={"Retry-After": str(int(wait) + 1)},
            )
        admin = await c.auth.get_admin(body.login)
        async with c.limiter.slots:
            valid = await verify_password(
                admin.password_hash if admin else await dummy_hash(), body.password
            )
        if admin is None or not valid:
            c.limiter.failure(key)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
        c.limiter.success(key)
    token, session = await c.auth.create_session(admin.id, key, request.headers.get("user-agent"))
    set_session_cookie(response, token, c.config, int(c.auth.ttl.total_seconds()))
    return MeOut(login=admin.login, csrf_token=session.csrf_token)


@router.get("/me", response_model=MeOut)
async def me(ctx: Annotated[SessionContext, Depends(current_session)]) -> MeOut:
    return MeOut(login=ctx.login, csrf_token=ctx.csrf_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    c: Annotated[Container, Depends(container)],
) -> None:
    await c.auth.revoke(ctx.session_id)
    response.delete_cookie(COOKIE, path="/")


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordIn,
    response: Response,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    c: Annotated[Container, Depends(container)],
) -> None:
    admin = await c.auth.get_admin(ctx.login)
    async with c.limiter.slots:
        if admin is None or not await verify_password(admin.password_hash, body.current):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid current password")
        new_hash = await hash_password(body.new)
    await c.auth.change_password(admin.id, new_hash)
    response.delete_cookie(COOKIE, path="/")

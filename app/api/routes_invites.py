from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.api.container import Container
from app.api.deps import container, set_session_cookie
from app.api.errors import (
    INVITE_GONE,
    INVITE_NOT_FOUND,
    LOGIN_TAKEN,
    Responses,
    error,
)
from app.api.security import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    hash_password,
)
from app.db.invites import InviteGone, InviteNotFound
from app.db.recovery import format_code, new_secret
from app.db.users import LoginTaken, Role

router = APIRouter(prefix="/api/v1/invites", tags=["invites"])


class InvitePeekOut(BaseModel):
    expires_at: datetime


class InviteAcceptIn(BaseModel):
    login: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]{3,64}$")
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)


class InviteAcceptOut(BaseModel):
    login: str
    role: Role
    csrf_token: str
    recovery_codes: list[str]


def _raise_if_blocked(c: Container, key: str) -> None:
    wait = c.limiter.blocked_for(key)
    if wait > 0:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "too many attempts",
            headers={"Retry-After": str(int(wait) + 1)},
        )


_LIMITED: Responses = {429: error("too many attempts")}


def _accept_error(exc: InviteNotFound | InviteGone | LoginTaken) -> HTTPException:
    if isinstance(exc, InviteNotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, INVITE_NOT_FOUND)
    if isinstance(exc, InviteGone):
        return HTTPException(status.HTTP_410_GONE, INVITE_GONE)
    return HTTPException(status.HTTP_409_CONFLICT, LOGIN_TAKEN)


@router.get(
    "/{token}",
    response_model=InvitePeekOut,
    responses={
        404: error(INVITE_NOT_FOUND),
        410: error(INVITE_GONE),
    },
)
async def peek_invite(
    token: str,
    c: Annotated[Container, Depends(container)],
) -> InvitePeekOut:
    try:
        info = await c.invites.peek(token)
        return InvitePeekOut(expires_at=info.expires_at)
    except InviteNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, INVITE_NOT_FOUND) from exc
    except InviteGone as exc:
        raise HTTPException(status.HTTP_410_GONE, INVITE_GONE) from exc


@router.post(
    "/{token}/accept",
    status_code=status.HTTP_201_CREATED,
    response_model=InviteAcceptOut,
    responses={
        404: error(INVITE_NOT_FOUND),
        409: error(LOGIN_TAKEN),
        410: error(INVITE_GONE),
        **_LIMITED,
    },
)
async def accept_invite(
    token: str,
    body: InviteAcceptIn,
    request: Request,
    response: Response,
    c: Annotated[Container, Depends(container)],
) -> InviteAcceptOut:
    key = request.client.host if request.client else "unknown"
    async with c.limiter.lock_for(key):
        _raise_if_blocked(c, key)
        try:
            # Дешёвая проверка до 11 хэшей argon2: мёртвый токен не занимает limiter.slots.
            await c.invites.peek(token)
        except (InviteNotFound, InviteGone) as exc:
            c.limiter.failure(key)
            raise _accept_error(exc) from exc

        secrets_list = [new_secret() for _ in range(10)]
        async with c.limiter.slots:
            code_hashes = [await hash_password(s) for s in secrets_list]
            password_hash = await hash_password(body.password)

        try:
            res = await c.invites.accept(token, body.login, password_hash, code_hashes)
        except (InviteNotFound, InviteGone, LoginTaken) as exc:
            c.limiter.failure(key)
            raise _accept_error(exc) from exc

        c.limiter.success(key)

    codes = [format_code(row_id, s) for row_id, s in zip(res.code_ids, secrets_list, strict=True)]

    session_token, auth_session = await c.auth.create_session(
        res.user.id, key, request.headers.get("user-agent")
    )
    set_session_cookie(response, session_token, c.config, int(c.auth.ttl.total_seconds()))

    if c.server_notifier is not None:
        await c.server_notifier.notify(
            "info", "invite_used", f"invite {res.invite_id} used by {body.login}"
        )

    return InviteAcceptOut(
        login=res.user.login,
        role=res.user.role,
        csrf_token=auth_session.csrf_token,
        recovery_codes=codes,
    )

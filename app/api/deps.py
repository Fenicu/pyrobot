import secrets
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, Response, status

from app.api.container import Container
from app.config import AppConfig

COOKIE = "pyrobot_session"


@dataclass(frozen=True)
class SessionContext:
    session_id: int
    admin_id: int
    login: str
    csrf_token: str


def container(request: Request) -> Container:
    return request.app.state.container  # type: ignore[no-any-return]


def set_session_cookie(response: Response, token: str, config: AppConfig, max_age: int) -> None:
    response.set_cookie(
        COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        secure=config.cookie_secure,
        samesite="strict",
        path="/",
    )


async def current_session(
    request: Request, response: Response, c: Annotated[Container, Depends(container)]
) -> SessionContext:
    token = request.cookies.get(COOKIE)
    resolved = await c.auth.resolve(token) if token else None
    if token is None or resolved is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")
    if resolved.slid:
        set_session_cookie(response, token, c.config, int(c.auth.ttl.total_seconds()))
    row, admin = resolved.session, resolved.admin
    return SessionContext(row.id, admin.id, admin.login, row.csrf_token)


async def require_csrf(
    ctx: Annotated[SessionContext, Depends(current_session)],
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> SessionContext:
    if not secrets.compare_digest(x_csrf_token or "", ctx.csrf_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "csrf token mismatch")
    return ctx

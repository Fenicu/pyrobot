import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.engine.tg_auth import TgAuthBackend, TgAuthManager
from app.engine.types import Button, IncomingMessage

GAME = 227859379
# Пользователь Telegram фейковых бэкендов входа.
TG_USER = 267519921


def now() -> datetime:
    return datetime.now(UTC)


def make_msg(
    text: str | None,
    *,
    chat_id: int = GAME,
    msg_id: int = 1,
    kind: str = "new",
    revision: int = 0,
    date: datetime | None = None,
    received_at: datetime | None = None,
    buttons: tuple[Button, ...] = (),
    outgoing: bool = False,
    recovered: bool = False,
) -> IncomingMessage:
    moment = date or now()
    return IncomingMessage(
        chat_id=chat_id,
        msg_id=msg_id,
        revision=revision,
        kind=kind,  # type: ignore[arg-type]
        date=moment,
        received_at=received_at or now(),
        text=text,
        inline=buttons,
        outgoing=outgoing,
        recovered=recovered,
    )


async def until(pred: Callable[[], bool], timeout: float = 1.0) -> None:  # noqa: ASYNC109
    deadline = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.005)


def tg_auth(
    backend: TgAuthBackend, expected_user_id: int | None = TG_USER, **kw: Any
) -> TgAuthManager:
    """Вход в Telegram аккаунта 1: привязка — только в памяти, своего чата в настройках нет."""

    async def bind(user_id: int) -> int:
        return user_id

    kw.setdefault("bind", bind)
    kw.setdefault("self_chat", lambda user_id: [])
    kw.setdefault("account_id", 1)
    return TgAuthManager(backend, expected_user_id=expected_user_id, **kw)

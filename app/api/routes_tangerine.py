"""Чат мандаринов @mandarinkaSW: своё сообщение в нём и пара своих аккаунтов, которые дарят 🍊
друг другу. Вне шлюза команд и в любом режиме, как вступление в общий чат игры: это явное
действие владельца."""

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StringConstraints

from app.api.container import Container
from app.api.deps import SessionContext, container, require_csrf
from app.api.errors import (
    ACCOUNT_DELETING,
    ACCOUNT_NOT_FOUND,
    CHAT_IS_SELF,
    CSRF,
    ENGINE,
    ENGINE_NOT_RUNNING,
    FLOOD_WAIT,
    TANGERINE_CHAT_MISMATCH,
    TANGERINE_PAIR_PARTIAL,
    TANGERINE_PAIR_SELF,
    TG_NOT_ONLINE,
    AccountErrorOut,
    ErrorOut,
    TangerinePairPartialOut,
    error,
)
from app.api.scope import AccountScope, account_router, account_scope, running
from app.engine.facade import EngineFacade, TangerinePost, TgNotOnline
from app.engine.fence import LeaseLost
from app.engine.settings import ChatIsSelf, SettingsConflict
from app.engine.tg_auth import TgState
from app.engine.transport.base import FloodWait, TransportAuthLost, TransportRejected

log = logging.getLogger(__name__)
router = account_router("tangerine")

TangerineText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
_TELEGRAM = error(
    "<код ошибки Telegram>", "join_request_sent", "join_declined", "message_id_unknown"
)


class TangerinePostIn(BaseModel):
    text: TangerineText = "🍊"


class TangerinePostOut(BaseModel):
    chat_id: int
    message_id: int


class TangerinePairIn(BaseModel):
    # Другой аккаунт той же учётки.
    partner_id: int = Field(ge=1, le=2**31 - 1)
    text: TangerineText = "🍊"


class PairedAccountOut(BaseModel):
    id: int
    message_id: int


class TangerinePairOut(BaseModel):
    account: PairedAccountOut
    partner: PairedAccountOut


class _PostFailed(Exception):
    def __init__(self, code: int, detail: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.headers = headers


async def _post(f: EngineFacade, text: str) -> TangerinePost:
    """Сообщение аккаунта в чате мандаринов; ошибка — `_PostFailed` с ответом API."""
    try:
        return await f.tangerine_post(text)
    except (TgNotOnline, TransportAuthLost) as exc:
        raise _PostFailed(status.HTTP_409_CONFLICT, TG_NOT_ONLINE) from exc
    except FloodWait as exc:
        retry_after = str(max(1, int(exc.seconds) + 1))
        raise _PostFailed(
            status.HTTP_429_TOO_MANY_REQUESTS, FLOOD_WAIT, {"Retry-After": retry_after}
        ) from exc
    except TransportRejected as exc:
        if str(exc) == "chat_mismatch":
            raise _PostFailed(status.HTTP_409_CONFLICT, TANGERINE_CHAT_MISMATCH) from exc
        raise _PostFailed(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    except LeaseLost as exc:
        raise _PostFailed(status.HTTP_503_SERVICE_UNAVAILABLE, ENGINE_NOT_RUNNING) from exc


async def _post_or_raise(f: EngineFacade, text: str) -> TangerinePost:
    try:
        return await _post(f, text)
    except _PostFailed as exc:
        raise HTTPException(exc.code, exc.detail, headers=exc.headers) from exc


@router.post(
    "/tangerine/post",
    response_model=TangerinePostOut,
    responses={
        **CSRF,
        **ENGINE,
        409: error(ACCOUNT_DELETING, TG_NOT_ONLINE, TANGERINE_CHAT_MISMATCH),
        429: error(FLOOD_WAIT),
        502: _TELEGRAM,
    },
)
async def tangerine_post(
    body: TangerinePostIn,
    _: Annotated[SessionContext, Depends(require_csrf)],
    scope: Annotated[AccountScope, Depends(account_scope)],
) -> TangerinePostOut:
    """Сообщение аккаунта в чате мандаринов @mandarinkaSW: перед ним аккаунт вступает в чат, id
    чата по username сверяется с `chats.tangerine_chat_id`. Текст обрезается по краям, 1–200
    символов."""
    if scope.account.status == "deleting":
        raise HTTPException(status.HTTP_409_CONFLICT, ACCOUNT_DELETING)
    f = await running(scope)
    post = await _post_or_raise(f, body.text)
    return TangerinePostOut(chat_id=post.chat_id, message_id=post.message_id)


@router.post(
    "/tangerine/pair",
    response_model=TangerinePairOut,
    responses={
        **CSRF,
        404: error(ACCOUNT_NOT_FOUND),
        409: {
            "model": AccountErrorOut | ErrorOut | TangerinePairPartialOut,
            "description": f"{TG_NOT_ONLINE} with `account_id` (before any message) | "
            f"{ACCOUNT_DELETING} | {TANGERINE_CHAT_MISMATCH} | {TANGERINE_PAIR_PARTIAL}",
        },
        422: {
            "model": ErrorOut | TangerinePairPartialOut,
            "description": f"invalid body | {TANGERINE_PAIR_SELF} | {TANGERINE_PAIR_PARTIAL} "
            f"({CHAT_IS_SELF} on the settings write)",
        },
        429: {"model": ErrorOut | TangerinePairPartialOut, "description": FLOOD_WAIT},
        502: {
            "model": ErrorOut | TangerinePairPartialOut,
            "description": f"<код ошибки Telegram> | {TANGERINE_PAIR_PARTIAL}",
        },
        500: {
            "model": TangerinePairPartialOut,
            "description": f"{TANGERINE_PAIR_PARTIAL} (settings_write_failed)",
        },
        503: {
            "model": ErrorOut | TangerinePairPartialOut,
            "description": f"{ENGINE_NOT_RUNNING} | {TANGERINE_PAIR_PARTIAL}",
        },
    },
)
async def tangerine_pair(
    body: TangerinePairIn,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    scope: Annotated[AccountScope, Depends(account_scope)],
    c: Annotated[Container, Depends(container)],
) -> TangerinePairOut | JSONResponse:
    """Пара своих аккаунтов дарит 🍊 друг другу: оба пишут в чат мандаринов (сначала этот, потом
    партнёр), и `chats.tangerine_reply_to` каждого указывает на сообщение другого — той же
    правкой, что PATCH настроек. Партнёр — другой аккаунт той же учётки (чужой — 404), оба
    движка запущены и в Telegram онлайн (иначе 409 `tg_not_online` с `account_id`, ничего не
    отправлено). Сообщение партнёра не ушло — настройки не меняются, ответ
    `tangerine_pair_partial` с кодом ошибки партнёра: сообщение этого аккаунта остаётся в чате.
    Не записалась настройка после обоих сообщений — тот же ответ с обоими id в `posted` и уже
    записанными аккаунтами в `written`, без отката."""
    me = scope.account
    if me.status == "deleting":
        raise HTTPException(status.HTTP_409_CONFLICT, ACCOUNT_DELETING)
    if body.partner_id == me.id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, TANGERINE_PAIR_SELF)
    partner = await c.accounts.get(body.partner_id)
    if partner is None or partner.owner_id != ctx.user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, ACCOUNT_NOT_FOUND)
    if partner.status == "deleting":
        raise HTTPException(status.HTTP_409_CONFLICT, ACCOUNT_DELETING)
    engine = c.engines.get(partner.id)
    pair = ((me.id, scope.facade), (partner.id, engine.facade if engine is not None else None))
    ready: list[EngineFacade] = []
    for account_id, f in pair:
        if f is None or f.tg.status().state is not TgState.ONLINE:
            return JSONResponse(
                {"detail": TG_NOT_ONLINE, "account_id": account_id},
                status_code=status.HTTP_409_CONFLICT,
            )
        ready.append(f)
    mine, theirs = ready
    my_post = await _post_or_raise(mine, body.text)
    try:
        their_post = await _post(theirs, body.text)
    except _PostFailed as exc:
        return _partial({me.id: my_post}, partner.id, exc.code, exc.detail, exc.headers)
    except Exception:
        log.exception("tangerine pair: partner %s post failed", partner.id)
        return _partial({me.id: my_post}, partner.id, status.HTTP_502_BAD_GATEWAY, "post_failed")
    posted = {me.id: my_post, partner.id: their_post}
    written: list[int] = []
    for account_id, f, reply_to in (
        (me.id, mine, their_post.message_id),
        (partner.id, theirs, my_post.message_id),
    ):
        try:
            await f.set_tangerine_reply_to(reply_to, by=ctx.login)
        except Exception as exc:
            code, reason = _write_failure(exc)
            if code == status.HTTP_500_INTERNAL_SERVER_ERROR:
                log.exception("tangerine pair: account %s settings write failed", account_id)
            return _partial(posted, account_id, code, reason, written=written)
        written.append(account_id)
    return TangerinePairOut(
        account=PairedAccountOut(id=me.id, message_id=my_post.message_id),
        partner=PairedAccountOut(id=partner.id, message_id=their_post.message_id),
    )


def _write_failure(exc: Exception) -> tuple[int, str]:
    """Ответ на сорванную запись адресата после обоих сообщений."""
    if isinstance(exc, LeaseLost):
        return status.HTTP_503_SERVICE_UNAVAILABLE, ENGINE_NOT_RUNNING
    if isinstance(exc, SettingsConflict):
        return status.HTTP_409_CONFLICT, "version_conflict"
    if isinstance(exc, ChatIsSelf):
        return status.HTTP_422_UNPROCESSABLE_CONTENT, CHAT_IS_SELF
    return status.HTTP_500_INTERNAL_SERVER_ERROR, "settings_write_failed"


def _partial(
    posted: dict[int, TangerinePost],
    failed: int,
    code: int,
    reason: str,
    headers: dict[str, str] | None = None,
    *,
    written: list[int] | None = None,
) -> JSONResponse:
    out = TangerinePairPartialOut(
        detail=TANGERINE_PAIR_PARTIAL,
        reason=reason,
        posted={account_id: post.message_id for account_id, post in posted.items()},
        failed=failed,
        written=written or [],
    )
    return JSONResponse(out.model_dump(mode="json"), status_code=code, headers=headers)

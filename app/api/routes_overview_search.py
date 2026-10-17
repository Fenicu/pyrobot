"""Обзор аккаунтов учётки: поиск по сообщениям игры - в нашей базе или в Telegram."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.api.container import Container
from app.api.deps import SessionContext, container, current_session
from app.api.errors import AUTH, ValidationErrorOut
from app.api.routes_engine import IsoDatetime
from app.api.routes_overview import _feed_accounts
from app.db.overview import SearchHit, SearchTarget
from app.engine.facade import TgNotOnline, TgUnreachable
from app.engine.fence import LeaseLost
from app.engine.transport.base import ChatUnavailable, FloodWait, TransportAuthLost

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/overview", tags=["overview"])

Where = Literal["db", "telegram"]
Status = Literal["ok", "unavailable", "error"]

MIN_QUERY, MAX_QUERY = 2, 100
# Поиск по Telegram: одновременно не больше стольких аккаунтов, на каждый - не дольше.
SEARCH_CONCURRENCY = 3
SEARCH_TIMEOUT_S = 15.0


class SearchAccountOut(BaseModel):
    account_id: int
    name: str
    status: Status
    # Код причины для `unavailable` и `error`: `engine_not_running`, `locked_elsewhere`,
    # `tg_offline`, `tg_unreachable`; `timeout`, `flood_wait`, `chat_unavailable`,
    # `internal_error`.
    reason: str | None
    # Найденных сообщений этого аккаунта в ответе.
    count: int


class SearchHitOut(BaseModel):
    account_id: int
    chat_id: int
    msg_id: int
    date: IsoDatetime
    outgoing: bool
    text: str | None
    # Как в журнале сообщений: {"inline": [...]} или {"reply": [...]} либо null.
    markup: dict[str, Any] | None


class SearchOut(BaseModel):
    q: str
    where: Where
    accounts: list[SearchAccountOut]
    hits: list[SearchHitOut]


Outcome = tuple[Status, str | None, list[SearchHit]]


def _hit_out(h: SearchHit) -> SearchHitOut:
    return SearchHitOut(
        account_id=h.account_id,
        chat_id=h.chat_id,
        msg_id=h.msg_id,
        date=h.date,
        outgoing=h.outgoing,
        text=h.text,
        markup=h.markup,
    )


async def _telegram(c: Container, target: SearchTarget, q: str, limit: int) -> Outcome:
    account_id = target.info.id
    runtime = c.engines.get(account_id)
    facade = runtime.facade if runtime is not None else None
    if facade is None:
        elsewhere = c.engines.host_reason(account_id) == "locked_elsewhere"
        return "unavailable", "locked_elsewhere" if elsewhere else "engine_not_running", []
    try:
        async with asyncio.timeout(SEARCH_TIMEOUT_S):
            found = await facade.search_game_chat(q, limit)
    except TimeoutError:
        return "error", "timeout", []
    except (TgNotOnline, TransportAuthLost):
        return "unavailable", "tg_offline", []
    except TgUnreachable:
        return "unavailable", "tg_unreachable", []
    except LeaseLost:
        return "unavailable", "locked_elsewhere", []
    except FloodWait:
        return "error", "flood_wait", []
    except ChatUnavailable:
        return "error", "chat_unavailable", []
    except OSError:
        return "unavailable", "tg_unreachable", []
    except Exception:
        log.exception("telegram search failed for account %s", account_id)
        return "error", "internal_error", []
    hits = [
        SearchHit(
            account_id=account_id,
            chat_id=m.chat_id,
            msg_id=m.msg_id,
            date=m.date,
            outgoing=m.outgoing,
            text=m.text,
            markup=m.markup_json(),
        )
        for m in found[:limit]
    ]
    return "ok", None, hits


async def _telegram_all(
    c: Container, targets: Sequence[SearchTarget], q: str, limit: int
) -> list[Outcome]:
    slots = asyncio.Semaphore(SEARCH_CONCURRENCY)

    async def one(target: SearchTarget) -> Outcome:
        async with slots:
            return await _telegram(c, target, q, limit)

    return list(await asyncio.gather(*(one(t) for t in targets)))


@router.get(
    "/search",
    response_model=SearchOut,
    responses={**AUTH, 422: {"model": ValidationErrorOut, "description": "invalid parameters"}},
)
async def overview_search(
    ctx: Annotated[SessionContext, Depends(current_session)],
    c: Annotated[Container, Depends(container)],
    q: Annotated[str, Query(description=f"подстрока, {MIN_QUERY}..{MAX_QUERY} символов")],
    where: Annotated[Where, Query(description="db - наша база, telegram - поиск Telegram")] = "db",
    account_ids: Annotated[str | None, Query(description="id аккаунтов через запятую")] = None,
    limit: Annotated[int, Query(ge=1, le=200, description="на аккаунт")] = 50,
) -> SearchOut:
    """Поиск по сообщениям чата игры аккаунтов учётки. `where=db` - журнал сообщений (хранится
    около 90 суток), без учёта регистра; `where=telegram` - поиск Telegram в чате игры каждого
    аккаунта через его запущенный здесь движок: чтение, в игру ничего не уходит. Чужие,
    удаляемые и несуществующие id молча отбрасываются. Недоступный или упавший аккаунт не
    ломает ответ: его статус и причина - в `accounts`."""
    query = q.strip()
    if not MIN_QUERY <= len(query) <= MAX_QUERY:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid q")
    ids = _feed_accounts(account_ids)
    targets = await c.overview.search_targets(ctx.user_id, ids)
    log.info(
        "overview search user=%s where=%s q_len=%d accounts=%s",
        ctx.login,
        where,
        len(query),
        [t.info.id for t in targets],
    )
    outcomes: list[Outcome]
    if where == "db":
        hits = await c.overview.search_messages(targets, query, limit)
        outcomes = [("ok", None, [h for h in hits if h.account_id == t.info.id]) for t in targets]
    else:
        outcomes = await _telegram_all(c, targets, query, limit)
    all_hits = sorted(
        (h for _, _, found in outcomes for h in found),
        key=lambda h: (h.date, h.account_id, h.msg_id),
        reverse=True,
    )
    return SearchOut(
        q=query,
        where=where,
        accounts=[
            SearchAccountOut(
                account_id=t.info.id,
                name=t.info.name,
                status=state,
                reason=reason,
                count=len(found),
            )
            for t, (state, reason, found) in zip(targets, outcomes, strict=True)
        ],
        hits=[_hit_out(h) for h in all_hits],
    )

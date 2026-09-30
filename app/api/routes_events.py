import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.api.container import Container
from app.api.deps import COOKIE, container
from app.api.errors import AUTH, ENGINE_NOT_RUNNING, error
from app.api.scope import AccountScope, account_router, account_scope
from app.engine.stream import EventStream, Subscription

router = account_router("events")


def _frame(event_id: str, kind: str, data: dict[str, Any]) -> str:
    body = json.dumps(data, ensure_ascii=False, default=str)
    return f"id: {event_id}\nevent: {kind}\ndata: {body}\n\n"


async def _events(
    stream: EventStream,
    sub: Subscription,
    alive: Callable[[], Awaitable[bool]],
    heartbeat_s: float,
    clock: Callable[[], float] = time.monotonic,
) -> AsyncIterator[str]:
    # Сессия перепроверяется по часам раз в heartbeat_s, а не только в тишине: иначе поток,
    # где события идут чаще пинга, продолжал бы отдавать данные отозванной сессии.
    check_at = clock() + heartbeat_s

    async def session_ok() -> bool:
        nonlocal check_at
        if clock() < check_at:
            return True
        check_at = clock() + heartbeat_s
        return await alive()

    try:
        if sub.reset is not None:
            yield _frame(stream.event_id(sub.at), "reset", {"reason": sub.reset})
        for event in sub.replay:
            if not await session_ok():
                return
            yield _frame(stream.event_id(event.seq), event.type, event.data)
        # История отдана: подписка не должна держать старые события, пока живёт соединение.
        sub.replay.clear()
        while True:
            # Отстающий отключён: дочитывает уже накопленное и закрывает поток, клиент
            # переподключится с Last-Event-ID и получит пропущенное из истории.
            if sub.dropped and sub.queue.empty():
                return
            if not await session_ok():
                return
            try:
                event = await asyncio.wait_for(sub.queue.get(), max(check_at - clock(), 0.001))
            except TimeoutError:
                if not await session_ok():
                    return
                yield ": ping\n\n"
                continue
            except asyncio.QueueShutDown:
                # Движок остановлен (`EventStream.close`): клиент переподключится к новому
                # движку аккаунта или получит 503.
                return
            yield _frame(stream.event_id(event.seq), event.type, event.data)
    finally:
        stream.unsubscribe(sub)


@router.get(
    "/events",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"text/event-stream": {}}, "description": "SSE stream"},
        **AUTH,
        503: error(ENGINE_NOT_RUNNING),
    },
)
async def events(
    request: Request,
    scope: Annotated[AccountScope, Depends(account_scope)],
    c: Annotated[Container, Depends(container)],
    last_event_id: Annotated[str | None, Header(max_length=64)] = None,
) -> StreamingResponse:
    """Поток событий движка аккаунта, зарегистрированного при подключении; движок
    остановится — поток закончится."""
    if scope.engine is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, ENGINE_NOT_RUNNING)
    stream = scope.engine.stream
    token = request.cookies.get(COOKIE)

    async def alive() -> bool:
        return token is not None and await c.auth.resolve(token, slide=False) is not None

    sub = stream.subscribe(last_event_id)
    return StreamingResponse(
        _events(stream, sub, alive, c.sse_heartbeat_s),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

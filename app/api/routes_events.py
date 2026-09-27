import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.api.container import Container
from app.api.deps import COOKIE, SessionContext, container, current_session
from app.api.routes_engine import facade
from app.engine.facade import EngineFacade
from app.engine.stream import EventStream, Subscription

router = APIRouter(prefix="/api/v1", tags=["events"])


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
            yield _frame(stream.event_id(event.seq), event.type, event.data)
    finally:
        stream.unsubscribe(sub)


@router.get(
    "/events",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "SSE stream"}},
)
async def events(
    request: Request,
    _: Annotated[SessionContext, Depends(current_session)],
    c: Annotated[Container, Depends(container)],
    f: Annotated[EngineFacade, Depends(facade)],
    last_event_id: Annotated[str | None, Header(max_length=64)] = None,
) -> StreamingResponse:
    stream = f.stream
    if stream is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "event stream not started")
    token = request.cookies.get(COOKIE)

    async def alive() -> bool:
        return token is not None and await c.auth.resolve(token) is not None

    sub = stream.subscribe(last_event_id)
    return StreamingResponse(
        _events(stream, sub, alive, c.sse_heartbeat_s),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

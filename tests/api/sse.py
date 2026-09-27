"""Чтение SSE прямо через ASGI: httpx.ASGITransport копит тело ответа целиком, а поток
бесконечный — здесь кадры разбираются по мере отправки, а после нужного числа событий
приложению приходит http.disconnect."""

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from starlette.types import ASGIApp, Message


@dataclass(frozen=True)
class SseEvent:
    id: str | None
    event: str | None
    data: Any
    comment: bool = False


def _parse(frame: str) -> SseEvent | None:
    fields: dict[str, str] = {}
    comment = False
    for line in frame.splitlines():
        if line.startswith(":"):
            comment = True
            continue
        name, _, value = line.partition(": ")
        fields[name] = value
    if comment and not fields:
        return SseEvent(None, None, None, comment=True)
    if not fields:
        return None
    data = json.loads(fields["data"]) if "data" in fields else None
    return SseEvent(fields.get("id"), fields.get("event"), data)


async def read_sse(
    app: ASGIApp,
    path: str,
    *,
    headers: dict[str, str],
    count: int,
    timeout: float = 3.0,  # noqa: ASYNC109
    keep_comments: bool = False,
) -> tuple[int, list[SseEvent]]:
    done = asyncio.Event()
    status = 0
    buffer = ""
    events: list[SseEvent] = []
    sent_request = False

    async def receive() -> Message:
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await done.wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        nonlocal status, buffer
        if message["type"] == "http.response.start":
            status = message["status"]
        elif message["type"] == "http.response.body":
            buffer += message.get("body", b"").decode()
            while "\n\n" in buffer:
                frame, buffer = buffer.split("\n\n", 1)
                parsed = _parse(frame)
                if parsed is not None and (keep_comments or not parsed.comment):
                    events.append(parsed)
            if len(events) >= count or not message.get("more_body", False):
                done.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }
    task = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(done.wait(), timeout)
    finally:
        done.set()
        await asyncio.wait_for(task, timeout)
    return status, events

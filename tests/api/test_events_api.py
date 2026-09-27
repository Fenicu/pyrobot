import asyncio

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.api.app import create_api
from app.api.container import Container
from app.api.routes_events import _events
from app.engine.stream import EventStream
from tests.api.conftest import login
from tests.api.sse import read_sse
from tests.engine.helpers import until
from tests.engine.test_facade import build

pytestmark = pytest.mark.db


@pytest.fixture
async def app_with_stream(container: Container) -> tuple[FastAPI, EventStream]:
    stream = EventStream(epoch="e1", history=3)
    facade = build()
    facade.stream = stream
    container.facade = facade
    container.sse_heartbeat_s = 0.05
    return create_api(container), stream


def _cookie(client: AsyncClient) -> dict[str, str]:
    return {"cookie": f"pyrobot_session={client.cookies['pyrobot_session']}"}


async def test_events_need_session(
    app_with_stream: tuple[FastAPI, EventStream], api_client: AsyncClient
) -> None:
    app, _ = app_with_stream
    status, _ = await read_sse(app, "/api/v1/events", headers={}, count=1)
    assert status == 401


async def test_new_connection_gets_reset_then_live_events(
    app_with_stream: tuple[FastAPI, EventStream], api_client: AsyncClient
) -> None:
    app, stream = app_with_stream
    await login(api_client)
    stream.publish("notification", {"code": "old"})

    async def later() -> None:
        await until(lambda: stream.subscribers == 1, 2.0)
        stream.publish("action", {"id": 1, "status": "sent"})

    task = asyncio.create_task(later())
    status, events = await read_sse(app, "/api/v1/events", headers=_cookie(api_client), count=2)
    await task
    assert status == 200
    assert [(e.id, e.event, e.data) for e in events] == [
        ("e1:1", "reset", {"reason": "new"}),
        ("e1:2", "action", {"id": 1, "status": "sent"}),
    ]
    assert stream.subscribers == 0


async def test_resume_and_reset_by_last_event_id(
    app_with_stream: tuple[FastAPI, EventStream], api_client: AsyncClient
) -> None:
    app, stream = app_with_stream
    await login(api_client)
    for code in "abcd":
        stream.publish("notification", {"code": code})
    headers = {**_cookie(api_client), "last-event-id": "e1:2"}
    _, events = await read_sse(app, "/api/v1/events", headers=headers, count=2)
    assert [(e.id, e.data["code"]) for e in events] == [("e1:3", "c"), ("e1:4", "d")]
    for last, reason in (("e1:0", "evicted"), ("e0:4", "epoch"), ("junk", "unknown")):
        headers = {**_cookie(api_client), "last-event-id": last}
        _, events = await read_sse(app, "/api/v1/events", headers=headers, count=1)
        assert (events[0].id, events[0].event, events[0].data) == (
            "e1:4",
            "reset",
            {"reason": reason},
        )


async def test_revoked_session_closes_stream(
    app_with_stream: tuple[FastAPI, EventStream], api_client: AsyncClient
) -> None:
    app, stream = app_with_stream
    csrf = await login(api_client)
    headers = _cookie(api_client)

    async def logout() -> None:
        await asyncio.sleep(0.12)
        await api_client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})

    task = asyncio.create_task(logout())
    status, events = await read_sse(
        app, "/api/v1/events", headers=headers, count=100, keep_comments=True
    )
    await task
    assert status == 200
    assert events[0].event == "reset" and any(e.comment for e in events)
    assert stream.subscribers == 0


async def test_lagging_client_is_cut_off(container: Container, api_client: AsyncClient) -> None:
    stream = EventStream(epoch="e1", queue_size=2)
    facade = build()
    facade.stream = stream
    container.facade = facade
    await login(api_client)

    async def burst() -> None:
        await until(lambda: stream.subscribers == 1, 2.0)
        for i in range(5):
            stream.publish("notification", {"n": i})

    task = asyncio.create_task(burst())
    status, events = await read_sse(
        create_api(container), "/api/v1/events", headers=_cookie(api_client), count=100
    )
    await task
    # Публикация не ждёт: переполненный подписчик снят, поток отдал накопленное и закрылся.
    assert status == 200
    assert [(e.event, e.data) for e in events] == [
        ("reset", {"reason": "new"}),
        ("notification", {"n": 0}),
        ("notification", {"n": 1}),
    ]
    assert stream.subscribers == 0 and len(stream.history()) == 5


async def test_busy_stream_of_revoked_session_is_closed(
    app_with_stream: tuple[FastAPI, EventStream], api_client: AsyncClient
) -> None:
    app, stream = app_with_stream
    csrf = await login(api_client)
    headers = _cookie(api_client)
    stop = asyncio.Event()

    async def flood() -> None:
        # События чаще пинга: сессия всё равно перепроверяется по часам.
        await until(lambda: stream.subscribers == 1, 2.0)
        n = 0
        while not stop.is_set():
            stream.publish("notification", {"n": n})
            n += 1
            await asyncio.sleep(0.005)

    async def logout() -> None:
        await until(lambda: stream.subscribers == 1, 2.0)
        await asyncio.sleep(0.1)
        await api_client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})

    tasks = [asyncio.create_task(flood()), asyncio.create_task(logout())]
    status, events = await read_sse(app, "/api/v1/events", headers=headers, count=100_000)
    stop.set()
    await asyncio.gather(*tasks)
    assert status == 200 and len(events) > 3
    assert stream.subscribers == 0


async def test_replay_checks_session_and_is_released() -> None:
    stream = EventStream(epoch="e1")
    for i in range(5):
        stream.publish("notification", {"n": i})
    now = [0.0]
    sub = stream.subscribe("e1:0")
    frames: list[str] = []

    async def revoked() -> bool:
        return False

    async for frame in _events(stream, sub, revoked, 1.0, clock=lambda: now[0]):
        frames.append(frame)
        now[0] += 0.4
    # Отозванная сессия замечена посреди истории: после проверки (≥1 с) отдачи нет.
    assert len(frames) == 3 and stream.subscribers == 0

    ok = stream.subscribe("e1:0")

    async def alive() -> bool:
        return True

    gen = _events(stream, ok, alive, 60.0)
    got = [await anext(gen) for _ in range(5)]
    stream.publish("notification", {"n": 5})
    got.append(await anext(gen))
    await gen.aclose()
    assert len(got) == 6 and ok.replay == []

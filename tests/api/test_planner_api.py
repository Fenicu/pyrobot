import asyncio
from collections.abc import AsyncIterator
from datetime import datetime

import pytest
from httpx import AsyncClient

from app.api.container import Container
from app.engine.facade import OUTLOOK_TTL_S, EngineFacade
from app.engine.planner.decide import Outlook
from app.engine.planner.loop import PlannerLoop
from tests.api.conftest import login
from tests.engine.planner.test_decide import awake
from tests.engine.planner.test_loop_outlook import CountingStore, rig
from tests.engine.test_facade import build

pytestmark = pytest.mark.db
URL = "/api/v1/planner/outlook"


class Tick:
    def __init__(self) -> None:
        self.at = 100.0

    def __call__(self) -> float:
        return self.at


class Planned:
    def __init__(self) -> None:
        # Цикл без своих решений: задача идёт, но ничего не исполняет и не пишет в журнал.
        self.loop, self.store, _ = rig(awake(), auto=False)
        self.ready: str | None = None
        self.loop._ready = lambda: self.ready
        self.tick = Tick()
        self.passes = 0
        original = self.loop.plan

        async def counted() -> tuple[datetime, Outlook]:
            self.passes += 1
            return await original()

        self.loop.plan = counted  # type: ignore[method-assign]
        self.facade: EngineFacade = build(
            settings=self.loop._settings, planner=self.loop, monotonic=self.tick
        )


@pytest.fixture
async def planned(container: Container) -> AsyncIterator[Planned]:
    p = Planned()
    container.facade = p.facade
    task = asyncio.create_task(p.loop.run())
    await asyncio.sleep(0)
    try:
        yield p
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_outlook_requires_session(planned: Planned, api_client: AsyncClient) -> None:
    assert (await api_client.get(URL)).status_code == 401


async def test_outlook_without_engine_or_planner(
    container: Container, api_client: AsyncClient
) -> None:
    await login(api_client)
    resp = await api_client.get(URL)
    assert (resp.status_code, resp.json()) == (503, {"detail": "engine not started"})
    container.facade = build()
    resp = await api_client.get(URL)
    assert (resp.status_code, resp.json()) == (503, {"detail": "planner not started"})
    # Цикл создан, но его задача не идёт (до старта или в перезапуске после падения).
    loop, _, _ = rig(awake())
    container.facade = build(planner=loop)
    resp = await api_client.get(URL)
    assert (resp.status_code, resp.json()) == (503, {"detail": "planner not started"})


async def test_outlook_reads_without_writing(planned: Planned, api_client: AsyncClient) -> None:
    await login(api_client)
    resp = await api_client.get(URL)
    assert resp.status_code == 200
    body = resp.json()
    assert body["phase"] == "free"
    assert body["decision"]["kind"] == "act" and body["decision"]["scenario"] == "deed:job"
    assert [c["verdict"] for c in body["considered"]].count("chosen") == 1
    assert body["loop"] == {
        "paused": False,
        "ready": None,
        "auto": False,
        "current": None,
        "manual_queue": 0,
        "next_wake": None,
    }
    assert {t["kind"] for t in body["wakeups"]} >= {"gorbushka_comeback", "sleep_window"}
    assert body["hints"]["sleep_hours"] == 7
    assert body["reserves"] == []
    store: CountingStore = planned.store
    assert store.decisions == [] and store.runs == []
    assert planned.facade.gateway.queue_size == 0


async def test_outlook_cached_by_versions_and_ttl(
    planned: Planned, api_client: AsyncClient
) -> None:
    await login(api_client)
    loop: PlannerLoop = planned.loop
    first = (await api_client.get(URL)).json()
    planned.tick.at += OUTLOOK_TTL_S - 0.1
    assert (await api_client.get(URL)).json() == first
    assert planned.passes == 1
    loop.revision += 1
    await api_client.get(URL)
    assert planned.passes == 2
    await planned.facade.settings.update(lambda s: s, changed_by="test")
    await api_client.get(URL)
    assert planned.passes == 3
    planned.tick.at += OUTLOOK_TTL_S
    await api_client.get(URL)
    assert planned.passes == 4


async def test_loop_state_is_fresh_over_cached_pass(
    planned: Planned, api_client: AsyncClient
) -> None:
    await login(api_client)
    first = (await api_client.get(URL)).json()
    planned.ready = "tg_offline"
    again = (await api_client.get(URL)).json()
    assert planned.passes == 1
    assert again["decision"] == first["decision"]
    assert (first["loop"]["ready"], again["loop"]["ready"]) == (None, "tg_offline")

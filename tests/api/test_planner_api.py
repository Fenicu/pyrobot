import asyncio
import time
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import pytest
from httpx import AsyncClient

from app.api.container import Container
from app.engine.facade import OUTLOOK_TTL_S, EngineFacade
from app.engine.gametime import MSK
from app.engine.planner.decide import Outlook
from app.engine.planner.loop import PlannerLoop
from app.engine.planner.types import Act
from tests.api.conftest import login
from tests.engine.planner.test_decide import awake, m
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
        "wait_reason": None,
        "wake_at": None,
    }
    assert {t["kind"] for t in body["wakeups"]} >= {"gorbushka_comeback", "sleep_window"}
    assert body["hints"]["sleep_hours"] == 7
    assert body["reserves"] == []
    assert body["basis"] is None
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


async def test_loop_wait_is_in_the_plan(planned: Planned, api_client: AsyncClient) -> None:
    await login(api_client)
    loop: PlannerLoop = planned.loop
    # Таймеры по часам планировщик считает в МСК: наружу — в UTC, как у остальных моментов плана.
    loop.next_wake = datetime(2026, 9, 29, 17, 42, 54, tzinfo=MSK)
    loop._waiting = ("book_ready", loop.next_wake)
    body = (await api_client.get(URL)).json()
    assert {k: body["loop"][k] for k in ("next_wake", "wait_reason", "wake_at")} == {
        "next_wake": "2026-09-29T14:42:54Z",
        "wait_reason": "book_ready",
        "wake_at": "2026-09-29T14:42:54Z",
    }


class Moving:
    """Часы цикла, которые двигает тест."""

    def __init__(self) -> None:
        self.at = m(0)

    def now(self) -> datetime:
        return self.at

    def monotonic(self) -> float:
        return time.monotonic()


async def test_plan_shows_the_wait_the_loop_took(
    planned: Planned, api_client: AsyncClient
) -> None:
    """Ожидание, которое принял step(), в плане видно и тогда, когда часы ушли вперёд и решение
    плана уже другое; исполненное решение ожидание снимает."""
    await login(api_client)
    loop: PlannerLoop = planned.loop
    clock = Moving()
    loop._clock = clock
    # 🔥 нет, тик регенерации — через 90 минут: цикл ждёт его, но проснётся через 30 (предел
    # простоя).
    loop._state = lambda: awake(motivation=0, motivation_next_at=m(90))
    await loop.step()
    waiting = {
        "next_wake": "2026-09-26T11:30:03Z",
        "wait_reason": "motivation",
        "wake_at": "2026-09-26T10:30:00Z",
    }

    def wait_of(body: dict[str, Any]) -> dict[str, Any]:
        return {k: body["loop"][k] for k in waiting}

    body = (await api_client.get(URL)).json()
    assert (body["decision"]["kind"], body["decision"]["reason"]) == ("wait", "motivation")
    assert wait_of(body) == waiting
    # Через 20 минут профиль устарел: план — обновить его, а цикл ещё спит до своего срока.
    clock.at = m(20)
    planned.tick.at += OUTLOOK_TTL_S
    body = (await api_client.get(URL)).json()
    assert body["now"] == "2026-09-26T10:20:00Z"
    assert (body["decision"]["kind"], body["decision"]["scenario"]) == ("act", "refresh")
    assert wait_of(body) == waiting
    # Проснулся в срок и исполнил решение: ожидания нет.
    executed: list[Act] = []

    async def execute(act: Act, decision_id: int, *, dry_run: bool) -> None:
        executed.append(act)

    loop._execute = execute  # type: ignore[method-assign]
    clock.at = m(30)
    await loop.step()
    body = (await api_client.get(URL)).json()
    assert [a.scenario for a in executed] == ["refresh"]
    assert wait_of(body) == dict.fromkeys(waiting)


async def test_stale_busy_plan_is_by_last_known_data(
    planned: Planned, api_client: AsyncClient
) -> None:
    await login(api_client)
    planned.loop._state = lambda: awake(m(-26))
    body = (await api_client.get(URL)).json()
    assert (body["phase"], body["decision"]["scenario"]) == ("unknown", "refresh")
    assert [c["verdict"] for c in body["considered"]] == ["stale:busy", "chosen"]
    basis = body["basis"]
    assert (basis["since"], basis["busy_at"], basis["ended"]) == (
        "2026-09-26T09:34:00Z",
        "2026-09-26T09:34:00Z",
        None,
    )
    assert {c["verdict"] for c in basis["considered"]} == {"ok"}
    assert [a["scenario"] for a in body["also_ready"]] == ["deed:job"]
    assert body["hints"]["next_deed"] == {"deed": "deed:job", "why": "best"}
    assert body["wakeups"]

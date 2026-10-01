import asyncio
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient

from app.api.container import Container
from app.db.base import Database
from app.db.planner import DbPlannerStore
from app.engine.clock import SystemClock
from app.engine.facade import EngineFacade
from app.engine.planner.loop import PlannerLoop
from app.engine.transport.fake import FakeTgBackend
from tests.api.conftest import login, run_engine
from tests.engine.fakegame import World, running_world
from tests.engine.helpers import tg_auth
from tests.engine.planner.test_loop import QUIET
from tests.engine.test_facade import build

pytestmark = pytest.mark.db


class _Notes:
    async def notify(self, level: str, code: str, text: str) -> None:
        pass


@pytest.fixture
async def world(container: Container, clean_db: Database) -> AsyncIterator[World]:
    async for w in running_world(QUIET):
        planner = PlannerLoop(
            gateway=w.gateway,
            state=lambda w=w: w.state,  # type: ignore[misc]
            settings=w.settings,
            clock=SystemClock(),
            store=DbPlannerStore(clean_db, 1),
            notifier=_Notes(),
            ready=lambda: None,
            step_timeout_s=0.3,
            auto=False,
        )
        facade = EngineFacade(
            settings=w.settings,
            gateway=w.gateway,
            pipeline=w.pipeline,
            tg_auth=tg_auth(FakeTgBackend()),
            planner=planner,
        )
        run_engine(container, facade)
        task = asyncio.create_task(planner.run())
        try:
            yield w
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def _run(
    client: AsyncClient, h: dict[str, str], name: str, key: str, **params: object
) -> tuple[int, dict[str, object]]:
    r = await client.post(
        f"/api/v1/accounts/1/scenarios/{name}/run",
        headers=h,
        json={"params": params, "idempotency_key": key},
    )
    return r.status_code, r.json()


async def test_manual_scenario_run(world: World, api_client: AsyncClient) -> None:
    world.game.on_text("/read_exp", ("items", 3516680))
    h = {"X-CSRF-Token": await login(api_client)}
    code, body = await _run(api_client, h, "book", "s1")
    assert code == 202 and body["status"] == "queued"
    run_id = body["scenario_run_id"]

    async def status() -> str:
        run = (await api_client.get(f"/api/v1/accounts/1/scenario-runs/{run_id}")).json()
        return str(run["status"])

    for _ in range(200):
        if await status() == "done":
            break
        await asyncio.sleep(0.01)
    run = (await api_client.get(f"/api/v1/accounts/1/scenario-runs/{run_id}")).json()
    assert (run["status"], run["requested_by"], run["decision_id"]) == ("done", "admin", None)
    assert run["params"] == {"item": "book"}
    assert world.game.payloads() == ["/read_exp"]
    code, again = await _run(api_client, h, "book", "s1")
    assert code == 200 and again == {"scenario_run_id": run_id, "status": "done"}
    code, _ = await _run(api_client, h, "card", "s1")
    assert code == 422
    code, _ = await _run(api_client, h, "book", "s1", extra=1)
    assert code == 422
    # Повтор сверяется по присланным параметрам, а не по результату слияния с реестром.
    code, _ = await _run(api_client, h, "book", "s1", item="book")
    assert code == 422
    code, body = await _run(api_client, h, "book", "s5", item="card")
    assert code == 422 and body == {"detail": "fixed params: item"}
    code, _ = await _run(api_client, h, "book", "s6", item="book")
    assert code == 202
    code, _ = await _run(api_client, h, "nope", "s2")
    assert code == 404
    r = await api_client.post(
        "/api/v1/accounts/1/scenarios/book/run", json={"params": {}, "idempotency_key": "s3"}
    )
    assert r.status_code == 403
    too_many = {f"p{i}": i for i in range(17)}
    code, _ = await _run(api_client, h, "book", "s4", **too_many)
    assert code == 422


async def test_run_without_required_params_is_422(world: World, api_client: AsyncClient) -> None:
    h = {"X-CSRF-Token": await login(api_client)}
    code, body = await _run(api_client, h, "refresh", "q1")
    assert code == 422 and body == {"detail": "invalid params: source"}
    code, body = await _run(api_client, h, "refresh", "q2", source="bank")
    assert code == 422 and body == {"detail": "invalid params: source"}
    code, _ = await _run(api_client, h, "refresh", "q3", source="profile")
    assert code == 202


async def test_uncertified_run_is_simulated(world: World, api_client: AsyncClient) -> None:
    h = {"X-CSRF-Token": await login(api_client)}
    code, body = await _run(api_client, h, "deed:rob", "u1")
    assert code == 202
    run_id = body["scenario_run_id"]
    for _ in range(200):
        run = (await api_client.get(f"/api/v1/accounts/1/scenario-runs/{run_id}")).json()
        if run["status"] not in ("queued", "running"):
            break
        await asyncio.sleep(0.01)
    assert (run["status"], run["reason"]) == ("suppressed", "uncertified")
    assert world.game.payloads() == []


async def test_scenario_catalog(container: Container, api_client: AsyncClient) -> None:
    await login(api_client)
    items = {i["name"]: i for i in (await api_client.get("/api/v1/scenarios")).json()}
    assert items["container_medium"]["certified"] and not items["deed:rob"]["certified"]
    assert items["deed:job"]["params"] == {"activity": "job"}
    assert items["deed:job"]["required"] == {}
    assert items["sleep"]["required"] == {"hours": {"type": "int", "min": 7, "max": 12}}
    assert items["tangerine"]["required"] == {"chat": {"type": "int"}, "reply_to": {"type": "int"}}
    assert items["fastfood"]["required"] == {
        "food": {"type": "enum", "values": ["banana", "burger", "hotdog", "pizza"]}
    }
    assert items["bulls_join"]["required"] == {
        "code": {"type": "string", "pattern": "^join_fight_[A-Za-z0-9_-]{11}$"}
    }
    assert (items["lottery_buy"]["certified"], items["lottery_buy"]["required"]) == (True, {})
    recipe = items["smoothie"]["required"]["recipe"]
    assert recipe["type"] == "string" and recipe["pattern"].endswith("){5}$")


async def test_run_without_planner(container: Container, api_client: AsyncClient) -> None:
    run_engine(container, build())
    h = {"X-CSRF-Token": await login(api_client)}
    code, _ = await _run(api_client, h, "book", "s1")
    assert code == 503

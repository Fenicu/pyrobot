from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from app.api.container import Container
from app.db.base import Database
from app.db.journal import DbJournal
from app.db.metro import DbMetroRunStore
from app.db.models import MetricRow
from app.db.notifications import DbNotifier
from app.db.planner import DbPlannerStore
from app.engine.events import Unrecognized
from app.engine.metro.budget import Budget
from app.engine.metro.live import LIVE, live_frame
from app.engine.metro.solver import MetroSolver, policy_of
from app.engine.planner.types import Act
from app.engine.settings import MetroSection
from tests.api.conftest import login, run_engine
from tests.engine.helpers import make_msg
from tests.engine.test_facade import build

pytestmark = pytest.mark.db

T0 = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def _at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


async def _metrics(db: Database, points: list[tuple[int, str, float]]) -> None:
    async with db.sessions() as s, s.begin():
        s.add_all(MetricRow(account_id=1, ts=_at(m), key=k, value=v) for m, k, v in points)


async def test_reference_needs_session(container: Container, api_client: AsyncClient) -> None:
    for path in ("/metrics", "/metro/runs", "/metro/live", "/unrecognized", "/notifications"):
        assert (await api_client.get(f"/api/v1/accounts/1{path}")).status_code == 401


async def test_metrics_window_fields_and_paging(
    container: Container, api_client: AsyncClient, clean_db: Database
) -> None:
    await _metrics(
        clean_db,
        [
            (-10, "money", 100),
            (-5, "stamina", 50),
            (0, "money", 110),
            (1, "stamina", 60),
            (2, "money", 120),
            (3, "money", 130),
            (30, "money", 999),
        ],
    )
    await login(api_client)
    window = {"from": _at(0).isoformat(), "to": _at(10).isoformat()}
    body = (await api_client.get("/api/v1/accounts/1/metrics", params=window)).json()
    assert body["series"]["money"] == [
        [_at(0).isoformat().replace("+00:00", "Z"), 110.0],
        [_at(2).isoformat().replace("+00:00", "Z"), 120.0],
        [_at(3).isoformat().replace("+00:00", "Z"), 130.0],
    ]
    assert [p[1] for p in body["series"]["stamina"]] == [60.0]
    # Значение на начало окна — последняя точка до него (метрики пишутся только при изменении).
    assert body["initial"]["money"][1] == 100.0 and body["initial"]["stamina"][1] == 50.0
    assert body["next_cursor"] is None
    only = (
        await api_client.get("/api/v1/accounts/1/metrics", params={**window, "fields": "stamina"})
    ).json()
    assert set(only["series"]) == {"stamina"} and set(only["initial"]) == {"stamina"}
    first = (
        await api_client.get("/api/v1/accounts/1/metrics", params={**window, "limit": 2})
    ).json()
    assert sum(len(v) for v in first["series"].values()) == 2 and first["next_cursor"]
    rest = (
        await api_client.get(
            "/api/v1/accounts/1/metrics",
            params={**window, "limit": 2, "cursor": first["next_cursor"]},
        )
    ).json()
    assert sum(len(v) for v in rest["series"].values()) == 2 and rest["initial"] == {}
    assert rest["events"] == []
    bad = await api_client.get("/api/v1/accounts/1/metrics", params={"fields": "money,password"})
    assert bad.status_code == 422
    too_many = await api_client.get("/api/v1/accounts/1/metrics", params={"limit": 5001})
    assert too_many.status_code == 422


async def test_metrics_events_are_done_runs_that_move_metrics(
    container: Container, api_client: AsyncClient, clean_db: Database
) -> None:
    planner = DbPlannerStore(clean_db, 1)

    async def run(scenario: str, start: int, status: str = "done") -> None:
        decision = await planner.record(_at(start), Act(scenario, {}, "test"))
        run_id = await planner.run_started(decision, scenario, {}, _at(start))
        await planner.run_finished(run_id, status, "", _at(start + 1))

    await run("stocks_dump", 1)
    await run("sleep", 5)
    await run("lottery_buy", 3, status="failed")
    await run("refresh", 4)
    await run("metro", 20)
    await login(api_client)
    window = {"from": _at(0).isoformat(), "to": _at(10).isoformat()}
    body = (await api_client.get("/api/v1/accounts/1/metrics", params=window)).json()
    # Только удачные, только те, что двигают метрики, и только кончившиеся в окне.
    assert body["events"] == [
        {"at": _at(2).isoformat().replace("+00:00", "Z"), "scenario": "stocks_dump"},
        {"at": _at(6).isoformat().replace("+00:00", "Z"), "scenario": "sleep"},
    ]


async def test_metro_runs_list_and_detail(
    container: Container, api_client: AsyncClient, clean_db: Database
) -> None:
    store = DbMetroRunStore(clean_db, 1)
    record = {
        "started_at": _at(0).isoformat(),
        "finished_at": _at(40).isoformat(),
        "outcome": "finished",
        "steps": 120,
        "duration_s": 2400.0,
        "grid": {"cells": [[0, 0, "."]]},
        "path": [[0, 0]],
        "exit": [3, 4],
    }
    first = await store.save(None, "done", record)
    visited = {"cells": [[0, 0, "."]], "visited": [[0, 0], [0, 1], [1, 1]]}
    second = await store.save(
        None, "stopped", {**record, "started_at": _at(60).isoformat(), "grid": visited}
    )
    await login(api_client)
    runs = (await api_client.get("/api/v1/accounts/1/metro/runs")).json()
    assert [r["id"] for r in runs["items"]] == [second, first]
    assert "grid" not in runs["items"][0] and runs["items"][1]["summary"] == {"exit": [3, 4]}
    # Посещённые клетки — для «шагов на клетку» в сводке списка; без grid.visited — 0.
    assert [r["visited"] for r in runs["items"]] == [3, 0]
    page = (await api_client.get("/api/v1/accounts/1/metro/runs", params={"limit": 1})).json()
    assert [r["id"] for r in page["items"]] == [second] and page["next_before"] == second
    tail = (
        await api_client.get(
            "/api/v1/accounts/1/metro/runs", params={"limit": 1, "before": second}
        )
    ).json()
    assert [r["id"] for r in tail["items"]] == [first] and tail["next_before"] is None
    full = (await api_client.get(f"/api/v1/accounts/1/metro/runs/{first}")).json()
    assert full["grid"] == {"cells": [[0, 0, "."]]} and full["steps"] == 120
    assert full["visited"] == 0
    assert (await api_client.get(f"/api/v1/accounts/1/metro/runs/{second}")).json()["visited"] == 3
    assert (await api_client.get("/api/v1/accounts/1/metro/runs/999999")).status_code == 404
    # Пункт списка — та же сводка, что у забега целиком, только без тяжёлых полей.
    heavy = {"grid", "path", "events", "vitals"}
    for item in runs["items"]:
        detail = (await api_client.get(f"/api/v1/accounts/1/metro/runs/{item['id']}")).json()
        assert item == {k: v for k, v in detail.items() if k not in heavy}


async def test_metro_live_last_frame_of_running_engine(
    container: Container, api_client: AsyncClient
) -> None:
    await login(api_client)
    url = "/api/v1/accounts/1/metro/live"
    # Движок не запущен — живого кадра нет и быть не может.
    assert (await api_client.get(url)).json() == {"detail": "engine not running"}
    engine = run_engine(container, build())
    # Забега с запуска движка не было.
    empty = await api_client.get(url)
    assert (empty.status_code, empty.content) == (204, b"")
    budget = Budget(started=T0, battle_at=_at(60), margin=timedelta(minutes=20))
    solver = MetroSolver(policy_of(MetroSection()), budget, pos=(0, 1), steps=1)
    solver.grid.cells[(0, 1)] = "."
    solver.events.append(
        {"step": 1, "pos": [0, 1], "kind": "metro_loot", "item": "money", "amount": 7}
    )
    solver.vitals.append({"step": 1, "pos": [0, 1], "stamina": 90, "packs": 2})
    frame = live_frame(
        solver, message_id=77, scenario_run_id=5, started=T0, now=_at(10), running=True
    )
    engine.stream.publish(LIVE, frame)
    body = (await api_client.get(url)).json()
    assert datetime.fromisoformat(body.pop("started_at")) == T0
    assert datetime.fromisoformat(body.pop("battle_at")) == _at(60)
    assert datetime.fromisoformat(body.pop("kick_at")) == _at(45)
    assert body == {
        k: v for k, v in frame.items() if k not in ("started_at", "battle_at", "kick_at")
    }
    assert body["found"] == {"money": 7} and body["budget"]["used"] == 0.25
    done = live_frame(
        solver,
        message_id=77,
        scenario_run_id=5,
        started=T0,
        now=_at(20),
        running=False,
        outcome="finished",
    )
    engine.stream.publish(LIVE, done)
    last = (await api_client.get(url)).json()
    assert (last["running"], last["outcome"]) == (False, "finished")


async def test_unrecognized_list_and_ack(
    container: Container, api_client: AsyncClient, clean_db: Database
) -> None:
    journal = DbJournal(clean_db, 1)
    for i in (1, 2):
        await journal.append(
            make_msg(f"странное {i}\nвторая строка", msg_id=i),
            [Unrecognized(first_line=f"странное {i}")],
            None,
            0,
        )
    h = {"X-CSRF-Token": await login(api_client)}
    items = (await api_client.get("/api/v1/accounts/1/unrecognized")).json()["items"]
    assert [i["first_line"] for i in items] == ["странное 2", "странное 1"]
    assert items[0]["text"] == "странное 2\nвторая строка" and items[0]["acked"] is False
    ack = {"ids": [items[0]["id"]]}
    assert (
        await api_client.post("/api/v1/accounts/1/unrecognized/ack", json=ack)
    ).status_code == 403
    r = await api_client.post("/api/v1/accounts/1/unrecognized/ack", headers=h, json=ack)
    assert r.json() == {"acked": 1}
    open_items = (await api_client.get("/api/v1/accounts/1/unrecognized")).json()["items"]
    assert [i["first_line"] for i in open_items] == ["странное 1"]
    everything = (
        await api_client.get("/api/v1/accounts/1/unrecognized", params={"acked": "all"})
    ).json()
    assert len(everything["items"]) == 2
    too_many = {"ids": list(range(501))}
    r = await api_client.post("/api/v1/accounts/1/unrecognized/ack", headers=h, json=too_many)
    assert r.status_code == 422


async def test_notifications_list_and_read(
    container: Container, api_client: AsyncClient, clean_db: Database
) -> None:
    notifier = DbNotifier(clean_db, 1)
    await notifier.notify("info", "engine_paused", "paused by admin")
    await notifier.notify("error", "task_failed:planner", "planner crashed")
    await notifier.notify("warn", "reconcile_stuck", "stuck")
    h = {"X-CSRF-Token": await login(api_client)}
    body = (await api_client.get("/api/v1/accounts/1/notifications")).json()
    assert [n["code"] for n in body["items"]] == [
        "reconcile_stuck",
        "task_failed:planner",
        "engine_paused",
    ]
    assert (body["unread"], body["unread_alerts"]) == (3, 2)
    errors = (
        await api_client.get("/api/v1/accounts/1/notifications", params={"level": "error"})
    ).json()
    assert [n["code"] for n in errors["items"]] == ["task_failed:planner"]
    middle = body["items"][1]["id"]
    r = await api_client.post(
        "/api/v1/accounts/1/notifications/read", headers=h, json={"up_to_id": middle}
    )
    assert r.json() == {"read": 2}
    unread = (
        await api_client.get("/api/v1/accounts/1/notifications", params={"unread": True})
    ).json()
    assert [n["code"] for n in unread["items"]] == ["reconcile_stuck"]
    assert (unread["unread"], unread["unread_alerts"]) == (1, 1)
    page = (await api_client.get("/api/v1/accounts/1/notifications", params={"limit": 1})).json()
    assert page["next_before"] == page["items"][0]["id"]

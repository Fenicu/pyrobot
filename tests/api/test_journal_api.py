from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from app.api.container import Container
from app.db.base import Database
from app.db.journal import DbJournal
from app.db.metro import DbMetroRunStore
from app.db.models import ActionRow
from app.db.planner import DbPlannerStore
from app.engine.parsing.refusals import Busy
from app.engine.planner.types import Act, Candidate, Wait
from tests.api.conftest import login
from tests.engine.helpers import GAME, make_msg

pytestmark = pytest.mark.db

T0 = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def _at(s: int) -> datetime:
    return T0 + timedelta(seconds=s)


async def _action(db: Database, at: datetime, **kw: object) -> int:
    row = ActionRow(
        account_id=1,
        created_at=at,
        source=kw.get("source", "planner"),
        kind=kw.get("kind", "send"),
        chat_id=kw.get("chat_id", GAME),
        payload=kw.get("payload", {"text": kw.get("text", "/job"), "data": None}),
        command_class="action",
        status=kw.get("status", "confirmed"),
        reason="",
        scenario_run_id=kw.get("run"),
        idempotency_key=kw.get("key"),
    )
    async with db.sessions() as s, s.begin():
        s.add(row)
        await s.flush()
        return row.id


async def _seed(db: Database) -> dict[str, int]:
    journal = DbJournal(db, 1)
    planner = DbPlannerStore(db, 1)
    ids: dict[str, int] = {}
    m1 = await journal.append(make_msg("first", msg_id=1, received_at=_at(0)), [], None, 0)
    m2 = await journal.append(
        make_msg("❗️Ты занят", msg_id=2, received_at=_at(10)), [Busy(left_s=60)], None, 0
    )
    other = await journal.append(
        make_msg("swinfo", chat_id=-100, msg_id=3, received_at=_at(20)), [], None, 0
    )
    assert m1 and m2 and other
    ids |= {"m1": m1, "m2": m2, "other": other}
    ids["a1"] = await _action(db, _at(10), text="/job")
    ids["a2"] = await _action(db, _at(30), status="rejected", source="manual", text="/harvest")
    ids["d1"] = await planner.record(
        _at(10),
        Act("deed:job", {"activity": "job"}, "best_score", (Candidate("deed:job", score=1.5),)),
    )
    ids["d2"] = await planner.record(_at(40), Wait(_at(100), "busy"))
    ids["run"] = await planner.run_started(ids["d1"], "deed:job", {"activity": "job"}, _at(11))
    return ids


async def _page(client: AsyncClient, **params: object) -> dict[str, object]:
    r = await client.get("/api/v1/accounts/1/journal", params=params)
    assert r.status_code == 200, r.text
    body: dict[str, object] = r.json()
    return body


async def test_journal_needs_session(container: Container, api_client: AsyncClient) -> None:
    assert (await api_client.get("/api/v1/accounts/1/journal")).status_code == 401
    assert (await api_client.get("/api/v1/accounts/1/actions/1")).status_code == 401
    assert (await api_client.get("/api/v1/accounts/1/scenario-runs")).status_code == 401


async def test_feed_merges_types_newest_first(
    container: Container, api_client: AsyncClient, clean_db: Database
) -> None:
    ids = await _seed(clean_db)
    await login(api_client)
    body = await _page(api_client)
    got = [(i["type"], i["id"]) for i in body["items"]]  # type: ignore[attr-defined]
    # На одном моменте: сообщение, потом действие, потом решение (от новых к старым — наоборот).
    assert got == [
        ("decision", ids["d2"]),
        ("action", ids["a2"]),
        ("message", ids["other"]),
        ("decision", ids["d1"]),
        ("action", ids["a1"]),
        ("message", ids["m2"]),
        ("message", ids["m1"]),
    ]
    assert body["next_cursor"] is None
    items = body["items"]
    msg = next(i for i in items if i["type"] == "message" and i["id"] == ids["m2"])  # type: ignore[attr-defined]
    assert msg["text"] == "❗️Ты занят" and msg["events"] == [{"kind": "busy", "left_s": 60}]
    act = next(i for i in items if i["id"] == ids["a2"] and i["type"] == "action")  # type: ignore[attr-defined]
    assert (act["status"], act["source"], act["text"]) == ("rejected", "manual", "/harvest")
    dec = next(i for i in items if i["id"] == ids["d1"] and i["type"] == "decision")  # type: ignore[attr-defined]
    assert (dec["kind"], dec["scenario"], dec["reason"]) == ("act", "deed:job", "best_score")


async def test_forward_in_feed_has_message_and_chat_title(
    container: Container, api_client: AsyncClient, clean_db: Database
) -> None:
    payload = {
        "text": None,
        "data": None,
        "message_id": 3625831,
        "from_chat_id": GAME,
        "chat_title": "☣️ SU",
    }
    await _action(clean_db, _at(5), kind="forward", chat_id=-1001149209877, payload=payload)
    await _action(clean_db, _at(6), text="/job")
    await login(api_client)
    fwd, job = reversed((await _page(api_client))["items"])  # type: ignore[call-overload]
    assert (fwd["kind"], fwd["message_id"], fwd["chat_title"]) == ("forward", 3625831, "☣️ SU")
    assert (job["text"], job["message_id"], job["chat_title"]) == ("/job", None, None)


async def test_feed_links_decision_and_steps_by_run(
    container: Container, api_client: AsyncClient, clean_db: Database
) -> None:
    # По run_id админка собирает решение и шаги его запуска в одну строку хроники.
    ids = await _seed(clean_db)
    step = await _action(clean_db, _at(12), text="/job", run=ids["run"], source="scenario")
    await login(api_client)
    items = (await _page(api_client))["items"]
    run_of = {(i["type"], i["id"]): i["run_id"] for i in items if i["type"] != "message"}  # type: ignore[attr-defined]
    assert run_of == {
        ("decision", ids["d2"]): None,
        ("action", ids["a2"]): None,
        ("action", step): ids["run"],
        ("decision", ids["d1"]): ids["run"],
        ("action", ids["a1"]): None,
    }


async def test_feed_cursor_pages_without_gaps(
    container: Container, api_client: AsyncClient, clean_db: Database
) -> None:
    await _seed(clean_db)
    await login(api_client)
    seen: list[tuple[str, int]] = []
    cursor = None
    while True:
        params: dict[str, object] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        body = await _page(api_client, **params)
        seen += [(i["type"], i["id"]) for i in body["items"]]  # type: ignore[attr-defined]
        cursor = body["next_cursor"]
        if cursor is None:
            break
    full = await _page(api_client, limit=200)
    assert seen == [(i["type"], i["id"]) for i in full["items"]]  # type: ignore[attr-defined]
    assert len(seen) == 7
    bad = await api_client.get("/api/v1/accounts/1/journal", params={"cursor": "garbage"})
    assert bad.status_code == 422


async def test_feed_filters(
    container: Container, api_client: AsyncClient, clean_db: Database
) -> None:
    ids = await _seed(clean_db)
    await login(api_client)

    def pairs(body: dict[str, object]) -> list[tuple[str, int]]:
        return [(i["type"], i["id"]) for i in body["items"]]  # type: ignore[attr-defined]

    assert pairs(await _page(api_client, types="action")) == [
        ("action", ids["a2"]),
        ("action", ids["a1"]),
    ]
    by_chat = pairs(await _page(api_client, chat_id=-100))
    assert by_chat == [("message", ids["other"])]
    window = pairs(await _page(api_client, since=_at(10).isoformat(), until=_at(30).isoformat()))
    assert window == [
        ("message", ids["other"]),
        ("decision", ids["d1"]),
        ("action", ids["a1"]),
        ("message", ids["m2"]),
    ]
    assert pairs(await _page(api_client, status="rejected")) == [("action", ids["a2"])]
    assert pairs(await _page(api_client, source="planner")) == [("action", ids["a1"])]
    assert (
        await api_client.get("/api/v1/accounts/1/journal", params={"types": "nope"})
    ).status_code == 422
    assert (
        await api_client.get("/api/v1/accounts/1/journal", params={"limit": 201})
    ).status_code == 422


async def test_details(container: Container, api_client: AsyncClient, clean_db: Database) -> None:
    ids = await _seed(clean_db)
    metro = DbMetroRunStore(clean_db, 1)
    metro_id = await metro.save(ids["run"], "done", {"started_at": _at(11).isoformat()})
    await login(api_client)
    dec = (await api_client.get(f"/api/v1/accounts/1/decisions/{ids['d1']}")).json()
    assert dec["candidates"] == [
        {"scenario": "deed:job", "params": {}, "score": 1.5, "verdict": "ok"}
    ]
    assert dec["params"] == {"activity": "job"}
    assert [r["id"] for r in dec["runs"]] == [ids["run"]]
    act = (await api_client.get(f"/api/v1/accounts/1/actions/{ids['a1']}")).json()
    assert act["payload"]["text"] == "/job" and act["command_class"] == "action"
    run = (await api_client.get(f"/api/v1/accounts/1/scenario-runs/{ids['run']}")).json()
    assert (run["scenario"], run["status"], run["decision_id"]) == (
        "deed:job",
        "running",
        ids["d1"],
    )
    assert run["metro_run_id"] == metro_id
    for path in ("decisions", "actions", "scenario-runs"):
        assert (await api_client.get(f"/api/v1/{path}/999999")).status_code == 404


async def test_scenario_runs_list_and_actions(
    container: Container, api_client: AsyncClient, clean_db: Database
) -> None:
    planner = DbPlannerStore(clean_db, 1)
    decision = await planner.record(_at(0), Wait(_at(100), "busy"))
    planned = await planner.run_started(decision, "deed:job", {"activity": "job"}, _at(1))
    manual_ids = []
    for i, name in enumerate(("sleep", "metro", "sleep")):
        run_id, _ = await planner.run_requested(
            name, {}, requested={}, key=f"k{i}", by="admin", at=_at(2 + i)
        )
        manual_ids.append(run_id)
    step1 = await _action(clean_db, _at(5), text="/job", run=planned)
    step2 = await _action(clean_db, _at(6), text="/job2", run=planned)
    await _action(clean_db, _at(7), text="/inv", source="manual", key="manual:x")
    await login(api_client)

    async def ids(**params: object) -> tuple[list[int], object]:
        r = await api_client.get("/api/v1/accounts/1/scenario-runs", params=params)
        assert r.status_code == 200, r.text
        body = r.json()
        return [i["id"] for i in body["items"]], body["next_before"]

    assert await ids() == ([*reversed(manual_ids), planned], None)
    assert await ids(manual="true") == ([*reversed(manual_ids)], None)
    assert await ids(manual="false") == ([planned], None)
    assert await ids(manual="true", scenario="sleep") == (
        [manual_ids[2], manual_ids[0]],
        None,
    )
    first, cursor = await ids(manual="true", limit=2)
    assert (first, cursor) == ([manual_ids[2], manual_ids[1]], manual_ids[1])
    assert await ids(manual="true", limit=2, before=cursor) == ([manual_ids[0]], None)
    item = (await api_client.get("/api/v1/accounts/1/scenario-runs", params={"limit": 1})).json()[
        "items"
    ][0]
    assert (item["scenario"], item["status"], item["requested_by"]) == ("sleep", "queued", "admin")

    run = (await api_client.get(f"/api/v1/accounts/1/scenario-runs/{planned}")).json()
    assert [(a["id"], a["payload"]["text"], a["scenario_run_id"]) for a in run["actions"]] == [
        (step1, "/job", planned),
        (step2, "/job2", planned),
    ]
    empty = (await api_client.get(f"/api/v1/accounts/1/scenario-runs/{manual_ids[0]}")).json()
    assert empty["actions"] == []
    manual = (await api_client.get("/api/v1/accounts/1/actions/" + str(step2 + 1))).json()
    assert (manual["scenario_run_id"], manual["idempotency_key"]) == (None, "manual:x")
    assert (
        await api_client.get("/api/v1/accounts/1/scenario-runs", params={"limit": 101})
    ).status_code == 422

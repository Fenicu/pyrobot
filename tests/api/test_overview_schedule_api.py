from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.api.container import Container
from app.db.models import SettingsRow, StateSnapshot, User
from app.engine.planner.loop import LoopView, PlanView
from app.engine.planner.types import Wakeup
from app.engine.settings import Settings
from app.engine.state.model import (
    ArtifactCollect,
    BusyState,
    CharacterState,
    LotteryState,
    Obs,
    dump_state,
)
from tests.api.conftest import Api, login, make_user

pytestmark = pytest.mark.db

URL = "/api/v1/overview/schedule"
# 2026-10-17 09:00 UTC = 12:00 MSK
NOW = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)


class FakeOutlook:
    def __init__(
        self,
        wakeups: list[Wakeup] | None = None,
        after_wake: list[Wakeup] | None = None,
    ) -> None:
        self.wakeups = wakeups or []
        self.after_wake = after_wake or []


class FakeFacade:
    stream = None

    def __init__(
        self,
        wakeups: list[Wakeup] | None = None,
        after_wake: list[Wakeup] | None = None,
        fail: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self._outlook = FakeOutlook(wakeups, after_wake)
        self._fail = fail
        self._delay = delay
        self._loop = LoopView(
            paused=False,
            ready=None,
            auto=True,
            current=None,
            current_params=None,
            manual_queue=0,
            next_wake=None,
            wait_reason=None,
            wake_at=None,
        )

    async def outlook(self) -> PlanView:
        if self._delay > 0:
            await asyncio.sleep(self._delay)
        if self._fail is not None:
            raise self._fail
        return PlanView(NOW, cast(Any, self._outlook), self._loop)


async def owner_of(c: Container, login_name: str = "admin") -> int:
    async with c.db.sessions() as s:
        uid = await s.scalar(select(User.id).where(User.login == login_name))
    assert uid is not None
    return uid


async def create_account(
    c: Container,
    *,
    name: str = "acc",
    owner: str = "admin",
    state: CharacterState | None = None,
    stale_min: int = 15,
) -> int:
    account_id = (await c.accounts.create(await owner_of(c, owner), name)).id
    settings = Settings.model_validate(
        {"engine": {"state_stale_after_min": stale_min}}
    ).model_dump(mode="json")
    async with c.db.sessions() as s, s.begin():
        await s.merge(SettingsRow(account_id=account_id, version=1, data=settings))
        if state is not None:
            await s.merge(
                StateSnapshot(
                    account_id=account_id,
                    version=1,
                    state=dump_state(state),
                )
            )
    return account_id


def get_acc(body: dict[str, Any], account_id: int) -> dict[str, Any]:
    [acc] = [a for a in body["accounts"] if a["id"] == account_id]
    return cast(dict[str, Any], acc)


async def test_schedule_auth_required(api_client: AsyncClient) -> None:
    resp = await api_client.get(URL)
    assert resp.status_code == 401


async def test_schedule_hours_bounds(api: Api) -> None:
    r_bad_low = await api.client.get(f"{URL}?hours=0")
    assert r_bad_low.status_code == 422

    r_bad_high = await api.client.get(f"{URL}?hours=25")
    assert r_bad_high.status_code == 422

    r_ok = await api.client.get(URL)
    assert r_ok.status_code == 200
    data = r_ok.json()
    assert "now" in data and "until" in data
    assert "shared" in data and "accounts" in data

    r_1 = await api.client.get(f"{URL}?hours=1")
    assert r_1.status_code == 200

    r_24 = await api.client.get(f"{URL}?hours=24")
    assert r_24.status_code == 200


async def test_schedule_zero_accounts(api: Api) -> None:
    await make_user(api.container, "empty_user")
    await login(api.client, login="empty_user")
    resp = await api.client.get(URL)
    assert resp.status_code == 200
    data = resp.json()
    assert data["accounts"] == []
    # shared marks still present (factory, lottery, night, fallback battle)
    assert len(data["shared"]) > 0


async def test_schedule_foreign_accounts_excluded(api: Api) -> None:
    c = api.container
    await make_user(api.container, "other_user")
    aid = await create_account(c, name="foreign_acc", owner="other_user")

    resp = await api.client.get(URL)
    assert resp.status_code == 200
    account_ids = [a["id"] for a in resp.json()["accounts"]]
    assert aid not in account_ids


async def test_schedule_no_snapshot(api: Api) -> None:
    c = api.container
    aid = await create_account(c, name="empty_acc", owner="admin", state=None)

    resp = await api.client.get(URL)
    assert resp.status_code == 200
    acc = get_acc(resp.json(), aid)
    assert acc["id"] == aid
    assert acc["seen_at"] is None
    assert acc["stale"] is True
    assert acc["items"] == []


async def test_schedule_stale_flag(api: Api) -> None:
    c = api.container
    c.clock = cast(Any, type("Clock", (), {"now": lambda self: NOW})())

    # 1. Fresh state (level observed 5 min ago)
    st_fresh = CharacterState(
        level=Obs(value=10, at=NOW - timedelta(minutes=5)),
        money=Obs(value=100, at=NOW - timedelta(minutes=6)),
    )
    a_fresh = await create_account(c, name="fresh", owner="admin", state=st_fresh, stale_min=15)

    # 2. Stale state (level observed 20 min ago)
    st_stale = CharacterState(
        level=Obs(value=10, at=NOW - timedelta(minutes=20)),
        money=Obs(value=100, at=NOW - timedelta(minutes=25)),
    )
    a_stale = await create_account(c, name="stale", owner="admin", state=st_stale, stale_min=15)

    # 3. No level/money
    st_no_nums = CharacterState()
    a_no_nums = await create_account(
        c, name="no_nums", owner="admin", state=st_no_nums, stale_min=15
    )

    resp = await api.client.get(URL)
    assert resp.status_code == 200
    rows = {r["id"]: r for r in resp.json()["accounts"]}

    assert rows[a_fresh]["stale"] is False
    assert rows[a_fresh]["seen_at"] == (NOW - timedelta(minutes=5)).isoformat()

    assert rows[a_stale]["stale"] is True
    assert rows[a_stale]["seen_at"] == (NOW - timedelta(minutes=20)).isoformat()

    assert rows[a_no_nums]["stale"] is True
    assert rows[a_no_nums]["seen_at"] is None


async def test_schedule_state_items(api: Api) -> None:
    c = api.container
    c.clock = cast(Any, type("Clock", (), {"now": lambda self: NOW})())

    # Items in state
    busy_at = NOW - timedelta(minutes=10)
    busy_until = NOW + timedelta(minutes=20)
    sleep_dl = NOW + timedelta(hours=4)
    sleep_allowed = NOW + timedelta(hours=2)
    book = NOW + timedelta(hours=1)
    card = NOW + timedelta(hours=3)
    fastfood = NOW - timedelta(minutes=30)  # past mark
    prizebox = NOW + timedelta(hours=5)
    tangerine = NOW + timedelta(hours=6)
    metro = NOW + timedelta(hours=7)
    art_ends = NOW + timedelta(hours=8)
    lottery_until = NOW + timedelta(hours=9)

    st = CharacterState(
        level=Obs(value=1, at=NOW),
        busy=Obs(value=BusyState(activity="harvest", until=busy_until), at=busy_at),
        sleep_deadline=Obs(value=sleep_dl, at=NOW),
        sleep_allowed_at=Obs(value=sleep_allowed, at=NOW),
        book_ready_at=Obs(value=book, at=NOW),
        card_ready_at=Obs(value=card, at=NOW),
        fastfood_ready_at=Obs(value=fastfood, at=NOW),
        prizebox_ready_at=Obs(value=prizebox, at=NOW),
        tangerine_ready_at=Obs(value=tangerine, at=NOW),
        metro_ready_at=Obs(value=metro, at=NOW),
        artifact_collect=Obs(value=ArtifactCollect(artifact="Катана", ends_at=art_ends), at=NOW),
        lottery=Obs(value=LotteryState(draw=42, until=lottery_until), at=NOW),
    )
    aid = await create_account(c, name="all_state", owner="admin", state=st)

    resp = await api.client.get(f"{URL}?hours=12")
    assert resp.status_code == 200
    acc = get_acc(resp.json(), aid)
    kinds = {item["kind"]: item for item in acc["items"]}

    assert "busy" in kinds
    assert kinds["busy"]["detail"] == "harvest"
    assert kinds["busy"]["source"] == "state"
    assert kinds["busy"]["until"] == busy_until.isoformat()

    assert "sleep_deadline" in kinds
    assert kinds["sleep_deadline"]["until"] is None

    assert "sleep_allowed" in kinds
    assert "book_ready" in kinds
    assert "card_ready" in kinds

    # Past mark kept
    assert "fastfood_ready" in kinds
    assert kinds["fastfood_ready"]["at"] == fastfood.isoformat()

    assert "prizebox_ready" in kinds
    assert "tangerine_ready" in kinds
    assert "metro_ready" in kinds

    assert "artifact_end" in kinds
    assert kinds["artifact_end"]["detail"] == "Катана"

    assert "lottery_close" in kinds
    assert kinds["lottery_close"]["detail"] == "42"


async def test_schedule_sleep_activity_becomes_sleep_kind(api: Api) -> None:
    c = api.container
    c.clock = cast(Any, type("Clock", (), {"now": lambda self: NOW})())

    st = CharacterState(
        level=Obs(value=1, at=NOW),
        busy=Obs(
            value=BusyState(activity="sleep_bridge", until=NOW + timedelta(hours=2)),
            at=NOW - timedelta(minutes=5),
        ),
    )
    aid = await create_account(c, name="sleeper", owner="admin", state=st)

    resp = await api.client.get(URL)
    assert resp.status_code == 200
    acc = get_acc(resp.json(), aid)
    assert len(acc["items"]) == 1
    assert acc["items"][0]["kind"] == "sleep"
    assert acc["items"][0]["detail"] == "sleep_bridge"


async def test_schedule_planner_items_only_for_local_engine(api: Api) -> None:
    c = api.container
    c.clock = cast(Any, type("Clock", (), {"now": lambda self: NOW})())

    st = CharacterState(level=Obs(value=1, at=NOW))
    aid = await create_account(c, name="engine_acc", owner="admin", state=st)

    # Put FakeFacade in engines
    w1 = Wakeup(at=NOW + timedelta(hours=2), kind="motivation")
    w2 = Wakeup(at=NOW + timedelta(hours=3), kind="cooldown", key="dungeon")
    # book_ready is provided by state, should NOT be shown from planner
    w_dup = Wakeup(at=NOW + timedelta(hours=4), kind="book_ready")
    # outside window
    w_out = Wakeup(at=NOW + timedelta(hours=20), kind="stocks_dump")

    facade = FakeFacade(wakeups=[w1, w2, w_dup, w_out])
    api.engines.put(cast(Any, facade), aid)

    resp = await api.client.get(f"{URL}?hours=12")
    assert resp.status_code == 200
    acc = get_acc(resp.json(), aid)
    p_kinds = {item["kind"]: item for item in acc["items"] if item["source"] == "planner"}

    assert "motivation" in p_kinds
    assert "cooldown" in p_kinds
    assert p_kinds["cooldown"]["detail"] == "dungeon"
    assert "book_ready" not in p_kinds
    assert "stocks_dump" not in p_kinds


async def test_schedule_outlook_timeout_keeps_state_items(api: Api) -> None:
    c = api.container
    c.clock = cast(Any, type("Clock", (), {"now": lambda self: NOW})())

    st = CharacterState(
        level=Obs(value=1, at=NOW),
        book_ready_at=Obs(value=NOW + timedelta(hours=1), at=NOW),
    )
    aid = await create_account(c, name="timeout_acc", owner="admin", state=st)

    # Delay outlook past timeout (OUTLOOK_TIMEOUT_S is 2.0s)
    facade = FakeFacade(delay=3.0)
    api.engines.put(cast(Any, facade), aid)

    resp = await api.client.get(URL)
    assert resp.status_code == 200
    acc = get_acc(resp.json(), aid)
    assert any(item["kind"] == "book_ready" for item in acc["items"])


async def test_schedule_shared_marks_windows_and_midnight(api: Api) -> None:
    c = api.container
    # NOW: 2026-10-17 14:00 MSK (11:00 UTC)
    # Window: 12 hours -> until 2026-10-18 02:00 MSK (23:00 UTC)
    now = datetime(2026, 10, 17, 11, 0, tzinfo=UTC)
    c.clock = cast(Any, type("Clock", (), {"now": lambda self: now})())

    # Observed battle at 15:00 MSK (12:00 UTC)
    b_time = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
    st = CharacterState(
        level=Obs(value=1, at=now),
        battle_at=Obs(value=b_time + timedelta(minutes=1), at=now),
    )
    await create_account(c, name="battle_acc", owner="admin", state=st)

    resp = await api.client.get(f"{URL}?hours=12")
    assert resp.status_code == 200
    shared = resp.json()["shared"]
    kinds = [s["kind"] for s in shared]

    # 1. Battle observed at 15:00 MSK (12:00 UTC)
    assert "battle" in kinds
    battle_marks = [s for s in shared if s["kind"] == "battle"]
    assert any(s["at"] == b_time.isoformat() for s in battle_marks)

    # 2. Factory at 18:00 - 18:30 MSK (15:00 - 15:30 UTC)
    assert "factory" in kinds
    [factory] = [s for s in shared if s["kind"] == "factory"]
    assert factory["label"] == "18:00–18:30"
    assert factory["at"] == datetime(2026, 10, 17, 15, 0, tzinfo=UTC).isoformat()
    assert factory["until"] == datetime(2026, 10, 17, 15, 30, tzinfo=UTC).isoformat()

    # 3. Lottery at 19:17 - 21:07 MSK (16:17 - 18:07 UTC)
    assert "lottery" in kinds
    [lottery] = [s for s in shared if s["kind"] == "lottery"]
    assert lottery["label"] == "19:17–21:07"
    assert lottery["at"] == datetime(2026, 10, 17, 16, 17, tzinfo=UTC).isoformat()
    assert lottery["until"] == datetime(2026, 10, 17, 18, 7, tzinfo=UTC).isoformat()

    # 4. Night crossing midnight: starts 22:00 MSK (19:00 UTC)
    # and ends 08:00 MSK next day (05:00 UTC)
    assert "night" in kinds
    [night] = [s for s in shared if s["kind"] == "night"]
    assert night["label"] == "22:00–08:00"
    assert night["at"] == datetime(2026, 10, 17, 19, 0, tzinfo=UTC).isoformat()
    assert night["until"] == datetime(2026, 10, 18, 5, 0, tzinfo=UTC).isoformat()

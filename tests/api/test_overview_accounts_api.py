from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from httpx import AsyncClient
from sqlalchemy import update

from app.api import overview_battle
from app.api.container import Container
from app.db.models import Account
from app.engine.facade import PlannerUnavailable
from app.engine.planner.loop import PlanView
from app.engine.planner.types import Act, Wait
from app.engine.state.model import ArtifactCollect, Obs
from tests.api.conftest import Api, login, make_user
from tests.api.test_overview_battle_api import (
    B1,
    B2,
    HOOLI,
    NOW,
    UMBRELLA,
    FakeFacade,
    Frozen,
    account,
    owner_of,
    seen_battle,
)

pytestmark = pytest.mark.db

URL = "/api/v1/overview/accounts"


class PlanFacade(FakeFacade):
    """Фасад со «Планом бота»: решение задано тестом."""

    def __init__(self, decision: Act | Wait, **kw: Any) -> None:
        super().__init__(**kw)
        self._decision = decision

    async def outlook(self) -> PlanView:
        view = await super().outlook()
        return PlanView(view.now, cast(Any, SimpleNamespace(decision=self._decision)), view.loop)


def run(api: Api, account_id: int = 1, decision: Act | Wait | None = None, **kw: Any) -> None:
    facade = PlanFacade(decision or Wait(None, "idle"), **kw)
    api.engines.put(cast(Any, facade), account_id)


async def get(api: Api) -> list[dict[str, Any]]:
    resp = await api.client.get(URL)
    assert resp.status_code == 200, resp.text
    return cast(list[dict[str, Any]], resp.json())


def row_of(rows: list[dict[str, Any]], account_id: int = 1) -> dict[str, Any]:
    [row] = [r for r in rows if r["id"] == account_id]
    return row


@pytest.fixture(autouse=True)
def frozen(container: Container) -> None:
    container.clock = Frozen(NOW)


# --- доступ и границы ---


async def test_requires_session(api_client: AsyncClient) -> None:
    assert (await api_client.get(URL)).status_code == 401


async def test_user_without_accounts_gets_empty_list(api: Api) -> None:
    await make_user(api.container, "bob")
    await login(api.client, login="bob")
    assert await get(api) == []


async def test_foreign_and_deleting_accounts_not_returned(api: Api) -> None:
    await make_user(api.container, "bob")
    await account(api.container, name="bobs", owner="bob", level=9)
    mine = await account(api.container, 1, level=31)
    gone = await account(api.container, name="gone", level=5)
    async with api.db.sessions() as s, s.begin():
        await s.execute(update(Account).where(Account.id == gone).values(status="deleting"))
    assert [r["id"] for r in await get(api)] == [mine]


# --- снимок ---


async def test_account_without_snapshot_has_null_numbers(api: Api) -> None:
    uid = await owner_of(api.container)
    created = (await api.container.accounts.create(uid, "fresh")).id
    row = row_of(await get(api), created)
    assert row["seen_at"] is None and row["stale"] is False
    for key in ("exp", "exp_next", "money", "stamina", "motivation", "motivation_max"):
        assert row[key] is None
    for key in ("knowledge", "details", "raw", "skills", "artifact", "metro", "battle"):
        assert row[key] is None
    assert (row["engine"], row["next_action"]) == ("stopped", None)


async def test_other_schema_version_is_treated_as_no_snapshot(api: Api) -> None:
    await account(api.container, 1, level=31, state={"schema_version": 0})
    row = row_of(await get(api))
    assert (row["seen_at"], row["money"], row["battle"]) == (None, None, None)


async def test_numbers_come_from_observations(api: Api) -> None:
    ends = NOW + timedelta(days=2)
    await account(
        api.container,
        1,
        level=31,
        skills=(31, 28, 12, 9),
        exp=Obs(value=1240, at=NOW),
        exp_next=Obs(value=5000, at=NOW),
        money=Obs(value=18450, at=NOW),
        stamina=Obs(value=85, at=NOW),
        motivation=Obs(value=14, at=NOW),
        motivation_max=Obs(value=20, at=NOW),
        knowledge=Obs(value=310, at=NOW),
        details=Obs(value=42, at=NOW),
        raw=Obs(value=17, at=NOW),
        artifact_collect=Obs(value=ArtifactCollect(artifact="light", ends_at=ends), at=NOW),
        metro_ready_at=Obs(value=NOW + timedelta(hours=3), at=NOW),
    )
    row = row_of(await get(api))
    assert (row["exp"], row["exp_next"], row["money"], row["stamina"]) == (1240, 5000, 18450, 85)
    assert (row["motivation"], row["motivation_max"]) == (14, 20)
    assert (row["knowledge"], row["details"], row["raw"]) == (310, 42, 17)
    assert row["skills"] == {"practice": 31, "theory": 28, "cunning": 12, "wisdom": 9}
    assert row["artifact"] == {"artifact": "light", "ends_at": ends.isoformat()}
    assert row["metro"] == {"ready_at": (NOW + timedelta(hours=3)).isoformat()}


async def test_no_collection_and_no_metro_are_null(api: Api) -> None:
    await account(api.container, 1, level=31, artifact_collect=Obs(value=None, at=NOW))
    row = row_of(await get(api))
    assert (row["artifact"], row["metro"]) == (None, None)


async def test_seen_at_is_newest_of_level_and_money(api: Api) -> None:
    older = NOW - timedelta(minutes=3)
    await account(api.container, 1, level=31, money=Obs(value=1, at=older))
    assert row_of(await get(api))["seen_at"] == NOW.isoformat()
    await account(api.container, 1, state={"level": None}, money=Obs(value=1, at=older))
    assert row_of(await get(api))["seen_at"] == older.isoformat()


async def test_stale_flips_after_threshold(api: Api) -> None:
    edge = NOW - timedelta(minutes=15)
    await account(api.container, 1, money=Obs(value=1, at=edge))
    assert row_of(await get(api))["stale"] is False
    await account(api.container, 1, money=Obs(value=1, at=edge - timedelta(seconds=1)))
    assert row_of(await get(api))["stale"] is True


# --- движок и действие бота ---


async def test_engine_kinds(api: Api) -> None:
    first = await account(api.container, 1, level=1)
    second = await account(api.container, name="b", level=1)
    third = await account(api.container, name="c", level=1)
    run(api, first)
    api.engines.reasons[second] = "locked_elsewhere"
    rows = await get(api)
    kinds = [row_of(rows, i)["engine"] for i in (first, second, third)]
    assert kinds == ["local", "elsewhere", "stopped"]


async def test_next_action_act(api: Api) -> None:
    await account(api.container, 1, level=1)
    run(api, 1, Act("deed:harvest", {}, "deeds"))
    assert row_of(await get(api))["next_action"] == {
        "kind": "act",
        "scenario": "deed:harvest",
        "reason": "deeds",
        "until": None,
        "blocked_by": None,
    }


async def test_next_action_wait_has_until_in_utc(api: Api) -> None:
    await account(api.container, 1, level=1)
    until = NOW + timedelta(minutes=7)
    run(api, 1, Wait(until, "busy"))
    action = row_of(await get(api))["next_action"]
    assert (action["kind"], action["scenario"], action["reason"]) == ("wait", None, "busy")
    assert action["until"] == until.isoformat()


async def test_next_action_blocked_by_loop_readiness(api: Api) -> None:
    await account(api.container, 1, level=1, paused=True)
    run(api, 1, Act("deed:harvest", {}, "deeds"), ready="paused")
    assert row_of(await get(api))["next_action"]["blocked_by"] == "paused"


async def test_next_action_null_when_engine_not_local(api: Api) -> None:
    await account(api.container, 1, level=1)
    api.engines.reasons[1] = "locked_elsewhere"
    assert row_of(await get(api))["next_action"] is None


@pytest.mark.parametrize("fail", [PlannerUnavailable(), RuntimeError("boom")])
async def test_failed_outlook_gives_null_and_spares_others(api: Api, fail: Exception) -> None:
    first = await account(api.container, 1, level=1)
    second = await account(api.container, name="b", level=1)
    run(api, first, fail=fail)
    run(api, second, Wait(None, "idle"))
    rows = await get(api)
    assert row_of(rows, first)["next_action"] is None
    assert row_of(rows, second)["next_action"]["reason"] == "idle"


async def test_slow_outlook_gives_null_and_spares_others(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(overview_battle, "OUTLOOK_TIMEOUT_S", 0.05)
    first = await account(api.container, 1, level=1)
    second = await account(api.container, name="b", level=1)
    run(api, first, delay=5.0)
    run(api, second, Wait(None, "idle"))
    rows = await get(api)
    slow = row_of(rows, first)
    assert (slow["engine"], slow["next_action"]) == ("local", None)
    assert row_of(rows, second)["next_action"]["reason"] == "idle"


# --- цель на битву ---


async def test_battle_follows_orders_and_profile(api: Api) -> None:
    uid = await owner_of(api.container)
    await account(
        api.container,
        1,
        company="bmesa",
        battle_target=Obs(value=UMBRELLA, at=NOW),
        **seen_battle(B1),
    )
    await api.container.battle_orders.replace(uid, B1, {"all": UMBRELLA}, "admin")
    run(api)
    assert row_of(await get(api))["battle"] == {
        "battle_at": B1.isoformat(),
        "desired": UMBRELLA,
        "source": "order_all",
        "in_game": UMBRELLA,
        "ready": True,
        "applies": True,
        "note": None,
    }


async def test_battle_uses_nearest_battle_of_all_accounts(api: Api) -> None:
    late = await account(api.container, 1, company="bmesa", target=HOOLI, **seen_battle(B2))
    await account(api.container, name="b", company="bmesa", **seen_battle(B1))
    rows = await get(api)
    assert row_of(rows, late)["battle"]["battle_at"] == B1.isoformat()


async def test_battle_null_without_observed_battle(api: Api) -> None:
    await account(api.container, 1, company="bmesa", target=HOOLI)
    assert row_of(await get(api))["battle"] is None


async def test_battle_null_for_account_that_never_saw_one(api: Api) -> None:
    seen = await account(api.container, 1, company="bmesa", **seen_battle(B1))
    blank = await account(api.container, name="b", company="bmesa")
    rows = await get(api)
    assert row_of(rows, seen)["battle"] is not None
    assert row_of(rows, blank)["battle"] is None


async def test_battle_works_without_running_engine(api: Api) -> None:
    await account(api.container, 1, company="bmesa", target=HOOLI, **seen_battle(B1))
    battle = row_of(await get(api))["battle"]
    assert (battle["desired"], battle["applies"], battle["note"]) == (
        HOOLI,
        False,
        "engine_stopped",
    )


async def test_battle_ignores_other_users_orders(api: Api) -> None:
    bob = await make_user(api.container, "bob")
    await account(api.container, 1, company="bmesa", target=HOOLI, **seen_battle(B1))
    await api.container.battle_orders.replace(bob, B1, {"all": UMBRELLA}, "bob")
    run(api)
    assert row_of(await get(api))["battle"]["desired"] == HOOLI

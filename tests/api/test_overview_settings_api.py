from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, update

from app.api.container import Container
from app.db.models import Account, SettingsHistory, User
from app.db.reads import DbReads
from app.engine.facade import EngineFacade
from app.engine.fence import LeaseLost
from app.engine.server_settings import EngineBounds
from app.engine.settings import Settings, SettingsChange, StaticSettings
from tests.api.conftest import Api, login, make_user
from tests.engine.test_facade import build

pytestmark = pytest.mark.db

URL = "/api/v1/overview/settings"
BANANA = {"food": {"banana_reserve": 40}}


class Racing(StaticSettings):
    """Версию двигает чужая запись перед первыми `races` записями фасада."""

    def __init__(self, races: int, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self.races = races

    async def update(
        self, change: SettingsChange, *, changed_by: str, expected_version: int | None = None
    ) -> tuple[Settings, int]:
        if self.races > 0:
            self.races -= 1
            await super().update(lambda s: s, changed_by="engine")
        return await super().update(
            change, changed_by=changed_by, expected_version=expected_version
        )


class Failing(StaticSettings):
    def __init__(self, exc: Exception) -> None:
        super().__init__()
        self._exc = exc

    async def update(
        self, change: SettingsChange, *, changed_by: str, expected_version: int | None = None
    ) -> tuple[Settings, int]:
        raise self._exc


class BrokenNotifier:
    async def notify(self, level: str, code: str, text: str) -> None:
        raise LeaseLost("account lease lost")


async def owner_id(c: Container, name: str = "admin") -> int:
    async with c.db.sessions() as s:
        uid = await s.scalar(select(User.id).where(User.login == name))
    assert uid is not None
    return uid


async def account(c: Container, name: str, owner: str = "admin") -> int:
    return (await c.accounts.create(await owner_id(c, owner), name)).id


def run(api: Api, account_id: int, **kw: Any) -> EngineFacade:
    f = build(**kw)
    api.engines.put(f, account_id)
    return f


async def post(
    api: Api, ids: list[int], changes: dict[str, Any], confirm_live: bool = False
) -> Any:
    return await api.client.post(
        URL,
        headers=api.headers,
        json={"account_ids": ids, "changes": changes, "confirm_live": confirm_live},
    )


def by_id(resp: Any) -> dict[int, dict[str, Any]]:
    return {r["account_id"]: r for r in resp.json()["results"]}


async def db_rows(api: Api, model: Any) -> int:
    async with api.db.sessions() as s:
        return int(await s.scalar(select(func.count()).select_from(model)) or 0)


async def test_mixed_applied_unchanged_and_not_found(api: Api) -> None:
    c = api.container
    a = await account(c, "a")
    b = await account(c, "b")
    gone = await account(c, "gone")
    await c.accounts.mark_deleting(gone)
    await make_user(c, "other")
    foreign = await account(c, "foreign", owner="other")
    sa = StaticSettings()
    sb = StaticSettings(Settings.model_validate({"food": {"banana_reserve": 40}}))
    run(api, a, settings=sa)
    run(api, b, settings=sb)
    ids = [a, b, gone, foreign, 99999]
    resp = await post(api, ids, BANANA)
    assert resp.status_code == 200
    assert [r["account_id"] for r in resp.json()["results"]] == ids
    rows = by_id(resp)
    assert rows[a]["status"] == "applied" and rows[a]["version"] == 1
    assert rows[a]["changed"] == {"food.banana_reserve": [50, 40]}
    assert rows[a]["restart_required"] == [] and rows[a]["error"] is None
    assert rows[b] == {
        "account_id": b,
        "status": "unchanged",
        "version": 0,
        "changed": {},
        "restart_required": [],
        "error": None,
        "warnings": [],
    }
    assert sb.version == 0
    assert rows[gone]["error"] == {"code": "account_deleting", "path": None, "loc": None}
    assert rows[foreign]["error"]["code"] == "account_not_found"
    assert rows[foreign] | {"account_id": 0} == rows[99999] | {"account_id": 0}
    assert sa.current.food.banana_reserve == 40


async def test_version_conflict_is_retried_then_reported(api: Api) -> None:
    c = api.container
    once = await account(c, "once")
    thrice = await account(c, "thrice")
    s_once = Racing(1)
    s_thrice = Racing(3)
    run(api, once, settings=s_once)
    run(api, thrice, settings=s_thrice)
    rows = by_id(await post(api, [once, thrice], BANANA))
    assert rows[once]["status"] == "applied" and rows[once]["version"] == 2
    assert rows[thrice]["status"] == "error"
    assert rows[thrice]["error"]["code"] == "version_conflict"
    assert s_thrice.current.food.banana_reserve == 50


async def test_live_needs_confirmation_before_any_write(api: Api) -> None:
    a = await account(api.container, "a")
    s = StaticSettings()
    run(api, a, settings=s)
    live = {"engine": {"mode": "live"}}
    resp = await post(api, [a], live)
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["loc"] == ["body", "confirm_live"]
    assert s.version == 0
    resp = await post(api, [a], live, confirm_live=True)
    assert resp.status_code == 200 and by_id(resp)[a]["status"] == "applied"
    assert s.current.engine.mode == "live"


async def test_out_of_bounds_on_one_account_does_not_stop_others(api: Api) -> None:
    c = api.container
    strict = await account(c, "strict")
    loose = await account(c, "loose")
    bounds = EngineBounds(min_request_interval_s_min=1.6)
    run(api, strict, bounds=lambda: bounds)
    s_loose = StaticSettings()
    run(api, loose, settings=s_loose)
    rows = by_id(await post(api, [strict, loose], {"engine": {"min_request_interval_s": 1.0}}))
    assert rows[strict]["status"] == "error"
    assert rows[strict]["error"]["code"] == "setting_out_of_bounds"
    assert rows[strict]["error"]["path"] == "engine.min_request_interval_s"
    assert rows[loose]["status"] == "applied"
    assert s_loose.current.engine.min_request_interval_s == 1.0


async def test_chat_is_self_and_lease_lost_are_isolated(api: Api) -> None:
    c = api.container
    me = await account(c, "me")
    lost = await account(c, "lost")
    fine = await account(c, "fine")
    run(api, me, bound_user_id=42)
    run(api, lost, settings=Failing(LeaseLost("lost")))
    s_fine = StaticSettings()
    run(api, fine, settings=s_fine, bound_user_id=7)
    rows = by_id(await post(api, [me, lost, fine], {"chats": {"swinfo_user_id": 42}}))
    assert rows[me]["error"]["code"] == "chat_is_self"
    assert rows[me]["error"]["path"] == "chats.swinfo_user_id"
    assert rows[lost]["error"]["code"] == "lock_lost"
    assert rows[fine]["status"] == "applied"
    assert rows[fine]["restart_required"] == ["chats.swinfo_user_id"]
    assert s_fine.current.chats.swinfo_user_id == 42


async def test_unexpected_failure_is_internal_error(api: Api) -> None:
    c = api.container
    bad = await account(c, "bad")
    fine = await account(c, "fine")
    run(api, bad, settings=Failing(RuntimeError("x")))
    run(api, fine)
    rows = by_id(await post(api, [bad, fine], BANANA))
    assert rows[bad]["error"]["code"] == "internal_error"
    assert rows[fine]["status"] == "applied"


async def test_audit_failure_after_commit_is_applied_with_warning(api: Api) -> None:
    c = api.container
    a = await account(c, "a")
    s = StaticSettings()
    run(api, a, settings=s, notifier=BrokenNotifier())
    rows = by_id(await post(api, [a], {"engine": {"mode": "live"}}, confirm_live=True))
    assert rows[a]["status"] == "applied" and rows[a]["warnings"] == ["audit_failed"]
    assert rows[a]["version"] == 1 and rows[a]["error"] is None
    assert s.current.engine.mode == "live"


async def test_team_chat_equal_to_default_swinfo_is_valid_for_account(api: Api) -> None:
    a = await account(api.container, "a")
    custom = Settings.model_validate({"chats": {"swinfo_chat_id": -1002000000000}})
    run(api, a, settings=StaticSettings(custom))
    default_swinfo = Settings().chats.swinfo_chat_id
    rows = by_id(await post(api, [a], {"chats": {"team_chat_id": default_swinfo}}))
    assert rows[a]["status"] == "applied"
    # Для аккаунта с умолчаниями то же значение нарушает его связь между полями.
    b = await account(api.container, "b")
    run(api, b)
    rows = by_id(await post(api, [b], {"chats": {"team_chat_id": default_swinfo}}))
    assert rows[b]["error"]["code"] == "validation_error"
    assert rows[b]["error"]["loc"] == ["chats", "team_chat_id"]


@pytest.mark.parametrize(
    "changes",
    [
        {"nope": 1},
        {"engine": {"killed": True}},
        {"engine": 3},
        {"engine": {"min_request_interval_s": -5}},
        {"battle": {"target": "nobody"}},
    ],
)
async def test_bad_changes_are_422_before_any_write(api: Api, changes: dict[str, Any]) -> None:
    a = await account(api.container, "a")
    s = StaticSettings()
    run(api, a, settings=s)
    resp = await post(api, [a], changes)
    assert resp.status_code == 422
    assert s.version == 0


async def test_body_limits(api: Api) -> None:
    assert (await post(api, [], BANANA)).status_code == 422
    assert (await post(api, [1, 1], BANANA)).status_code == 422
    assert (await post(api, [1], {})).status_code == 422
    assert (await post(api, list(range(1, 52)), BANANA)).status_code == 422


async def test_stopped_account_with_free_lease_is_written_to_db(api: Api) -> None:
    a = await account(api.container, "a")
    before = await db_rows(api, SettingsHistory)
    start = (await DbReads(api.db, a).settings())[1]
    rows = by_id(await post(api, [a], BANANA))
    assert rows[a]["status"] == "applied" and rows[a]["version"] == start + 1
    assert rows[a]["changed"] == {"food.banana_reserve": [50, 40]}
    assert (await DbReads(api.db, a).settings())[1] == start + 1
    assert await db_rows(api, SettingsHistory) == before + 1
    again = by_id(await post(api, [a], BANANA))
    assert again[a]["status"] == "unchanged" and again[a]["version"] == start + 1
    assert await db_rows(api, SettingsHistory) == before + 1


async def test_held_lease_without_engine_is_engine_starting(api: Api) -> None:
    a = await account(api.container, "a")
    async with api.db.sessions() as s, s.begin():
        await s.execute(
            update(Account)
            .where(Account.id == a)
            .values(lease_holder="host-b", lease_expires_at=func.now() + timedelta(minutes=1))
        )
    api.container.engine_wait_s = 0.05
    before = await db_rows(api, SettingsHistory)
    rows = by_id(await post(api, [a], BANANA))
    assert rows[a]["error"]["code"] == "engine_starting"
    assert await db_rows(api, SettingsHistory) == before


async def test_lists_and_dicts_are_replaced_wholesale(api: Api) -> None:
    a = await account(api.container, "a")
    start = Settings.model_validate({"battle": {"overrides": {13: "🤖Hooli"}}})
    s = StaticSettings(start)
    run(api, a, settings=s)
    rows = by_id(await post(api, [a], {"battle": {"overrides": {"22": "☂️Umbrella"}}}))
    assert rows[a]["status"] == "applied"
    assert s.current.battle.overrides == {22: "☂️Umbrella"}


async def test_user_without_accounts_gets_not_found_for_every_id(api: Api) -> None:
    await make_user(api.container, "empty_user")
    csrf = await login(api.client, login="empty_user")
    resp = await api.client.post(
        URL,
        headers={"X-CSRF-Token": csrf},
        json={"account_ids": [1, 2], "changes": BANANA, "confirm_live": False},
    )
    assert resp.status_code == 200
    assert [r["error"]["code"] for r in resp.json()["results"]] == ["account_not_found"] * 2


async def test_auth_and_csrf_required(api: Api) -> None:
    body = {"account_ids": [1], "changes": BANANA}
    assert (await api.client.post(URL, json=body)).status_code == 403
    api.client.cookies.clear()
    assert (await api.client.post(URL, json=body)).status_code == 401

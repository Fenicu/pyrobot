from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest
from sqlalchemy import select

from app.api.container import Container
from app.db.models import User
from app.engine.facade import EngineFacade, LockLostError
from app.engine.settings import Settings, SettingsChange, SettingsConflict, StaticSettings
from tests.api.conftest import Api, login, make_user
from tests.engine.test_facade import build

pytestmark = pytest.mark.db

URL = "/api/v1/overview/engine"


class Rec:
    def __init__(self) -> None:
        self.items: list[tuple[str, str, str]] = []

    async def notify(self, level: str, code: str, text: str) -> None:
        self.items.append((level, code, text))


class Broken(StaticSettings):
    def __init__(self, exc: Exception) -> None:
        super().__init__()
        self._exc = exc

    async def update(
        self, change: SettingsChange, *, changed_by: str, expected_version: int | None = None
    ) -> tuple[Settings, int]:
        raise self._exc


class DeadNotifier:
    async def notify(self, level: str, code: str, text: str) -> None:
        raise RuntimeError("notifications down")


async def owner_id(c: Container, name: str = "admin") -> int:
    async with c.db.sessions() as s:
        uid = await s.scalar(select(User.id).where(User.login == name))
    assert uid is not None
    return uid


async def account(c: Container, name: str, owner: str = "admin") -> int:
    return (await c.accounts.create(await owner_id(c, owner), name)).id


def run(
    api: Api, account_id: int, *, paused: bool = False, rec: Rec | None = None
) -> EngineFacade:
    settings = StaticSettings(Settings.model_validate({"engine": {"paused": paused}}))
    f = build(settings=settings, notifier=rec)
    api.engines.put(f, account_id)
    return f


async def post(api: Api, action: str, ids: list[int] | None) -> Any:
    return await api.client.post(
        URL, headers=api.headers, json={"action": action, "account_ids": ids}
    )


async def test_pause_mixed_results(api: Api) -> None:
    c = api.container
    rec = Rec()
    done = await account(c, "done")
    same = await account(c, "same")
    away = await account(c, "away")
    lost = await account(c, "lost")
    boom = await account(c, "boom")
    deleting = await account(c, "deleting")
    await c.accounts.mark_deleting(deleting)
    run(api, done, rec=rec)
    run(api, same, paused=True)
    api.engines.reasons[away] = "locked_elsewhere"
    api.engines.put(build(settings=Broken(LockLostError("lease"))), lost)
    api.engines.put(build(settings=Broken(RuntimeError("x"))), boom)
    await make_user(c, "other")
    foreign = await account(c, "foreign", owner="other")

    ids = [done, same, away, lost, boom, deleting, foreign, 99999]
    resp = await post(api, "pause", ids)
    assert resp.status_code == 200
    rows = {r["account_id"]: r for r in resp.json()["results"]}
    assert [r["account_id"] for r in resp.json()["results"]] == ids
    assert rows[done] == {
        "account_id": done,
        "status": "done",
        "reason": None,
        "host": None,
        "warnings": [],
    }
    assert rows[same]["status"] == "unchanged" and rows[same]["reason"] == "already_paused"
    assert rows[away]["status"] == "skipped"
    assert (
        rows[away]["reason"] == "engine_not_running" and rows[away]["host"] == "locked_elsewhere"
    )
    assert rows[lost]["status"] == "error" and rows[lost]["reason"] == "lock_lost"
    assert rows[boom]["status"] == "error" and rows[boom]["reason"] == "internal_error"
    assert rows[deleting]["reason"] == "account_deleting"
    assert rows[foreign]["status"] == "error" and rows[foreign]["reason"] == "account_not_found"
    assert [code for _, code, _ in rec.items] == ["engine_paused"]
    assert "admin" in rec.items[0][2]


async def test_audit_failure_keeps_done_with_warning(api: Api) -> None:
    a = await account(api.container, "a")
    api.engines.put(build(notifier=DeadNotifier()), a)
    row = (await post(api, "pause", [a])).json()["results"][0]
    assert row["status"] == "done" and row["warnings"] == ["audit_failed"]


async def test_settings_conflict_after_retries_is_version_conflict(
    api: Api, caplog: pytest.LogCaptureFixture
) -> None:
    a = await account(api.container, "a")
    api.engines.put(build(settings=Broken(SettingsConflict("moved"))), a)
    resp = await post(api, "pause", [a])
    row = resp.json()["results"][0]
    assert row["status"] == "error" and row["reason"] == "version_conflict"
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_resume_reports_already_running(api: Api) -> None:
    a = await account(api.container, "a")
    b = await account(api.container, "b")
    run(api, a, paused=True)
    run(api, b)
    resp = await post(api, "resume", [a, b])
    rows = {r["account_id"]: r for r in resp.json()["results"]}
    assert rows[a]["status"] == "done"
    assert rows[b]["status"] == "unchanged" and rows[b]["reason"] == "already_running"


async def test_null_ids_mean_enabled_accounts_of_the_owner(api: Api) -> None:
    c = api.container
    on = await account(c, "on")
    off = await account(c, "off")
    gone = await account(c, "gone")
    await c.accounts.set_status(off, "disabled", None)
    await c.accounts.mark_deleting(gone)
    await make_user(c, "other")
    foreign = await account(c, "foreign", owner="other")
    for i in (on, off, gone, foreign):
        run(api, i)
    resp = await post(api, "pause", None)
    assert resp.status_code == 200
    ids = [r["account_id"] for r in resp.json()["results"]]
    assert ids == [1, on]
    assert foreign not in ids


async def test_zero_accounts_gives_empty_results(api: Api) -> None:
    await make_user(api.container, "empty_user")
    csrf = await login(api.client, login="empty_user")
    resp = await api.client.post(
        URL, headers={"X-CSRF-Token": csrf}, json={"action": "pause", "account_ids": None}
    )
    assert resp.status_code == 200 and resp.json() == {"results": []}


async def test_more_than_fifty_ids_is_422(api: Api) -> None:
    assert (await post(api, "pause", list(range(1, 52)))).status_code == 422
    assert (await post(api, "pause", list(range(1, 51)))).status_code == 200


async def test_duplicates_are_removed_keeping_request_order(api: Api) -> None:
    a = await account(api.container, "a")
    run(api, a)
    resp = await post(api, "pause", [a, 1, a, 1])
    assert [r["account_id"] for r in resp.json()["results"]] == [a, 1]


async def test_unknown_action_is_422(api: Api) -> None:
    assert (await post(api, "stop", [1])).status_code == 422


async def test_foreign_and_missing_ids_look_the_same(api: Api) -> None:
    c = api.container
    await make_user(c, "other")
    foreign = await account(c, "foreign", owner="other")
    run(api, foreign)
    resp = await post(api, "pause", [foreign, foreign + 1000])
    first, second = resp.json()["results"]
    assert first | {"account_id": 0} == second | {"account_id": 0}
    assert first["reason"] == "account_not_found"


async def test_auth_and_csrf_required(api: Api) -> None:
    body = {"action": "pause", "account_ids": None}
    assert (await api.client.post(URL, json=body)).status_code == 403
    api.client.cookies.clear()
    assert (await api.client.post(URL, json=body)).status_code == 401


async def test_parallel_pause_requests_change_each_account_once(api: Api) -> None:
    c = api.container
    rec = Rec()
    a = await account(c, "a")
    b = await account(c, "b")
    for i in (a, b):
        run(api, i, rec=rec)
    first, second = await asyncio.gather(post(api, "pause", [a, b]), post(api, "pause", [a, b]))
    statuses: dict[int, list[str]] = {a: [], b: []}
    for resp in (first, second):
        for r in resp.json()["results"]:
            statuses[r["account_id"]].append(r["status"])
    assert all(sorted(v) == ["done", "unchanged"] for v in statuses.values())
    assert [code for _, code, _ in rec.items] == ["engine_paused", "engine_paused"]

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, update

from app.api.app import create_api
from app.db.models import Account, NotificationRow, SettingsHistory, SettingsRow, StateSnapshot
from app.db.settings_store import direct_update
from app.engine.fence import LeaseLost
from app.engine.settings import Settings, StaticSettings
from app.engine.state.model import CharacterState, Obs, dump_state
from app.engine.stream import EventStream
from tests.api.conftest import A1, Api, FakeEngine
from tests.api.sse import read_sse
from tests.engine.helpers import until
from tests.engine.test_facade import build

pytestmark = pytest.mark.db


def _stopped_engine(s: Settings) -> Settings:
    engine = s.engine.model_copy(
        update={"mode": "live", "paused": True, "killed": True, "kill_reason": "руками"}
    )
    return s.model_copy(update={"engine": engine})


async def _rows(api: Api, model: Any) -> int:
    async with api.db.sessions() as s:
        return int(await s.scalar(select(func.count()).select_from(model)) or 0)


async def test_status_from_db_when_not_running(api: Api) -> None:
    await api.container.accounts.set_status(1, "error", "crash_loop:gateway")
    await api.container.accounts.bind_telegram(1, 42)
    await direct_update(api.db, 1, _stopped_engine, changed_by="t", expected_version=None)
    api.engines.reasons[1] = "locked_elsewhere"
    status = (await api.client.get(f"{A1}/engine/status")).json()
    tg = {
        "state": "stopped",
        "user_id": None,
        "attempt_id": None,
        "error": None,
        "bound_user_id": 42,
    }
    assert status == {
        "running": False,
        "status": "error",
        "status_reason": "crash_loop:gateway",
        "host_reason": "locked_elsewhere",
        "mode": "live",
        "paused": True,
        "scenario": None,
        "next_wake": None,
        "killed": True,
        "kill_reason": "руками",
        "spending_blocked": None,
        "tg": tg,
        "queue": 0,
        "in_flight": None,
        "pipeline_backlog": 0,
        "pipeline_healthy": False,
        "workers_ok": False,
        "lock_ok": False,
        "loop_lag_ms": 0.0,
    }
    assert (await api.client.get(f"{A1}/tg/status")).json() == tg
    # Запущенный движок: статус — его, поля аккаунта — из базы.
    api.engines.put(build(authorized=False))
    running = (await api.client.get(f"{A1}/engine/status")).json()
    assert (running["running"], running["status"], running["mode"]) == (True, "error", "dry_run")
    assert running["tg"]["state"] == "unauthorized"


async def test_state_from_snapshot(api: Api) -> None:
    empty = (await api.client.get(f"{A1}/state")).json()
    assert (empty["version"], empty["state"], empty["stale"]) == (0, {}, [])
    seen = datetime.now(UTC) - timedelta(hours=1)
    snapshot = dump_state(CharacterState(money=Obs(value=867, at=seen)))
    async with api.db.sessions() as s, s.begin():
        s.add(StateSnapshot(account_id=1, version=5, state=snapshot))
    body = (await api.client.get(f"{A1}/state")).json()
    assert body["version"] == 5 and body["state"]["money"]["value"] == 867
    assert body["stale"] == ["money"]


async def test_settings_read_and_direct_write(api: Api) -> None:
    body = (await api.client.get(f"{A1}/settings")).json()
    assert body["version"] == 0 and body["values"]["food"]["banana_reserve"] == 50
    patch = {"version": 0, "changes": {"food": {"banana_reserve": 40}}}
    r = await api.client.patch(f"{A1}/settings", headers=api.headers, json=patch)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "version": 1,
        "values": r.json()["values"],
        "changed": {"food.banana_reserve": [50, 40]},
        "restart_required": [],
    }
    async with api.db.sessions() as s:
        row = await s.scalar(select(SettingsRow).where(SettingsRow.account_id == 1))
        history = (await s.scalars(select(SettingsHistory))).all()
    assert row is not None and row.version == 1 and row.data["food"]["banana_reserve"] == 40
    assert [(h.version, h.changed_by) for h in history] == [(1, "admin")]
    assert (await api.client.get(f"{A1}/settings")).json()["version"] == 1
    assert (await api.client.get(f"{A1}/settings/history")).json()["items"][0]["changes"] == {
        "food.banana_reserve": [50, 40]
    }
    # Как и через движок: live — только с подтверждением, смена режима — в уведомлениях.
    live = {"version": 1, "changes": {"engine": {"mode": "live"}}}
    refused = await api.client.patch(f"{A1}/settings", headers=api.headers, json=live)
    assert refused.status_code == 422
    assert refused.json()["detail"][0]["loc"] == ["body", "confirm_live"]
    confirmed = {**live, "confirm_live": True}
    r = await api.client.patch(f"{A1}/settings", headers=api.headers, json=confirmed)
    assert r.status_code == 200 and r.json()["version"] == 2
    async with api.db.sessions() as s:
        codes = (await s.scalars(select(NotificationRow.code))).all()
    assert codes == ["engine_mode"]


async def test_direct_write_version_conflict_409(api: Api) -> None:
    patch = {"version": 0, "changes": {"food": {"banana_reserve": 40}}}
    assert (
        await api.client.patch(f"{A1}/settings", headers=api.headers, json=patch)
    ).status_code == 200
    again = await api.client.patch(f"{A1}/settings", headers=api.headers, json=patch)
    assert again.status_code == 409
    assert again.json() == {"detail": {"code": "version_conflict", "version": 1}}
    assert await _rows(api, SettingsHistory) == 1


async def test_direct_write_refused_for_deleting_account(api: Api) -> None:
    await api.container.accounts.mark_deleting(1)
    patch = {"version": 0, "changes": {"food": {"banana_reserve": 40}}}
    r = await api.client.patch(f"{A1}/settings", headers=api.headers, json=patch)
    assert (r.status_code, r.json()) == (409, {"detail": "account_deleting"})
    # Чтение из базы у удаляемого аккаунта работает.
    status = (await api.client.get(f"{A1}/engine/status")).json()
    assert (status["running"], status["status"]) == (False, "deleting")
    assert (await api.client.get(f"{A1}/settings")).status_code == 200
    assert await _rows(api, SettingsHistory) == 0


async def _hold_lease(api: Api) -> None:
    async with api.db.sessions() as s, s.begin():
        await s.execute(
            update(Account)
            .where(Account.id == 1)
            .values(lease_holder="host-b", lease_expires_at=func.now() + timedelta(minutes=1))
        )


async def test_direct_write_with_active_lease_goes_to_engine(api: Api) -> None:
    await _hold_lease(api)
    settings = StaticSettings()
    api.engines.starting[1] = FakeEngine(build(settings=settings), 1)
    patch = {"version": 0, "changes": {"food": {"banana_reserve": 40}}}
    r = await api.client.patch(f"{A1}/settings", headers=api.headers, json=patch)
    assert r.status_code == 200 and r.json()["version"] == 1
    assert api.engines.waited == [(1, 5.0)]
    assert settings.version == 1 and settings.current.food.banana_reserve == 40
    assert await _rows(api, SettingsRow) == 0 and await _rows(api, SettingsHistory) == 0


async def test_direct_write_with_active_lease_and_no_engine_503(api: Api) -> None:
    await _hold_lease(api)
    api.container.engine_wait_s = 0.05
    patch = {"version": 0, "changes": {"food": {"banana_reserve": 40}}}
    r = await api.client.patch(f"{A1}/settings", headers=api.headers, json=patch)
    assert (r.status_code, r.json()) == (503, {"detail": "engine_starting"})
    assert r.headers["retry-after"] == "2"
    assert api.engines.waited == [(1, 0.05)]
    assert await _rows(api, SettingsRow) == 0


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/engine/kill", {"reason": "r"}),
        ("POST", "/engine/pause", None),
        ("POST", "/tg/login/start", {"phone": "+888"}),
        ("GET", "/planner/outlook", None),
        ("POST", "/commands/send", {"text": "😎Я", "idempotency_key": "k"}),
        ("GET", "/events", None),
    ],
)
async def test_live_routes_503_engine_not_running(
    api: Api, method: str, path: str, body: dict[str, Any] | None
) -> None:
    r = await api.client.request(method, f"{A1}{path}", headers=api.headers, json=body)
    assert (r.status_code, r.json()) == (503, {"detail": "engine not running"})


async def test_lease_lost_in_engine_call_is_503(api: Api) -> None:
    f = build()

    async def lost(**_: object) -> None:
        raise LeaseLost("account 1 lease (epoch 3) lost")

    f.pause = lost  # type: ignore[method-assign]
    api.engines.put(f)
    r = await api.client.post(f"{A1}/engine/pause", headers=api.headers)
    assert (r.status_code, r.json()) == (503, {"detail": "engine not running"})


async def test_sse_streams_only_own_account(api: Api) -> None:
    admin = await api.container.auth.get_admin("admin")
    assert admin is not None
    second = await api.container.accounts.create(admin.id, "Второй", capacity=10)
    own = api.engines.put(build(), 1).stream
    other = api.engines.put(build(), second.id).stream
    assert own is not other and isinstance(own, EventStream)

    async def publish() -> None:
        await until(lambda: own.subscribers == 1, 2.0)
        other.publish("notification", {"code": "чужое"})
        own.publish("notification", {"code": "своё"})

    task = asyncio.create_task(publish())
    cookie = {"cookie": f"pyrobot_session={api.client.cookies['pyrobot_session']}"}
    status, events = await read_sse(
        create_api(api.container), f"{A1}/events", headers=cookie, count=2
    )
    await task
    assert status == 200
    assert [(e.event, e.data) for e in events] == [
        ("reset", {"reason": "new"}),
        ("notification", {"code": "своё"}),
    ]

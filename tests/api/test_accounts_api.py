from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import insert, select

from app.db.accounts import AccountInfo
from app.db.models import Account, ActionRow, NotificationRow
from app.db.settings_store import direct_update
from app.engine.settings import Settings, StaticSettings
from tests.api.conftest import A1, Api, run_engine
from tests.engine.test_facade import build

pytestmark = pytest.mark.db
ACCOUNTS = "/api/v1/accounts"
TG_USER = 267519921


async def _admin_id(api: Api, login: str = "admin") -> int:
    admin = await api.container.auth.get_admin(login)
    assert admin is not None
    return admin.id


async def _second(api: Api, name: str = "Второй") -> AccountInfo:
    return await api.container.accounts.create(await _admin_id(api), name, capacity=10)


async def _foreign(api: Api) -> int:
    await api.container.auth.ensure_admin("other", "other horse battery")
    account = await api.container.accounts.create(
        await _admin_id(api, "other"), "Чужой", capacity=10
    )
    return account.id


async def _notify(api: Api, account_id: int, level: str, *, read: bool = False) -> None:
    async with api.db.sessions() as s, s.begin():
        await s.execute(
            insert(NotificationRow).values(
                account_id=account_id, level=level, code="c", text="t", read=read
            )
        )


async def _action(api: Api, account_id: int, created_at: datetime) -> None:
    async with api.db.sessions() as s, s.begin():
        await s.execute(
            insert(ActionRow).values(
                account_id=account_id,
                created_at=created_at,
                source="manual",
                kind="send",
                chat_id=1,
                payload={},
                command_class="free",
                status="confirmed",
            )
        )


async def _generation(api: Api, account_id: int) -> int:
    async with api.db.sessions() as s:
        value = await s.scalar(select(Account.engine_generation).where(Account.id == account_id))
    assert value is not None
    return value


def _killed(s: Settings) -> Settings:
    engine = s.engine.model_copy(update={"mode": "live", "killed": True, "kill_reason": "руками"})
    return s.model_copy(update={"engine": engine})


def _listed(
    account_id: int,
    name: str,
    *,
    status: str = "enabled",
    tg: dict[str, Any] | None = None,
    mode: str = "dry_run",
    paused: bool = False,
    killed: bool = False,
    last_action_at: str | None = None,
    unread: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "id": account_id,
        "name": name,
        "status": status,
        "status_reason": None,
        "tg": tg or {"user_id": None, "online": False},
        "mode": mode,
        "paused": paused,
        "killed": killed,
        "last_action_at": last_action_at,
        "unread": unread or {"warn": 0, "error": 0},
    }


async def test_list_only_own_accounts_with_live_fields(api: Api) -> None:
    foreign = await _foreign(api)
    second = await _second(api)
    # Аккаунт 1 запущен и в онлайне; пауза — у движка (в базе её нет).
    await api.container.accounts.bind_telegram(1, TG_USER)
    facade = build(authorized=True, settings=StaticSettings(), bound_user_id=TG_USER)
    await facade.tg.boot()
    await facade.pause(by="t")
    run_engine(api.container, facade)
    # Второй не запущен: режим и kill — из настроек в базе.
    await direct_update(api.db, second.id, _killed, changed_by="t", expected_version=None)
    for level, read in (("warn", False), ("warn", False), ("warn", True), ("error", False)):
        await _notify(api, 1, level, read=read)
    await _notify(api, 1, "info")
    await _notify(api, foreign, "error")
    await _action(api, 1, datetime(2026, 9, 30, 10, 0, tzinfo=UTC))
    await _action(api, 1, datetime(2026, 9, 30, 12, 0, tzinfo=UTC))
    await _action(api, foreign, datetime(2026, 10, 1, 12, 0, tzinfo=UTC))
    r = await api.client.get(ACCOUNTS)
    assert r.status_code == 200
    assert r.json() == [
        _listed(
            1,
            "Основной",
            tg={"user_id": TG_USER, "online": True},
            paused=True,
            last_action_at="2026-09-30T12:00:00+00:00",
            unread={"warn": 2, "error": 1},
        ),
        _listed(second.id, "Второй", mode="live", killed=True),
    ]
    api.client.cookies.clear()
    assert (await api.client.get(ACCOUNTS)).status_code == 401


async def test_create_201_enabled_dry_run_and_starts(api: Api) -> None:
    body = {"name": "  Второй  "}
    assert (await api.client.post(ACCOUNTS, json=body)).status_code == 403
    r = await api.client.post(ACCOUNTS, headers=api.headers, json=body)
    assert r.status_code == 201
    created = r.json()
    assert created == _listed(created["id"], "Второй")
    # Хост разбужен: новый аккаунт `enabled`, его движок поднимет сверка.
    assert api.engines.pokes == 1
    account = await api.container.accounts.get(created["id"])
    assert account is not None and account.status == "enabled"
    listed = (await api.client.get(ACCOUNTS)).json()
    assert [a["id"] for a in listed] == [1, created["id"]]
    for bad in ({"name": "   "}, {"name": "x" * 65}, {}):
        r = await api.client.post(ACCOUNTS, headers=api.headers, json=bad)
        assert r.status_code == 422, bad
    assert api.engines.pokes == 1


async def test_create_name_taken_and_capacity_reached(api: Api) -> None:
    r = await api.client.post(ACCOUNTS, headers=api.headers, json={"name": "Основной"})
    assert (r.status_code, r.json()) == (409, {"detail": "name_taken"})
    api.container.config.max_engines = 2
    r = await api.client.post(ACCOUNTS, headers=api.headers, json={"name": "Второй"})
    assert r.status_code == 201
    r = await api.client.post(ACCOUNTS, headers=api.headers, json={"name": "Третий"})
    assert (r.status_code, r.json()) == (409, {"detail": "capacity_reached"})
    assert [a["name"] for a in (await api.client.get(ACCOUNTS)).json()] == [
        "Основной",
        "Второй",
    ]
    assert api.engines.pokes == 1


async def test_patch_rename_enable_disable(api: Api) -> None:
    second = await _second(api)
    path = f"{ACCOUNTS}/{second.id}"
    assert (await api.client.patch(path, json={"name": "Новый"})).status_code == 403
    r = await api.client.patch(path, headers=api.headers, json={"name": "Новый"})
    assert r.status_code == 200 and r.json() == _listed(second.id, "Новый")
    r = await api.client.patch(path, headers=api.headers, json={"enabled": False})
    assert r.status_code == 200 and r.json()["status"] == "disabled"
    assert api.engines.pokes == 2
    r = await api.client.patch(path, headers=api.headers, json={"name": "Основной"})
    assert (r.status_code, r.json()) == (409, {"detail": "name_taken"})
    # Ёмкость занята аккаунтом 1: включить второй нельзя.
    api.container.config.max_engines = 1
    r = await api.client.patch(path, headers=api.headers, json={"enabled": True})
    assert (r.status_code, r.json()) == (409, {"detail": "capacity_reached"})
    api.container.config.max_engines = 2
    r = await api.client.patch(path, headers=api.headers, json={"enabled": True})
    assert r.status_code == 200 and r.json()["status"] == "enabled"
    assert api.engines.pokes == 3
    # Удаляемый аккаунт не правится: `deleting` конечный.
    await api.container.accounts.mark_deleting(second.id)
    for body in ({"name": "Другой"}, {"enabled": True}, {"enabled": False}):
        r = await api.client.patch(path, headers=api.headers, json=body)
        assert (r.status_code, r.json()) == (409, {"detail": "account_deleting"}), body
    account = await api.container.accounts.get(second.id)
    assert account is not None and (account.name, account.status) == ("Новый", "deleting")


async def test_delete_requires_exact_name(api: Api) -> None:
    for body in ({"confirm_name": "основной"}, {"confirm_name": "Основной "}):
        r = await api.client.request("DELETE", A1, headers=api.headers, json=body)
        assert (r.status_code, r.json()) == (422, {"detail": "confirm_name_mismatch"}), body
    assert (await api.client.request("DELETE", A1, headers=api.headers)).status_code == 422
    account = await api.container.accounts.get(1)
    assert account is not None and account.status == "enabled"
    assert api.engines.pokes == 0


async def test_delete_202_then_gone(api: Api) -> None:
    second = await _second(api)
    path = f"{ACCOUNTS}/{second.id}"
    body = {"confirm_name": "Второй"}
    assert (await api.client.request("DELETE", path, json=body)).status_code == 403
    r = await api.client.request("DELETE", path, headers=api.headers, json=body)
    assert r.status_code == 202 and r.content == b""
    assert api.engines.pokes == 1
    # До чистки аккаунт виден со статусом deleting, настройки не правятся.
    listed = (await api.client.get(ACCOUNTS)).json()
    assert [(a["id"], a["status"]) for a in listed] == [(1, "enabled"), (second.id, "deleting")]
    patch = {"version": 0, "changes": {"food": {"banana_reserve": 40}}}
    r = await api.client.patch(f"{path}/settings", headers=api.headers, json=patch)
    assert (r.status_code, r.json()) == (409, {"detail": "account_deleting"})
    # Чистка (её делает хост движков).
    await api.container.accounts.purge(second.id)
    assert [a["id"] for a in (await api.client.get(ACCOUNTS)).json()] == [1]
    r = await api.client.get(f"{path}/state")
    assert (r.status_code, r.json()) == (404, {"detail": "account not found"})
    r = await api.client.request("DELETE", path, headers=api.headers, json=body)
    assert r.status_code == 404


async def test_restart_bumps_generation_503_when_not_running(api: Api) -> None:
    path = f"{A1}/engine/restart"
    r = await api.client.post(path, headers=api.headers)
    assert (r.status_code, r.json()) == (503, {"detail": "engine not running"})
    assert await _generation(api, 1) == 0 and api.engines.pokes == 0
    run_engine(api.container, build())
    assert (await api.client.post(path)).status_code == 403
    r = await api.client.post(path, headers=api.headers)
    assert r.status_code == 202 and r.content == b""
    assert await _generation(api, 1) == 1 and api.engines.pokes == 1


async def test_restart_refused_for_deleting_account(api: Api) -> None:
    # Движок удаляемого аккаунта ещё зарегистрирован (хост его останавливает) и без него.
    path = f"{A1}/engine/restart"
    run_engine(api.container, build())
    await api.container.accounts.mark_deleting(1)
    r = await api.client.post(path, headers=api.headers)
    assert (r.status_code, r.json()) == (409, {"detail": "account_deleting"})
    api.engines.engines.clear()
    r = await api.client.post(path, headers=api.headers)
    assert (r.status_code, r.json()) == (409, {"detail": "account_deleting"})
    assert await _generation(api, 1) == 0 and api.engines.pokes == 0


async def test_host_status(api: Api) -> None:
    run_engine(api.container, build())
    api.engines.reasons[2] = "locked_elsewhere"
    r = await api.client.get("/api/v1/host/status")
    assert r.status_code == 200
    assert r.json() == {
        "holder": "test-host",
        "lock_connection_ok": True,
        "engines": [1],
        "busy": {"2": "locked_elsewhere"},
        "loop_lag_ms": 0.0,
        "tasks_ok": True,
    }
    api.client.cookies.clear()
    assert (await api.client.get("/api/v1/host/status")).status_code == 401


@pytest.mark.parametrize("account_id", ["0", "-1", "2147483648", "99999999999999999999"])
async def test_account_id_outside_int4_is_404(api: Api, account_id: str) -> None:
    r = await api.client.get(f"{ACCOUNTS}/{account_id}/state")
    assert (r.status_code, r.json()) == (404, {"detail": "account not found"})

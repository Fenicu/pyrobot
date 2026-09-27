import pytest
from httpx import AsyncClient

from app.api.container import Container
from app.api.errors import VersionConflictOut
from app.db.base import Database
from app.db.models import SettingsHistory, SettingsRow
from app.db.settings_store import DbSettingsStore
from app.engine.settings import Settings
from tests.api.conftest import login
from tests.engine.test_facade import build

pytestmark = pytest.mark.db


@pytest.fixture
async def with_settings(container: Container, clean_db: Database) -> Container:
    store = DbSettingsStore(clean_db, 1)
    await store.load()
    container.facade = build(settings=store)
    return container


async def _csrf(client: AsyncClient) -> dict[str, str]:
    return {"X-CSRF-Token": await login(client)}


async def test_get_settings_schema_values_defaults(
    with_settings: Container, api_client: AsyncClient
) -> None:
    assert (await api_client.get("/api/v1/settings")).status_code == 401
    await login(api_client)
    body = (await api_client.get("/api/v1/settings")).json()
    assert body["version"] == 0
    assert body["values"]["engine"]["mode"] == "dry_run"
    assert body["defaults"]["engine"]["min_request_interval_s"] == 1.6
    engine = body["schema"]["$defs"]["EngineSection"]["properties"]
    assert engine["killed"]["readOnly"] is True


async def test_patch_requires_csrf_and_bumps_version(
    with_settings: Container, api_client: AsyncClient
) -> None:
    h = await _csrf(api_client)
    patch = {"version": 0, "changes": {"food": {"banana_reserve": 40}}}
    assert (await api_client.patch("/api/v1/settings", json=patch)).status_code == 403
    r = await api_client.patch("/api/v1/settings", headers=h, json=patch)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == 1 and body["values"]["food"]["banana_reserve"] == 40
    assert body["changed"] == {"food.banana_reserve": [50, 40]}
    assert body["restart_required"] == []
    again = await api_client.patch("/api/v1/settings", headers=h, json=patch)
    assert again.status_code == 409
    assert again.json()["detail"] == {"code": "version_conflict", "version": 1}
    VersionConflictOut.model_validate(again.json())


async def test_patch_errors_are_422_with_location(
    with_settings: Container, api_client: AsyncClient
) -> None:
    h = await _csrf(api_client)
    unknown = {"version": 0, "changes": {"engine": {"moed": "live"}}}
    r = await api_client.patch("/api/v1/settings", headers=h, json=unknown)
    assert r.status_code == 422
    assert r.json()["detail"][0]["loc"] == ["body", "changes", "engine", "moed"]
    assert r.json()["detail"][0]["type"] == "unknown_field"
    ro = {"version": 0, "changes": {"engine": {"killed": False}}}
    r = await api_client.patch("/api/v1/settings", headers=h, json=ro)
    assert r.status_code == 422 and r.json()["detail"][0]["type"] == "read_only"
    bad = {"version": 0, "changes": {"sleep": {"duration_h": 13}}}
    r = await api_client.patch("/api/v1/settings", headers=h, json=bad)
    assert r.status_code == 422
    assert r.json()["detail"][0]["loc"] == ["body", "changes", "sleep", "duration_h"]
    assert (await api_client.get("/api/v1/settings")).json()["version"] == 0


async def test_live_needs_explicit_confirm(
    with_settings: Container, api_client: AsyncClient
) -> None:
    h = await _csrf(api_client)
    live = {"version": 0, "changes": {"engine": {"mode": "live"}}}
    r = await api_client.patch("/api/v1/settings", headers=h, json=live)
    assert r.status_code == 422
    assert r.json()["detail"][0] == {
        "loc": ["body", "confirm_live"],
        "msg": "live_requires_confirm",
        "type": "live_requires_confirm",
    }
    ok = await api_client.patch("/api/v1/settings", headers=h, json={**live, "confirm_live": True})
    assert ok.status_code == 200 and ok.json()["values"]["engine"]["mode"] == "live"
    status = (await api_client.get("/api/v1/engine/status")).json()
    assert status["mode"] == "live"


async def test_restart_required_listed(with_settings: Container, api_client: AsyncClient) -> None:
    h = await _csrf(api_client)
    patch = {"version": 0, "changes": {"chats": {"bulls_invite_chat_id": -100123}}}
    r = await api_client.patch("/api/v1/settings", headers=h, json=patch)
    assert r.json()["restart_required"] == ["chats.bulls_invite_chat_id"]


async def test_history_pages_with_diffs(with_settings: Container, api_client: AsyncClient) -> None:
    h = await _csrf(api_client)
    for version, reserve in enumerate((40, 30, 20)):
        patch = {"version": version, "changes": {"food": {"banana_reserve": reserve}}}
        assert (await api_client.patch("/api/v1/settings", headers=h, json=patch)).is_success
    page = (await api_client.get("/api/v1/settings/history", params={"limit": 2})).json()
    assert [i["version"] for i in page["items"]] == [3, 2]
    assert page["items"][0]["changes"] == {"food.banana_reserve": [30, 20]}
    assert page["items"][0]["changed_by"] == "admin"
    assert page["next_before"] == 2
    rest = await api_client.get("/api/v1/settings/history", params={"limit": 2, "before": 2})
    items = rest.json()["items"]
    assert [i["version"] for i in items] == [1]
    assert items[0]["changes"] == {"food.banana_reserve": [50, 40]}
    assert rest.json()["next_before"] is None
    whole = (await api_client.get("/api/v1/settings/history", params={"limit": 3})).json()
    assert [i["version"] for i in whole["items"]] == [3, 2, 1]
    assert whole["next_before"] is None


async def test_history_ignores_sections_missing_in_old_versions(
    container: Container, clean_db: Database, api_client: AsyncClient
) -> None:
    # Версия, записанная до появления секции retention, не даёт ложного изменения.
    old = Settings().model_dump(mode="json")
    del old["retention"]
    new = Settings().model_dump(mode="json")
    new["food"]["banana_reserve"] = 40
    async with clean_db.sessions() as s, s.begin():
        s.add(SettingsHistory(account_id=1, version=1, data=old, changed_by="admin"))
        s.add(SettingsHistory(account_id=1, version=2, data=new, changed_by="admin"))
    await login(api_client)
    items = (await api_client.get("/api/v1/settings/history")).json()["items"]
    assert [(i["version"], i["changes"]) for i in items] == [
        (2, {"food.banana_reserve": [50, 40]}),
        (1, {}),
    ]


def _prod_version(weight_xp: float) -> dict[str, object]:
    """Версия настроек прода до основных дел: с `strategy.weight_team`, без `strategy.focus`,
    явный список дел без прогулки и выключенные задания."""
    data = Settings().model_dump(mode="json")
    strategy = data["strategy"]
    del strategy["focus"]
    strategy.update(
        weight_team=0.5, weight_xp=weight_xp, deeds=["harvest", "job", "learn", "dconv"]
    )
    data["features"]["daily_tasks"] = False
    return data


async def test_prod_settings_with_weight_team_load_patch_and_history(
    container: Container, clean_db: Database, api_client: AsyncClient
) -> None:
    v1, v2 = _prod_version(1.0), _prod_version(2.0)
    async with clean_db.sessions() as s, s.begin():
        s.add(SettingsRow(account_id=1, version=2, data=v2))
        s.add(SettingsHistory(account_id=1, version=1, data=v1, changed_by="admin"))
        s.add(SettingsHistory(account_id=1, version=2, data=v2, changed_by="admin"))
    store = DbSettingsStore(clean_db, 1)
    await store.load()
    container.facade = build(settings=store)
    h = await _csrf(api_client)
    got = (await api_client.get("/api/v1/settings")).json()
    assert got["values"]["strategy"]["deeds"] == ["harvest", "job", "learn", "dconv"]
    assert got["values"]["strategy"]["focus"] == ["harvest", "dconv"]
    assert "weight_team" not in got["values"]["strategy"]
    patch = {"version": 2, "changes": {"food": {"banana_reserve": 40}}}
    r = await api_client.patch("/api/v1/settings", headers=h, json=patch)
    assert r.status_code == 200, r.text
    assert r.json()["changed"] == {"food.banana_reserve": [50, 40]}
    items = (await api_client.get("/api/v1/settings/history")).json()["items"]
    assert [(i["version"], i["changes"]) for i in items] == [
        (3, {"food.banana_reserve": [50, 40]}),
        (2, {"strategy.weight_xp": [1.0, 2.0]}),
        # Первая версия — к нынешним умолчаниям: список дел и флаг заданий у неё свои.
        (
            1,
            {
                "features.daily_tasks": [True, False],
                "strategy.deeds": [
                    ["harvest", "job", "learn", "dconv", "walk"],
                    ["harvest", "job", "learn", "dconv"],
                ],
            },
        ),
    ]


def _prod_v020() -> dict[str, object]:
    """Полный дамп настроек прода v0.2.0: явный `features.lottery=false`, без флага
    `robbery_defense` и без секции `lottery`."""
    data = Settings().model_dump(mode="json")
    data["features"]["lottery"] = False
    del data["features"]["robbery_defense"]
    del data["lottery"]
    return data


async def test_prod_v020_settings_load_patch_and_history(
    container: Container, clean_db: Database, api_client: AsyncClient
) -> None:
    prod = _prod_v020()
    async with clean_db.sessions() as s, s.begin():
        s.add(SettingsRow(account_id=1, version=1, data=prod))
        s.add(SettingsHistory(account_id=1, version=1, data=prod, changed_by="admin"))
    store = DbSettingsStore(clean_db, 1)
    await store.load()
    container.facade = build(settings=store)
    h = await _csrf(api_client)
    got = (await api_client.get("/api/v1/settings")).json()
    features = got["values"]["features"]
    assert (features["lottery"], features["robbery_defense"]) == (False, True)
    assert got["values"]["lottery"] == {
        "tickets": {"money": "max", "knowledge": "max", "raw": "max", "details": "max"},
        "keep": {"money": 0, "knowledge": 0, "raw": 0, "details": 0},
    }
    patch = {
        "version": 1,
        "changes": {"features": {"lottery": True}, "lottery": {"keep": {"money": 300}}},
    }
    r = await api_client.patch("/api/v1/settings", headers=h, json=patch)
    assert r.status_code == 200, r.text
    assert r.json()["changed"] == {
        "features.lottery": [False, True],
        "lottery.keep.money": [0, 300],
    }
    bad = {"version": 2, "changes": {"lottery": {"tickets": {"money": "all"}}}}
    assert (await api_client.patch("/api/v1/settings", headers=h, json=bad)).status_code == 422
    items = (await api_client.get("/api/v1/settings/history")).json()["items"]
    assert [(i["version"], i["changes"]) for i in items] == [
        (2, {"features.lottery": [False, True], "lottery.keep.money": [0, 300]}),
        # Первая версия — к нынешним умолчаниям: флаг лотереи у неё явный.
        (1, {"features.lottery": [True, False]}),
    ]


async def test_settings_need_engine(container: Container, api_client: AsyncClient) -> None:
    await login(api_client)
    assert (await api_client.get("/api/v1/settings")).status_code == 503
    assert (await api_client.get("/api/v1/settings/history")).status_code == 200

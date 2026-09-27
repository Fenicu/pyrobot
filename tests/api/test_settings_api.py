import pytest
from httpx import AsyncClient

from app.api.container import Container
from app.db.base import Database
from app.db.models import SettingsHistory
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


async def test_settings_need_engine(container: Container, api_client: AsyncClient) -> None:
    await login(api_client)
    assert (await api_client.get("/api/v1/settings")).status_code == 503
    assert (await api_client.get("/api/v1/settings/history")).status_code == 200

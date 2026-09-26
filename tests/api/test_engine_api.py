import pytest
from httpx import AsyncClient

from app.api.container import Container
from tests.api.conftest import login
from tests.engine.test_facade import build

pytestmark = pytest.mark.db


@pytest.fixture
async def with_facade(container: Container) -> Container:
    container.facade = build(authorized=False)
    await container.facade.tg.boot()
    return container


async def test_engine_status_kill_unkill(with_facade: Container, api_client: AsyncClient) -> None:
    csrf = await login(api_client)
    st = await api_client.get("/api/v1/engine/status")
    assert st.status_code == 200 and st.json()["mode"] == "dry_run"
    assert st.json()["tg"]["state"] == "unauthorized"
    h = {"X-CSRF-Token": csrf}
    assert (await api_client.post("/api/v1/engine/kill", json={"reason": "r"})).status_code == 403
    assert (
        await api_client.post("/api/v1/engine/kill", headers=h, json={"reason": "r"})
    ).status_code == 204
    assert (await api_client.get("/api/v1/engine/status")).json()["killed"] is True
    assert (await api_client.post("/api/v1/engine/unkill", headers=h)).status_code == 204
    assert (await api_client.post("/api/v1/engine/reconciled", headers=h)).status_code == 204


async def test_tg_login_flow_and_readyz(with_facade: Container, api_client: AsyncClient) -> None:
    assert (await api_client.get("/readyz")).status_code == 503
    csrf = await login(api_client)
    h = {"X-CSRF-Token": csrf}
    assert (await api_client.get("/api/v1/tg/status")).json()["state"] == "unauthorized"
    start = await api_client.post("/api/v1/tg/login/start", headers=h, json={"phone": "+888"})
    attempt = start.json()["attempt_id"]
    wrong = await api_client.post(
        "/api/v1/tg/login/code", headers=h, json={"attempt_id": "nope", "code": "12345"}
    )
    assert wrong.status_code == 409
    ok = await api_client.post(
        "/api/v1/tg/login/code", headers=h, json={"attempt_id": attempt, "code": "12345"}
    )
    assert ok.json()["state"] == "online"
    assert (await api_client.get("/readyz")).status_code == 200

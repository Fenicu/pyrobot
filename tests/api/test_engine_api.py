import pytest
from httpx import AsyncClient

from app.api.container import Container
from app.engine.tg_auth import InvalidPhone
from app.engine.transport.fake import FakeTgBackend
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
    empty = await api_client.post("/api/v1/engine/kill", headers=h, json={"reason": ""})
    assert empty.status_code == 422


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


class _SendCodeDown(FakeTgBackend):
    async def send_code(self, phone: str) -> str:
        raise ConnectionError("network down")


class _BadPhone(FakeTgBackend):
    async def send_code(self, phone: str) -> str:
        raise InvalidPhone


async def test_tg_backend_failure_is_502(container: Container, api_client: AsyncClient) -> None:
    container.facade = build(authorized=False, backend=_SendCodeDown())
    h = {"X-CSRF-Token": await login(api_client)}
    r = await api_client.post("/api/v1/tg/login/start", headers=h, json={"phone": "+888"})
    assert r.status_code == 502 and r.json() == {"detail": "send_code_failed"}
    st = (await api_client.get("/api/v1/tg/status")).json()
    assert st["state"] == "error" and st["error"] == "send_code_failed"


async def test_tg_classified_error_is_400(container: Container, api_client: AsyncClient) -> None:
    container.facade = build(authorized=False, backend=_BadPhone())
    h = {"X-CSRF-Token": await login(api_client)}
    r = await api_client.post("/api/v1/tg/login/start", headers=h, json={"phone": "+888"})
    assert r.status_code == 400 and r.json() == {"detail": "invalid_phone"}

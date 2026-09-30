import pytest
from httpx import AsyncClient

from app.api.container import Container
from app.engine.tg_auth import InvalidPhone, SendCodeRejected
from app.engine.transport.base import FloodWait
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
    assert (await api_client.post("/api/v1/engine/pause")).status_code == 403
    assert (await api_client.post("/api/v1/engine/pause", headers=h)).status_code == 204
    assert (await api_client.get("/api/v1/engine/status")).json()["paused"] is True
    assert (await api_client.post("/api/v1/engine/resume", headers=h)).status_code == 204
    assert (await api_client.get("/api/v1/engine/status")).json()["paused"] is False
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


class _FloodWaitOnSendCode(FakeTgBackend):
    async def send_code(self, phone: str) -> str:
        raise FloodWait(30)


class _RejectedOnSendCode(FakeTgBackend):
    async def send_code(self, phone: str) -> str:
        raise SendCodeRejected("phone_number_banned")


async def test_tg_send_code_flood_wait_is_429(
    container: Container, api_client: AsyncClient
) -> None:
    container.facade = build(authorized=False, backend=_FloodWaitOnSendCode())
    h = {"X-CSRF-Token": await login(api_client)}
    r = await api_client.post("/api/v1/tg/login/start", headers=h, json={"phone": "+888"})
    assert r.status_code == 429 and r.json() == {"detail": "flood_wait"}
    assert r.headers["retry-after"] == "31"
    st = (await api_client.get("/api/v1/tg/status")).json()
    assert st["state"] == "error" and st["error"] == "flood_wait"


async def test_tg_send_code_rejected_is_400_with_rpc_code(
    container: Container, api_client: AsyncClient
) -> None:
    container.facade = build(authorized=False, backend=_RejectedOnSendCode())
    h = {"X-CSRF-Token": await login(api_client)}
    r = await api_client.post("/api/v1/tg/login/start", headers=h, json={"phone": "+888"})
    assert r.status_code == 400 and r.json() == {"detail": "phone_number_banned"}


async def test_tg_status_reports_binding(container: Container, api_client: AsyncClient) -> None:
    container.facade = build(authorized=False, bound_user_id=None)
    await container.facade.tg.boot()
    h = {"X-CSRF-Token": await login(api_client)}
    assert (await api_client.get("/api/v1/tg/status")).json()["bound_user_id"] is None
    start = await api_client.post("/api/v1/tg/login/start", headers=h, json={"phone": "+888"})
    code = await api_client.post(
        "/api/v1/tg/login/code",
        headers=h,
        json={"attempt_id": start.json()["attempt_id"], "code": "12345"},
    )
    # Первый вход привязывает аккаунт, и привязка видна в статусах.
    assert code.json()["bound_user_id"] == 267519921
    st = (await api_client.get("/api/v1/tg/status")).json()
    assert st["bound_user_id"] == 267519921
    assert (await api_client.get("/api/v1/engine/status")).json()["tg"] == st

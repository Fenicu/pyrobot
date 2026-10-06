import asyncio
from dataclasses import replace

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.app import create_api
from app.api.container import Container
from app.db.base import Database
from app.engine.host.codes import CodeLimiter
from app.engine.tg_auth import InvalidPhone, SendCodeRejected, SentCodeInfo
from app.engine.transport.base import FloodWait, TransportAuthLost, TransportRejected
from app.engine.transport.fake import FakeTgBackend, FakeTransport
from app.logctx import current_account
from tests.api.conftest import A1, Api, engines, login, run_engine
from tests.engine.test_facade import Watch, build
from tests.engine.test_fence import FakeMonotonic

pytestmark = pytest.mark.db


@pytest.fixture
async def with_facade(container: Container) -> Container:
    f = build(authorized=False)
    run_engine(container, f)
    await f.tg.boot()
    return container


async def test_engine_status_kill_unkill(with_facade: Container, api_client: AsyncClient) -> None:
    csrf = await login(api_client)
    st = await api_client.get("/api/v1/accounts/1/engine/status")
    assert st.status_code == 200 and st.json()["mode"] == "dry_run"
    assert st.json()["tg"]["state"] == "unauthorized"
    h = {"X-CSRF-Token": csrf}
    assert (
        await api_client.post("/api/v1/accounts/1/engine/kill", json={"reason": "r"})
    ).status_code == 403
    assert (
        await api_client.post("/api/v1/accounts/1/engine/kill", headers=h, json={"reason": "r"})
    ).status_code == 204
    assert (await api_client.get("/api/v1/accounts/1/engine/status")).json()["killed"] is True
    assert (
        await api_client.post("/api/v1/accounts/1/engine/unkill", headers=h)
    ).status_code == 204
    assert (
        await api_client.post("/api/v1/accounts/1/engine/reconciled", headers=h)
    ).status_code == 204
    assert (await api_client.post("/api/v1/accounts/1/engine/pause")).status_code == 403
    assert (await api_client.post("/api/v1/accounts/1/engine/pause", headers=h)).status_code == 204
    assert (await api_client.get("/api/v1/accounts/1/engine/status")).json()["paused"] is True
    assert (
        await api_client.post("/api/v1/accounts/1/engine/resume", headers=h)
    ).status_code == 204
    assert (await api_client.get("/api/v1/accounts/1/engine/status")).json()["paused"] is False
    empty = await api_client.post("/api/v1/accounts/1/engine/kill", headers=h, json={"reason": ""})
    assert empty.status_code == 422


async def test_tg_login_flow(with_facade: Container, api_client: AsyncClient) -> None:
    csrf = await login(api_client)
    h = {"X-CSRF-Token": csrf}
    assert (await api_client.get("/api/v1/accounts/1/tg/status")).json()["state"] == "unauthorized"
    start = await api_client.post(
        "/api/v1/accounts/1/tg/login/start", headers=h, json={"phone": "+888"}
    )
    attempt = start.json()["attempt_id"]
    wrong = await api_client.post(
        "/api/v1/accounts/1/tg/login/code", headers=h, json={"attempt_id": "nope", "code": "12345"}
    )
    assert wrong.status_code == 409
    ok = await api_client.post(
        "/api/v1/accounts/1/tg/login/code",
        headers=h,
        json={"attempt_id": attempt, "code": "12345"},
    )
    assert ok.json()["state"] == "online"


async def test_tg_login_resend_code(container: Container, api_client: AsyncClient) -> None:
    backend = FakeTgBackend(
        sent_code_info=SentCodeInfo(phone_code_hash="h3", type="app", next_type="sms", timeout=60)
    )
    run_engine(container, build(authorized=False, backend=backend))
    csrf = await login(api_client)
    h = {"X-CSRF-Token": csrf}
    start = await api_client.post(
        "/api/v1/accounts/1/tg/login/start", headers=h, json={"phone": "+888"}
    )
    attempt = start.json()["attempt_id"]
    assert start.json()["delivery_type"] == "app"
    assert start.json()["delivery_next_type"] == "sms"
    assert start.json()["delivery_timeout"] == 60

    wrong = await api_client.post(
        "/api/v1/accounts/1/tg/login/resend", headers=h, json={"attempt_id": "nope"}
    )
    assert wrong.status_code == 409

    res = await api_client.post(
        "/api/v1/accounts/1/tg/login/resend", headers=h, json={"attempt_id": attempt}
    )
    assert res.status_code == 200
    assert res.json()["delivery_type"] == "sms"
    assert backend.resend_calls == [("+888", "h3")]


async def test_tg_login_resend_unavailable_is_200_with_error(
    container: Container, api_client: AsyncClient
) -> None:
    backend = FakeTgBackend(
        sent_code_info=SentCodeInfo(phone_code_hash="h3", type="app", next_type="sms")
    )
    backend.errors["resend_code"] = SendCodeRejected("send_code_unavailable")
    run_engine(container, build(authorized=False, backend=backend))
    h = {"X-CSRF-Token": await login(api_client)}
    start = await api_client.post(
        "/api/v1/accounts/1/tg/login/start", headers=h, json={"phone": "+888"}
    )
    res = await api_client.post(
        "/api/v1/accounts/1/tg/login/resend",
        headers=h,
        json={"attempt_id": start.json()["attempt_id"]},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["state"] == "awaiting_code" and body["error"] == "send_code_unavailable"
    assert body["delivery_next_type"] is None


async def test_tg_login_email_flow(container: Container, api_client: AsyncClient) -> None:
    backend = FakeTgBackend(
        sent_code_info=SentCodeInfo(phone_code_hash="h1", type="setup_email"),
        email_code="54321",
    )
    run_engine(container, build(authorized=False, backend=backend))
    csrf = await login(api_client)
    h = {"X-CSRF-Token": csrf}

    start = await api_client.post(
        "/api/v1/accounts/1/tg/login/start", headers=h, json={"phone": "+888"}
    )
    attempt = start.json()["attempt_id"]
    assert start.json()["state"] == "awaiting_email"
    assert start.json()["delivery_type"] == "setup_email"

    wrong = await api_client.post(
        "/api/v1/accounts/1/tg/login/email",
        headers=h,
        json={"attempt_id": "nope", "email": "test@example.com"},
    )
    assert wrong.status_code == 409

    res_email = await api_client.post(
        "/api/v1/accounts/1/tg/login/email",
        headers=h,
        json={"attempt_id": attempt, "email": "test@example.com"},
    )
    assert res_email.status_code == 200
    assert res_email.json()["state"] == "awaiting_email_code"
    assert res_email.json()["delivery_email_pattern"] == "t***@e***.com"

    wrong_code = await api_client.post(
        "/api/v1/accounts/1/tg/login/email-code",
        headers=h,
        json={"attempt_id": "nope", "code": "54321"},
    )
    assert wrong_code.status_code == 409

    bad_code = await api_client.post(
        "/api/v1/accounts/1/tg/login/email-code",
        headers=h,
        json={"attempt_id": attempt, "code": "00000"},
    )
    assert bad_code.status_code == 200
    assert bad_code.json()["state"] == "awaiting_email_code"
    assert bad_code.json()["error"] == "invalid_code"

    ok_code = await api_client.post(
        "/api/v1/accounts/1/tg/login/email-code",
        headers=h,
        json={"attempt_id": attempt, "code": "54321"},
    )
    assert ok_code.status_code == 200
    assert ok_code.json()["state"] == "awaiting_code"

    ok_login = await api_client.post(
        "/api/v1/accounts/1/tg/login/code",
        headers=h,
        json={"attempt_id": attempt, "code": "12345"},
    )
    assert ok_login.status_code == 200
    assert ok_login.json()["state"] == "online"


async def test_readyz_is_process_readiness(container: Container, api_client: AsyncClient) -> None:
    # Ни движков, ни Telegram: процесс готов, пока база отвечает и соединение блокировок живо.
    ready = await api_client.get("/readyz")
    assert (ready.status_code, ready.json()) == (200, {"status": "ready"})
    engines(container).lock_connection_ok = False
    down = await api_client.get("/readyz")
    assert (down.status_code, down.json()) == (503, {"status": "not_ready"})


async def test_readyz_needs_database(container: Container) -> None:
    db = Database("postgresql+asyncpg://pyrobot:pyrobot@127.0.0.1:1/pyrobot")
    app = create_api(replace(container, db=db))
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            assert (await client.get("/readyz")).status_code == 503
    finally:
        await db.dispose()


class _SendCodeDown(FakeTgBackend):
    async def send_code(self, phone: str) -> str:
        raise ConnectionError("network down")


class _BadPhone(FakeTgBackend):
    async def send_code(self, phone: str) -> str:
        raise InvalidPhone


async def test_tg_backend_failure_is_502(container: Container, api_client: AsyncClient) -> None:
    run_engine(container, build(authorized=False, backend=_SendCodeDown()))
    h = {"X-CSRF-Token": await login(api_client)}
    r = await api_client.post(
        "/api/v1/accounts/1/tg/login/start", headers=h, json={"phone": "+888"}
    )
    assert r.status_code == 502 and r.json() == {"detail": "send_code_failed"}
    st = (await api_client.get("/api/v1/accounts/1/tg/status")).json()
    assert st["state"] == "error" and st["error"] == "send_code_failed"


async def test_tg_classified_error_is_400(container: Container, api_client: AsyncClient) -> None:
    run_engine(container, build(authorized=False, backend=_BadPhone()))
    h = {"X-CSRF-Token": await login(api_client)}
    r = await api_client.post(
        "/api/v1/accounts/1/tg/login/start", headers=h, json={"phone": "+888"}
    )
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
    run_engine(container, build(authorized=False, backend=_FloodWaitOnSendCode()))
    h = {"X-CSRF-Token": await login(api_client)}
    r = await api_client.post(
        "/api/v1/accounts/1/tg/login/start", headers=h, json={"phone": "+888"}
    )
    assert r.status_code == 429 and r.json() == {"detail": "flood_wait"}
    assert r.headers["retry-after"] == "31"
    st = (await api_client.get("/api/v1/accounts/1/tg/status")).json()
    assert st["state"] == "error" and st["error"] == "flood_wait"


async def test_tg_send_code_rejected_is_400_with_rpc_code(
    container: Container, api_client: AsyncClient
) -> None:
    run_engine(container, build(authorized=False, backend=_RejectedOnSendCode()))
    h = {"X-CSRF-Token": await login(api_client)}
    r = await api_client.post(
        "/api/v1/accounts/1/tg/login/start", headers=h, json={"phone": "+888"}
    )
    assert r.status_code == 400 and r.json() == {"detail": "phone_number_banned"}


async def test_tg_status_reports_binding(container: Container, api_client: AsyncClient) -> None:
    f = build(authorized=False, bound_user_id=None)
    run_engine(container, f)
    await f.tg.boot()
    h = {"X-CSRF-Token": await login(api_client)}
    assert (await api_client.get("/api/v1/accounts/1/tg/status")).json()["bound_user_id"] is None
    start = await api_client.post(
        "/api/v1/accounts/1/tg/login/start", headers=h, json={"phone": "+888"}
    )
    code = await api_client.post(
        "/api/v1/accounts/1/tg/login/code",
        headers=h,
        json={"attempt_id": start.json()["attempt_id"], "code": "12345"},
    )
    # Первый вход привязывает аккаунт, и привязка видна в статусах.
    assert code.json()["bound_user_id"] == 267519921
    st = (await api_client.get("/api/v1/accounts/1/tg/status")).json()
    assert st["bound_user_id"] == 267519921
    assert (await api_client.get("/api/v1/accounts/1/engine/status")).json()["tg"] == st


class _AccountSeen(FakeTgBackend):
    """Запоминает аккаунт контекста, в котором идёт вход и создаются задачи клиента."""

    def __init__(self) -> None:
        super().__init__(authorized=False)
        self.seen: list[int | None] = []

    async def send_code(self, phone: str) -> str:
        self.seen.append(current_account.get())
        task = asyncio.create_task(asyncio.sleep(0))
        self.seen.append(task.get_context()[current_account])
        await task
        return await super().send_code(phone)


async def test_engine_calls_run_in_account_log_context(
    container: Container, api_client: AsyncClient
) -> None:
    backend = _AccountSeen()
    f = build(authorized=False, backend=backend)
    run_engine(container, f)
    await f.tg.boot()
    h = {"X-CSRF-Token": await login(api_client)}
    r = await api_client.post("/api/v1/accounts/1/tg/login/start", headers=h, json={"phone": "+8"})
    assert r.status_code == 200
    # Вход и задачи, созданные внутри (клиент kurigram), — в контексте аккаунта 1; контекст
    # запроса наружу не протекает.
    assert backend.seen == [1, 1]
    assert current_account.get() is None


async def test_login_start_429_with_retry_after(api: Api) -> None:
    clock = FakeMonotonic(0.0)
    f = build(authorized=False, codes=CodeLimiter(10, monotonic=clock))
    run_engine(api.container, f)
    await f.tg.boot()
    start = f"{A1}/tg/login/start"
    for _ in range(3):
        r = await api.client.post(start, headers=api.headers, json={"phone": "+888"})
        assert r.status_code == 200 and r.json()["state"] == "awaiting_code"
    clock.now = 0.25
    r = await api.client.post(start, headers=api.headers, json={"phone": "+888"})
    assert (r.status_code, r.json()) == (429, {"detail": "tg_code_rate_limited"})
    # Целые секунды вверх: до места — 3599.75 с.
    assert r.headers["retry-after"] == "3600"
    # Начатый вход не сброшен.
    st = (await api.client.get(f"{A1}/tg/status")).json()
    assert st["state"] == "awaiting_code" and st["attempt_id"] is not None


JOIN = f"{A1}/tg/game-chat/join"


async def _joining(api: Api, *, authorized: bool = True) -> tuple[FakeTransport, Watch]:
    transport, watch = FakeTransport(), Watch()
    f = build(authorized=authorized, transport=transport, history=watch)
    run_engine(api.container, f)
    await f.tg.boot()
    return transport, watch


async def test_game_chat_join(api: Api) -> None:
    transport, watch = await _joining(api)
    status = (await api.client.get(f"{A1}/engine/status")).json()
    assert status["game_chat_member"] is False
    assert (await api.client.post(JOIN)).status_code == 403
    r = await api.client.post(JOIN, headers=api.headers)
    assert (r.status_code, r.json()) == (200, {"status": "joined", "game_chat_member": True})
    assert transport.joins == [("startupwarschat", -1001109615116)] and watch.joined == 1
    status = (await api.client.get(f"{A1}/engine/status")).json()
    assert status["game_chat_member"] is True


async def test_game_chat_join_request_sent(api: Api) -> None:
    transport, watch = await _joining(api)
    transport.join_status = "request_sent"
    r = await api.client.post(JOIN, headers=api.headers)
    assert (r.status_code, r.json()) == (
        200,
        {"status": "request_sent", "game_chat_member": False},
    )
    assert watch.joined == 0


@pytest.mark.parametrize(
    ("failure", "code", "detail"),
    [
        (TransportRejected("chat_mismatch"), 409, "game_chat_mismatch"),
        (TransportAuthLost("revoked"), 409, "tg_not_online"),
        (TransportRejected("CHANNELS_TOO_MUCH"), 502, "CHANNELS_TOO_MUCH"),
    ],
)
async def test_game_chat_join_errors(api: Api, failure: Exception, code: int, detail: str) -> None:
    transport, watch = await _joining(api)
    transport.join_fail_with = [failure]
    r = await api.client.post(JOIN, headers=api.headers)
    assert (r.status_code, r.json()) == (code, {"detail": detail})
    assert watch.joined == 0


async def test_game_chat_join_flood_wait(api: Api) -> None:
    transport, _ = await _joining(api)
    transport.join_fail_with = [FloodWait(30)]
    r = await api.client.post(JOIN, headers=api.headers)
    assert (r.status_code, r.json()) == (429, {"detail": "flood_wait"})
    assert r.headers["Retry-After"] == "31"


async def test_game_chat_join_needs_online_telegram(api: Api) -> None:
    transport, _ = await _joining(api, authorized=False)
    r = await api.client.post(JOIN, headers=api.headers)
    assert (r.status_code, r.json()) == (409, {"detail": "tg_not_online"})
    assert transport.joins == []


async def test_game_chat_join_needs_engine(api: Api) -> None:
    r = await api.client.post(JOIN, headers=api.headers)
    assert (r.status_code, r.json()) == (503, {"detail": "engine not running"})


async def test_game_chat_join_refused_for_deleting_account(api: Api) -> None:
    transport, _ = await _joining(api)
    await api.container.accounts.mark_deleting(1)
    r = await api.client.post(JOIN, headers=api.headers)
    assert (r.status_code, r.json()) == (409, {"detail": "account_deleting"})
    assert transport.joins == []

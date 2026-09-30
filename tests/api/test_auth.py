import asyncio
import re
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from app.api.app import create_api
from app.api.container import Container
from app.api.deps import COOKIE
from app.db.base import Database
from app.db.models import AuthSession
from tests.api.conftest import PASSWORD, engines, login, make_container

pytestmark = pytest.mark.db


async def test_login_me_logout(api_client: AsyncClient) -> None:
    assert (await api_client.get("/api/v1/auth/me")).status_code == 401
    csrf = await login(api_client)
    me = await api_client.get("/api/v1/auth/me")
    assert me.status_code == 200 and me.json() == {"login": "admin", "csrf_token": csrf}
    assert (await api_client.post("/api/v1/auth/logout")).status_code == 403
    resp = await api_client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 204
    assert (await api_client.get("/api/v1/auth/me")).status_code == 401


async def test_rate_limit_sequential_and_parallel(api_client: AsyncClient) -> None:
    bad = {"login": "admin", "password": "x"}
    results = await asyncio.gather(
        *(api_client.post("/api/v1/auth/login", json=bad) for _ in range(8))
    )
    assert {r.status_code for r in results} <= {401, 429}
    assert sum(r.status_code == 401 for r in results) == 6
    r = await api_client.post("/api/v1/auth/login", json={"login": "admin", "password": PASSWORD})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0


async def test_password_change_revokes_other_sessions(
    container: Container, api_client: AsyncClient
) -> None:
    csrf = await login(api_client)
    old_cookie = api_client.cookies[COOKIE]
    short = await api_client.post(
        "/api/v1/auth/password",
        headers={"X-CSRF-Token": csrf},
        json={"current": PASSWORD, "new": "short"},
    )
    assert short.status_code == 422
    ok = await api_client.post(
        "/api/v1/auth/password",
        headers={"X-CSRF-Token": csrf},
        json={"current": PASSWORD, "new": "a much longer password"},
    )
    assert ok.status_code == 204
    app = create_api(container)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", cookies={COOKIE: old_cookie}
    ) as stale:
        assert (await stale.get("/api/v1/auth/me")).status_code == 401


async def test_sliding_expiry_refreshes_cookie(
    clean_db: Database, api_client: AsyncClient
) -> None:
    await login(api_client)
    async with clean_db.sessions() as s, s.begin():
        await s.execute(
            update(AuthSession).values(
                last_seen_at=datetime.now(UTC) - timedelta(days=2),
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
    me = await api_client.get("/api/v1/auth/me")
    assert me.status_code == 200
    cookie = me.headers.get("set-cookie", "")
    assert (
        COOKIE in cookie and "httponly" in cookie.lower() and "samesite=strict" in cookie.lower()
    )


async def test_secure_cookie_over_https(clean_db: Database) -> None:
    c = make_container(clean_db, secure=True)
    await c.auth.ensure_admin("admin", PASSWORD)
    app = create_api(c)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await client.post(
            "/api/v1/auth/login", json={"login": "admin", "password": PASSWORD}
        )
        assert "secure" in resp.headers["set-cookie"].lower()
        assert (await client.get("/api/v1/auth/me")).status_code == 200


async def test_login_calls_verify_password_for_unknown_login(
    api_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    async def fake_verify(password_hash: str, password: str) -> bool:
        nonlocal calls
        calls += 1
        return False

    monkeypatch.setattr("app.api.routes_auth.verify_password", fake_verify)
    resp = await api_client.post("/api/v1/auth/login", json={"login": "nobody", "password": "x"})
    assert resp.status_code == 401
    assert calls == 1


async def test_csrf_empty_header_rejected(api_client: AsyncClient) -> None:
    await login(api_client)
    resp = await api_client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": ""})
    assert resp.status_code == 403


async def test_healthz(api_client: AsyncClient) -> None:
    assert (await api_client.get("/healthz")).json() == {"status": "ok"}


async def test_login_field_limits(api_client: AsyncClient) -> None:
    long_login = {"login": "a" * 65, "password": PASSWORD}
    assert (await api_client.post("/api/v1/auth/login", json=long_login)).status_code == 422
    long_pw = {"login": "admin", "password": "p" * 1025}
    assert (await api_client.post("/api/v1/auth/login", json=long_pw)).status_code == 422


async def test_password_change_field_limits(api_client: AsyncClient) -> None:
    h = {"X-CSRF-Token": await login(api_client)}
    url = "/api/v1/auth/password"
    long_current = {"current": "p" * 1025, "new": "a much longer password"}
    assert (await api_client.post(url, headers=h, json=long_current)).status_code == 422
    long_new = {"current": PASSWORD, "new": "n" * 1025}
    assert (await api_client.post(url, headers=h, json=long_new)).status_code == 422


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
async def test_api_docs_disabled(api_client: AsyncClient, path: str) -> None:
    assert (await api_client.get(path)).status_code == 404


async def test_password_change_wrong_current_is_throttled(api_client: AsyncClient) -> None:
    h = {"X-CSRF-Token": await login(api_client)}
    bad = {"current": "wrong password", "new": "a much longer password"}
    codes = [
        (await api_client.post("/api/v1/auth/password", headers=h, json=bad)).status_code
        for _ in range(7)
    ]
    assert codes == [403] * 6 + [429]
    good = {"current": PASSWORD, "new": "a much longer password"}
    r = await api_client.post("/api/v1/auth/password", headers=h, json=good)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0


async def test_anonymous_gets_401_before_engine_check(
    container: Container, api_client: AsyncClient
) -> None:
    # Сессия проверяется раньше фасада: аноним не получает 503 и не узнаёт, поднят ли движок.
    assert engines(container).get(1) is None
    checked: list[str] = []
    for template, methods in create_api(container).openapi()["paths"].items():
        if not template.startswith("/api/v1/") or template == "/api/v1/auth/login":
            continue
        path = re.sub(r"\{[^}]+\}", "1", template)
        for method in methods:
            resp = await api_client.request(method.upper(), path)
            assert resp.status_code == 401, (method, path, resp.status_code)
            checked.append(path)
    assert {
        "/api/v1/accounts/1/engine/status",
        "/api/v1/accounts/1/state",
        "/api/v1/accounts/1/tg/logout",
    } <= set(checked)

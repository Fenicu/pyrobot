import re
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.app import create_api
from app.db.base import Database
from tests.api.conftest import Api, login, make_container, make_user
from tests.engine.test_facade import build

pytestmark = pytest.mark.db
PREFIX = "/api/v1/accounts/{account_id}"
# Минимальные валидные тела запросов по хвосту пути; остальным хватает пустого.
BODIES: dict[str, dict[str, Any]] = {
    "/engine/kill": {"reason": "r"},
    "/tg/login/start": {"phone": "+888"},
    "/tg/login/code": {"attempt_id": "a", "code": "12345"},
    "/tg/login/password": {"attempt_id": "a", "password": "p"},
    "/settings": {"version": 0, "changes": {}},
    "/commands/send": {"text": "😎Я", "idempotency_key": "k"},
    "/commands/click": {
        "chat_id": 1,
        "message_id": 1,
        "revision": 0,
        "callback_data": "d",
        "idempotency_key": "k",
    },
    "/scenarios/{name}/run": {"idempotency_key": "k"},
    "/unrecognized/ack": {"ids": [1]},
    "/notifications/read": {"up_to_id": 1},
    "/artifact/start": {"artifact": "light"},
    "/gadgets/upgrade": {"slot": "right", "target": 25, "kind": "auto"},
}


def account_routes(app: FastAPI) -> list[tuple[str, str]]:
    """Все (метод, путь) приложения с префиксом /api/v1/accounts/{account_id} — по схеме:
    FastAPI держит подключённые роутеры свёрнутыми, `app.routes` их маршрутов не перечисляет."""
    return sorted(
        (method.upper(), path)
        for path, ops in app.openapi()["paths"].items()
        if path.startswith(PREFIX)
        for method in ops
    )


@pytest.fixture
def app(db: Database) -> FastAPI:
    return create_api(make_container(db))


def test_matrix_covers_spec_table(app: FastAPI) -> None:
    paths = {p for _, p in account_routes(app)}
    for tail in (
        "/engine/status",
        "/engine/kill",
        "/tg/login/start",
        "/state",
        "/settings",
        "/settings/history",
        "/planner/outlook",
        "/journal",
        "/decisions/{decision_id}",
        "/actions/{action_id}",
        "/scenario-runs",
        "/commands/send",
        "/scenarios/{name}/run",
        "/metrics",
        "/metro/runs",
        "/metro/live",
        "/unrecognized/ack",
        "/notifications/read",
        "/daily",
        "/events",
        "/artifact",
        "/artifact/start",
        "/gadgets",
        "/gadgets/upgrade",
        "/gadgets/upgrade/stop",
    ):
        assert f"{PREFIX}{tail}" in paths
    # Каталог сценариев общий.
    assert not any(p.endswith("/scenarios") for p in paths)


async def _foreign_account(api: Api) -> int:
    other_id = await make_user(api.container, "other")
    account = await api.container.accounts.create(other_id, "Чужой", capacity=10)
    return account.id


@pytest.mark.parametrize("role", ["user", "owner"])
async def test_foreign_account_is_404_for_every_path(api: Api, role: str) -> None:
    # учётка «bob» с ролью role владеет аккаунтом bob_acc;
    # bob на всех путях аккаунта 1 — 404,
    # admin (owner) на всех путях аккаунта bob_acc — 404
    bob_id = await make_user(api.container, "bob", role=role)
    bob_acc = await api.container.accounts.create(bob_id, "Боб", capacity=10)
    api.engines.put(build(), 1)
    api.engines.put(build(), bob_acc.id)

    app = create_api(api.container)
    routes = account_routes(app)
    assert routes

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as bob_client:
        bob_csrf = await login(bob_client, login="bob")
        bob_headers = {"X-CSRF-Token": bob_csrf}

        for method, template in routes:
            tail = template.removeprefix(PREFIX)
            path = re.sub(r"\{[^}]+\}", "1", tail)
            body = BODIES.get(tail, {}) if method != "GET" else None

            # 1. bob обращается к аккаунту 1 (admin's account)
            resp_bob = await bob_client.request(
                method, f"/api/v1/accounts/1{path}", headers=bob_headers, json=body
            )
            assert (resp_bob.status_code, resp_bob.json()) == (
                404,
                {"detail": "account not found"},
            ), (
                "bob on account 1",
                method,
                template,
            )

            # 2. admin (owner) обращается к чужому аккаунту bob_acc.id
            resp_admin = await api.client.request(
                method, f"/api/v1/accounts/{bob_acc.id}{path}", headers=api.headers, json=body
            )
            assert (resp_admin.status_code, resp_admin.json()) == (
                404,
                {"detail": "account not found"},
            ), (
                "admin on bob's account",
                method,
                template,
            )

            # 3. аккаунта нет вовсе
            resp_missing = await api.client.request(
                method, f"/api/v1/accounts/999{path}", headers=api.headers, json=body
            )
            assert (resp_missing.status_code, resp_missing.json()) == (
                404,
                {"detail": "account not found"},
            ), (
                "admin on missing account",
                method,
                template,
            )


async def test_host_status_is_404_for_user(api: Api) -> None:
    await make_user(api.container, "bob", role="user")
    app = create_api(api.container)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as bob_client:
        await login(bob_client, login="bob")
        r = await bob_client.get("/api/v1/host/status")
        assert r.status_code == 404

    r_admin = await api.client.get("/api/v1/host/status")
    assert r_admin.status_code == 200


def test_no_route_changes_foreign_password(app: FastAPI) -> None:
    # среди путей /api/v1/admin/* и /api/v1/auth/* нет пути с телом, где есть поле password и
    # путь содержит {user_id} — проверка по openapi
    schema = app.openapi()
    paths = schema.get("paths", {})
    components = schema.get("components", {}).get("schemas", {})
    for path, ops in paths.items():
        if "{user_id}" in path:
            for method, op in ops.items():
                req_body = op.get("requestBody", {})
                content = req_body.get("content", {})
                for media in content.values():
                    schema_obj = media.get("schema", {})
                    ref = schema_obj.get("$ref", "")
                    if ref:
                        schema_name = ref.split("/")[-1]
                        model = components.get(schema_name, {})
                        props = model.get("properties", {})
                        assert "password" not in props, (
                            f"Route {method} {path} has password field in {schema_name}"
                        )


async def test_old_paths_are_gone(api: Api) -> None:
    api.engines.put(build())
    for path in ("/api/v1/engine/status", "/api/v1/state", "/api/v1/settings", "/api/v1/events"):
        assert (await api.client.get(path)).status_code == 404, path
    old = await api.client.post("/api/v1/engine/pause", headers=api.headers)
    assert old.status_code == 404
    assert (await api.client.get("/api/v1/scenarios")).status_code == 200


async def test_confirm_token_not_valid_on_other_account(api: Api) -> None:
    admin = await api.container.auth.get_user("admin")
    assert admin is not None
    second = await api.container.accounts.create(admin.id, "Второй", capacity=10)
    api.engines.put(build(), 1)
    api.engines.put(build(), second.id)
    body = {"text": "/ucon", "idempotency_key": "r1"}
    first = await api.client.post(
        "/api/v1/accounts/1/commands/send", headers=api.headers, json=body
    )
    detail = first.json()["detail"]
    assert first.status_code == 409 and detail["reason"] == "missing"
    # Та же сессия, ключ, параметры и версия состояния, но другой аккаунт.
    other = await api.client.post(
        f"/api/v1/accounts/{second.id}/commands/send",
        headers=api.headers,
        json={**body, "confirm_token": detail["confirm_token"]},
    )
    assert other.status_code == 409 and other.json()["detail"]["reason"] == "invalid"

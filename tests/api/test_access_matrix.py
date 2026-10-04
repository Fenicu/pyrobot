import re
from typing import Any

import pytest
from fastapi import FastAPI

from app.api.app import create_api
from app.db.base import Database
from tests.api.conftest import Api, make_container, make_user
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
        "/unrecognized/ack",
        "/notifications/read",
        "/daily",
        "/events",
        "/artifact",
        "/artifact/start",
    ):
        assert f"{PREFIX}{tail}" in paths
    # Каталог сценариев общий.
    assert not any(p.endswith("/scenarios") for p in paths)


async def _foreign_account(api: Api) -> int:
    other_id = await make_user(api.container, "other")
    account = await api.container.accounts.create(other_id, "Чужой", capacity=10)
    return account.id


@pytest.mark.parametrize("target", ["foreign", "missing"])
async def test_every_account_route_is_404_for_foreign_or_missing(api: Api, target: str) -> None:
    account_id = await _foreign_account(api) if target == "foreign" else 999
    # Движок у аккаунта есть: маршрут, пропустивший проверку, ответил бы не 404.
    api.engines.put(build(), account_id)
    routes = account_routes(create_api(api.container))
    assert routes
    for method, template in routes:
        tail = template.removeprefix(PREFIX)
        path = re.sub(r"\{[^}]+\}", "1", tail)
        body = BODIES.get(tail, {}) if method != "GET" else None
        resp = await api.client.request(
            method, f"/api/v1/accounts/{account_id}{path}", headers=api.headers, json=body
        )
        assert (resp.status_code, resp.json()) == (404, {"detail": "account not found"}), (
            method,
            template,
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

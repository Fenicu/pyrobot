from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.app import create_api
from app.db.models import User
from tests.api.conftest import PASSWORD, Api, login, make_user

pytestmark = pytest.mark.db

URL = "/api/v1/me/ui/account-order"


def order(ids: list[Any]) -> dict[str, Any]:
    return {"version": 1, "ids": ids}


async def admin_id(api: Api) -> int:
    async with api.db.sessions() as s:
        return int(await s.scalar(select(User.id).where(User.login == "admin")))


async def test_get_without_saved_order(api: Api) -> None:
    resp = await api.client.get(URL)
    assert resp.status_code == 200
    assert resp.json() == {"order": None}


async def test_put_then_get_and_replace(api: Api) -> None:
    first = order([3, 1, 2])
    resp = await api.client.put(URL, json=first, headers=api.headers)
    assert resp.status_code == 204
    assert (await api.client.get(URL)).json() == {"order": first}

    second = order([2])
    assert (await api.client.put(URL, json=second, headers=api.headers)).status_code == 204
    assert (await api.client.get(URL)).json() == {"order": second}


async def test_order_is_per_user(api: Api) -> None:
    mine = order([1, 2])
    assert (await api.client.put(URL, json=mine, headers=api.headers)).status_code == 204

    await make_user(api.container, "other")
    async with AsyncClient(
        transport=ASGITransport(app=create_api(api.container)), base_url="http://test"
    ) as other:
        csrf = await login(other, PASSWORD, login="other")
        assert (await other.get(URL)).json() == {"order": None}
        theirs = order([7])
        resp = await other.put(URL, json=theirs, headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 204
        assert (await other.get(URL)).json() == {"order": theirs}
    assert (await api.client.get(URL)).json() == {"order": mine}


async def test_put_requires_csrf(api: Api) -> None:
    resp = await api.client.put(URL, json=order([1]))
    assert resp.status_code == 403
    assert resp.json() == {"detail": "csrf token mismatch"}
    assert (await api.client.get(URL)).json() == {"order": None}


async def test_unauthenticated(api_client: AsyncClient) -> None:
    assert (await api_client.get(URL)).status_code == 401
    assert (await api_client.put(URL, json=order([1]))).status_code == 401


def _invalid() -> list[tuple[str, dict[str, Any]]]:
    return [
        ("version", {"version": 2, "ids": [1]}),
        ("no_version", {"ids": [1]}),
        ("no_ids", {"version": 1}),
        ("zero", order([0])),
        ("negative", order([-1])),
        ("not_int", order(["a"])),
        ("float", order([1.5])),
        ("bool", order([True])),
        ("duplicate", order([1, 2, 1])),
        ("too_many", order(list(range(1, 502)))),
        ("extra_field", {**order([1]), "extra": 1}),
    ]


@pytest.mark.parametrize(("name", "body"), _invalid(), ids=[n for n, _ in _invalid()])
async def test_invalid_order_is_rejected(api: Api, name: str, body: dict[str, Any]) -> None:
    resp = await api.client.put(URL, json=body, headers=api.headers)
    assert resp.status_code == 422, name
    assert (await api.client.get(URL)).json() == {"order": None}


async def test_max_sized_and_empty_orders_are_accepted(api: Api) -> None:
    body = order(list(range(1, 501)))
    assert (await api.client.put(URL, json=body, headers=api.headers)).status_code == 204
    assert (await api.client.get(URL)).json() == {"order": body}
    assert (await api.client.put(URL, json=order([]), headers=api.headers)).status_code == 204
    assert (await api.client.get(URL)).json() == {"order": order([])}


async def test_unknown_ids_are_accepted(api: Api) -> None:
    body = order([999_999, 5])
    assert (await api.client.put(URL, json=body, headers=api.headers)).status_code == 204
    assert (await api.client.get(URL)).json() == {"order": body}


@pytest.mark.parametrize(
    "stored",
    [{"version": 2, "ids": [1]}, {"version": 1, "ids": [1, 1]}, {"version": 1, "ids": "x"}, {}],
    ids=["version", "duplicate", "ids_type", "empty"],
)
async def test_invalid_stored_order_reads_as_null(api: Api, stored: dict[str, Any]) -> None:
    await api.container.ui_prefs.put(await admin_id(api), "account_order", stored)
    resp = await api.client.get(URL)
    assert resp.status_code == 200
    assert resp.json() == {"order": None}

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.api.app import create_api
from app.db.models import User, UserUiPref
from tests.api.conftest import PASSWORD, Api, login, make_user

pytestmark = pytest.mark.db

URL = "/api/v1/me/ui/home-layout"


def ids(n: int) -> list[str]:
    return [f"b{chr(97 + i % 26)}{chr(97 + i // 26)}" for i in range(n)]


def item(id_: str = "now", *, x: int = 0, y: int = 0, w: int = 5, h: int = 4) -> dict[str, Any]:
    return {"id": id_, "x": x, "y": y, "w": w, "h": h}


def layout(
    items: list[dict[str, Any]] | None = None, hidden: list[str] | None = None
) -> dict[str, Any]:
    return {
        "version": 1,
        "items": [item()] if items is None else items,
        "hidden": hidden or [],
    }


async def test_get_without_saved_layout(api: Api) -> None:
    resp = await api.client.get(URL)
    assert resp.status_code == 200
    assert resp.json() == {"layout": None}


async def test_put_then_get_and_replace(api: Api) -> None:
    first = layout([item("now"), item("next", x=5, w=4)], hidden=["gadgets"])
    resp = await api.client.put(URL, json=first, headers=api.headers)
    assert resp.status_code == 204
    assert (await api.client.get(URL)).json() == {"layout": first}

    second = layout([item("daily", x=2, y=3, w=10, h=7)])
    assert (await api.client.put(URL, json=second, headers=api.headers)).status_code == 204
    assert (await api.client.get(URL)).json() == {"layout": second}


async def test_layout_is_per_user(api: Api) -> None:
    mine = layout([item("now")])
    assert (await api.client.put(URL, json=mine, headers=api.headers)).status_code == 204

    await make_user(api.container, "other")
    async with AsyncClient(
        transport=ASGITransport(app=create_api(api.container)), base_url="http://test"
    ) as other:
        csrf = await login(other, PASSWORD, login="other")
        assert (await other.get(URL)).json() == {"layout": None}
        theirs = layout([item("today", x=3)])
        resp = await other.put(URL, json=theirs, headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 204
        assert (await other.get(URL)).json() == {"layout": theirs}
    assert (await api.client.get(URL)).json() == {"layout": mine}


async def test_put_requires_csrf(api: Api) -> None:
    resp = await api.client.put(URL, json=layout())
    assert resp.status_code == 403
    assert resp.json() == {"detail": "csrf token mismatch"}
    assert (await api.client.get(URL)).json() == {"layout": None}


async def test_unauthenticated(api_client: AsyncClient) -> None:
    assert (await api_client.get(URL)).status_code == 401
    assert (await api_client.put(URL, json=layout())).status_code == 401


def _invalid() -> list[tuple[str, dict[str, Any]]]:
    base = layout()
    return [
        ("version", {**base, "version": 2}),
        ("w_zero", layout([item(w=0)])),
        ("w_too_wide", layout([item(w=13)])),
        ("h_zero", layout([item(h=0)])),
        ("h_too_tall", layout([item(h=51)])),
        ("x_negative", layout([item(x=-1)])),
        ("x_too_big", layout([item(x=12, w=1)])),
        ("y_too_big", layout([item(y=501)])),
        ("overflow", layout([item(x=10, w=4)])),
        ("duplicate_item", layout([item("now"), item("now", x=5)])),
        ("bad_id_case", layout([item("Bad-Id")])),
        ("bad_id_empty", layout([item("")])),
        ("bad_id_long", layout([item("a" * 33)])),
        ("hidden_overlaps", layout([item("now")], hidden=["now"])),
        ("hidden_duplicate", layout([item("now")], hidden=["gadgets", "gadgets"])),
        ("hidden_bad_id", layout([item("now")], hidden=["Bad-Id"])),
        ("too_many_items", layout([item(i) for i in ids(51)])),
        ("too_many_hidden", layout([], hidden=ids(51))),
        ("extra_field", {**base, "extra": 1}),
        ("extra_item_field", layout([{**item(), "z": 1}])),
    ]


@pytest.mark.parametrize(("name", "body"), _invalid(), ids=[n for n, _ in _invalid()])
async def test_invalid_layout_is_rejected(api: Api, name: str, body: dict[str, Any]) -> None:
    resp = await api.client.put(URL, json=body, headers=api.headers)
    assert resp.status_code == 422, name
    assert (await api.client.get(URL)).json() == {"layout": None}


async def test_max_sized_layout_is_accepted(api: Api) -> None:
    body = layout([item(i, y=n, w=12, h=50) for n, i in enumerate(ids(50))])
    assert (await api.client.put(URL, json=body, headers=api.headers)).status_code == 204


async def test_unknown_ids_are_accepted(api: Api) -> None:
    body = layout([item("future_block")], hidden=["another_future"])
    assert (await api.client.put(URL, json=body, headers=api.headers)).status_code == 204
    assert (await api.client.get(URL)).json() == {"layout": body}


async def test_deleting_user_removes_prefs(api: Api) -> None:
    other_id = await make_user(api.container, "other")
    await api.container.ui_prefs.put(other_id, "home_layout", layout())
    assert await api.container.ui_prefs.get(other_id, "home_layout") == layout()

    async with api.db.sessions() as s, s.begin():
        await s.execute(delete(User).where(User.id == other_id))
    async with api.db.sessions() as s:
        rows = list(await s.scalars(select(UserUiPref).where(UserUiPref.user_id == other_id)))
    assert rows == []
    assert await api.container.ui_prefs.get(other_id, "home_layout") is None

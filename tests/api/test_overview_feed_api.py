from datetime import timedelta
from typing import Any, cast

import pytest
from httpx import AsyncClient

from app.api.container import Container
from tests.api.conftest import Api, login, make_user
from tests.api.test_overview_battle_api import NOW, Frozen, owner_of
from tests.db.test_overview_feed import ledger, note

pytestmark = pytest.mark.db

URL = "/api/v1/overview/feed"


def ago(minutes: int):  # type: ignore[no-untyped-def]
    return NOW - timedelta(minutes=minutes)


async def get(api: Api, **params: Any) -> dict[str, Any]:
    resp = await api.client.get(URL, params=params)
    assert resp.status_code == 200, resp.text
    return cast(dict[str, Any], resp.json())


def keys(body: dict[str, Any]) -> list[str]:
    return [i["key"] for i in body["items"]]


@pytest.fixture(autouse=True)
def frozen(container: Container) -> None:
    container.clock = Frozen(NOW)


async def test_requires_session(api_client: AsyncClient) -> None:
    assert (await api_client.get(URL)).status_code == 401


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 101},
        {"types": "battle,nope"},
        {"account_ids": "1,x"},
        {"after": "5"},
        {"after": "a:b"},
        {"after": "-1:2"},
        {"cursor": "garbage!"},
        {"cursor": "e1swXQ"},
        {"cursor": "abc", "after": "1:1"},
    ],
)
async def test_bad_parameters_rejected(api: Api, params: dict[str, Any]) -> None:
    assert (await api.client.get(URL, params=params)).status_code == 422


async def test_user_without_accounts_gets_empty_feed(api: Api) -> None:
    await make_user(api.container, "bob")
    await login(api.client, login="bob")
    body = await get(api)
    assert body == {
        "items": [],
        "next_cursor": None,
        "watermark": {"ledger": 0, "notifications": 0},
        "has_more": False,
    }


async def test_items_have_the_documented_shape(api: Api) -> None:
    battle = await ledger(api.db, 1, ago(2), "battle", {"money": 1400, "exp": 800}, {"Нитки": 1})
    alert = await note(api.db, 1, ago(1), "scenario_failed", "error", "scenario deed:learn failed")
    body = await get(api)
    assert body["items"] == [
        {
            "key": f"n:{alert}",
            "at": ago(1).isoformat(),
            "account_id": 1,
            "type": "alert",
            "kind": "scenario_failed",
            "level": "error",
            "text": "scenario deed:learn failed",
            "amounts": None,
            "items": None,
        },
        {
            "key": f"l:{battle}",
            "at": ago(2).isoformat(),
            "account_id": 1,
            "type": "battle",
            "kind": "battle",
            "level": None,
            "text": None,
            "amounts": {"money": 1400, "exp": 800},
            "items": {"Нитки": 1},
        },
    ]
    assert body["watermark"] == {"ledger": battle, "notifications": alert}
    assert body["has_more"] is False and body["next_cursor"] is None


async def test_cursor_walks_pages_with_equal_times_without_duplicates(api: Api) -> None:
    made: set[str] = set()
    for _ in range(9):
        made.add(f"l:{await ledger(api.db, 1, ago(5))}")
        made.add(f"n:{await note(api.db, 1, ago(5))}")
    made.add(f"l:{await ledger(api.db, 1, ago(1))}")
    first = await get(api, limit=4)
    assert first["has_more"] is True and first["next_cursor"] is not None
    assert first["watermark"] is not None
    seen = keys(first)
    cursor, pages = first["next_cursor"], 1
    while cursor:
        page = await get(api, limit=4, cursor=cursor)
        pages += 1
        assert pages < 10
        # Знак приёма держит клиент от первой страницы, страница по курсору его не отдаёт.
        assert page["watermark"] is None
        seen += keys(page)
        cursor = page["next_cursor"]
        assert page["has_more"] is (cursor is not None)
    assert pages == 5
    assert len(seen) == len(set(seen)) == len(made) and set(seen) == made


async def test_filters_by_account_and_type(api: Api) -> None:
    second = (await api.container.accounts.create(await owner_of(api.container), "b")).id
    await ledger(api.db, 1, ago(5), "battle")
    wanted = await ledger(api.db, second, ago(6), "factory")
    await note(api.db, second, ago(7), "tg_slow", "warn")
    await note(api.db, second, ago(8), "gadget_bought", "info")
    body = await get(api, account_ids=str(second), types="factory,manual")
    assert keys(body) == [f"l:{wanted}"]
    both = await get(api, account_ids=f"{second}, 1", types="alert")
    assert [i["kind"] for i in both["items"]] == ["tg_slow"]
    assert body["watermark"] == {"ledger": wanted, "notifications": 0}


async def test_foreign_and_unknown_account_ids_look_alike(api: Api) -> None:
    bob = await make_user(api.container, "bob")
    foreign = (await api.container.accounts.create(bob, "f")).id
    await ledger(api.db, foreign, ago(5))
    await note(api.db, foreign, ago(5))
    await ledger(api.db, 1, ago(6))
    a = await api.client.get(URL, params={"account_ids": str(foreign)})
    b = await api.client.get(URL, params={"account_ids": "99999"})
    assert a.status_code == b.status_code == 200
    assert a.content == b.content
    assert a.json()["items"] == []
    mixed = await get(api, account_ids=f"{foreign},1")
    assert {i["account_id"] for i in mixed["items"]} == {1}


async def test_after_returns_late_records_and_pages_with_has_more(api: Api) -> None:
    await ledger(api.db, 1, ago(1))
    first = await get(api)
    mark = first["watermark"]
    late = [await ledger(api.db, 1, ago(300 + i)) for i in range(3)]
    late_note = await note(api.db, 1, ago(900), "tg_slow", "warn")
    after = f"{mark['ledger']}:{mark['notifications']}"
    got: list[str] = []
    calls = 0
    while True:
        part = await get(api, after=after, limit=2)
        calls += 1
        assert calls < 5 and len(part["items"]) <= 2
        assert part["next_cursor"] is None
        got += keys(part)
        after = f"{part['watermark']['ledger']}:{part['watermark']['notifications']}"
        if not part["has_more"]:
            break
    assert calls == 2
    assert set(got) == {*(f"l:{i}" for i in late), f"n:{late_note}"}
    assert after == f"{max(late)}:{late_note}"
    assert (await get(api, after=after))["items"] == []


async def test_after_respects_filters(api: Api) -> None:
    await ledger(api.db, 1, ago(5), "battle")
    book = await ledger(api.db, 1, ago(5), "book")
    body = await get(api, after="0:0", types="battle")
    assert book not in [int(k[2:]) for k in keys(body)]
    assert len(body["items"]) == 1


async def test_ledger_horizon_follows_server_retention(api: Api) -> None:
    old = await ledger(api.db, 1, NOW - timedelta(days=35))
    assert keys(await get(api)) == []
    api.container.server_settings.current.retention.ledger_days = 40
    assert keys(await get(api)) == [f"l:{old}"]

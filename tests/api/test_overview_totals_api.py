from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from httpx import AsyncClient

from app.api.container import Container
from app.db.models import LedgerRow, MetricRow
from app.engine.daily import BALANCE_KEYS
from app.engine.gametime import MSK
from tests.api.conftest import Api, login, make_user
from tests.api.test_overview_battle_api import NOW, Frozen, owner_of

pytestmark = pytest.mark.db

URL = "/api/v1/overview/totals"
NO_DATA = {"delta": None, "covered": False, "accounts": 0}


def msk(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=MSK).astimezone(UTC)


async def metrics(api: Api, account_id: int, points: list[tuple[datetime, str, float]]) -> None:
    async with api.db.sessions() as s, s.begin():
        s.add_all(MetricRow(account_id=account_id, ts=ts, key=k, value=v) for ts, k, v in points)


async def ledger(
    api: Api,
    account_id: int,
    rows: list[tuple[datetime, str, dict[str, Any], dict[str, Any]]],
    *,
    recorded: datetime | None = None,
) -> None:
    async with api.db.sessions() as s, s.begin():
        s.add_all(
            LedgerRow(
                account_id=account_id,
                at=at,
                day=at.astimezone(MSK).date(),
                recorded_at=recorded or at,
                kind=kind,
                amounts=amounts,
                items=items,
                chat_id=1,
                msg_id=account_id * 1_000_000 + i,
                revision=0,
                content_hash="h",
                seq=0,
            )
            for i, (at, kind, amounts, items) in enumerate(rows)
        )


async def get(api: Api, days: int = 7) -> dict[str, Any]:
    resp = await api.client.get(URL, params={"days": days})
    assert resp.status_code == 200, resp.text
    return cast(dict[str, Any], resp.json())


@pytest.fixture(autouse=True)
def frozen(container: Container) -> None:
    container.clock = Frozen(NOW)


async def test_requires_session(api_client: AsyncClient) -> None:
    assert (await api_client.get(URL)).status_code == 401


@pytest.mark.parametrize("days", ["0", "2", "8", "31", "x", ""])
async def test_days_other_than_1_7_30_rejected(api: Api, days: str) -> None:
    assert (await api.client.get(URL, params={"days": days})).status_code == 422


async def test_days_is_required(api: Api) -> None:
    # Умолчания нет: период называет клиент.
    assert (await api.client.get(URL)).status_code == 422


async def test_user_without_accounts_gets_empty_totals(api: Api) -> None:
    await make_user(api.container, "bob")
    await login(api.client, login="bob")
    body = await get(api)
    assert body["accounts"] == []
    assert body["total"]["balance"] == dict.fromkeys(BALANCE_KEYS, NO_DATA)
    assert (body["total"]["levels"], body["total"]["trophies"]) == (0, 0)
    assert (body["total"]["items"], body["total"]["income"], body["total"]["losses"]) == (
        {},
        [],
        [],
    )


async def test_window_header_and_floor(api: Api) -> None:
    body = await get(api, 7)
    assert (body["days"], body["first"], body["today"]) == (7, "2026-10-11", "2026-10-17")
    assert body["now"] == NOW.isoformat()
    assert body["metrics_floor"] == (NOW - timedelta(days=365)).isoformat()
    assert (await get(api, 1))["first"] == "2026-10-17"
    assert (await get(api, 30))["first"] == "2026-09-18"


async def test_totals_and_days_for_one_account(api: Api) -> None:
    await metrics(
        api,
        1,
        [
            (msk(1, 10), "money", 1000.0),  # опорная точка: давно до окна
            (msk(10, 20), "exp", 100.0),
            (msk(12, 9), "money", 1500.0),
            (msk(16, 9), "money", 1800.0),
            (msk(17, 10), "money", 1700.0),
            (msk(16, 9), "level", 70.0),
            (msk(17, 8), "level", 72.0),
            (msk(10, 22), "level", 69.0),
            (msk(16, 12), "glory", 50.0),
        ],
    )
    await ledger(
        api,
        1,
        [
            (msk(16, 10), "battle", {"money": 200, "exp": 80}, {}),
            (msk(17, 1), "battle", {"money": 100}, {}),
            (msk(17, 2), "task", {"trophies": 90, "exp": 700}, {}),
            (msk(17, 3), "hotel", {"money": -30}, {}),
            (msk(16, 15), "deed", {"exp": 9}, {"Нитки": 2}),
        ],
        recorded=msk(14, 10),
    )
    body = await get(api, 7)
    [acc] = body["accounts"]
    assert acc["id"] == 1 and acc["ledger_since"] == "2026-10-14"
    money = {"delta": 700, "covered": True, "accounts": 1}
    assert body["total"]["balance"]["money"] == money
    assert acc["totals"]["balance"]["money"] == money
    # В окне нет ни одной точки exp (последняя до окна) и нет точки glory до него.
    assert acc["totals"]["balance"]["exp"] == NO_DATA
    assert acc["totals"]["balance"]["glory"] == NO_DATA
    assert body["total"]["levels"] == 3 and acc["totals"]["levels"] == 3
    assert body["total"]["trophies"] == 90
    assert body["total"]["items"] == {"Нитки": 2}
    assert body["total"]["income"] == [
        {"kind": "task", "count": 1, "amounts": {"trophies": 90, "exp": 700}},
        {"kind": "battle", "count": 2, "amounts": {"money": 300, "exp": 80}},
    ]
    assert body["total"]["losses"] == [{"kind": "hotel", "count": 1, "amounts": {"money": -30}}]
    days = {d["day"]: d for d in acc["days"]}
    assert list(days) == [f"2026-10-{n}" for n in range(17, 10, -1)]
    assert days["2026-10-17"]["partial"] is True
    assert days["2026-10-16"]["partial"] is False
    assert days["2026-10-14"]["partial"] is True
    assert days["2026-10-17"]["balance"]["money"] == -100
    # Столбец суток - по «Итогам дня»: без точки накануне числа нет, даже если период его знает.
    assert days["2026-10-16"]["balance"]["money"] is None
    assert days["2026-10-15"]["balance"]["money"] is None
    assert set(days["2026-10-17"]["balance"]) == set(BALANCE_KEYS)


async def test_days_match_daily_screen_of_the_account(api: Api) -> None:
    await metrics(
        api,
        1,
        [
            (msk(10, 9), "money", 10.0),
            (msk(11, 9), "money", 20.0),
            (msk(13, 9), "money", 50.0),
            (msk(14, 9), "money", 70.0),
            (msk(16, 9), "exp", 5.0),
            (msk(17, 9), "exp", 9.0),
        ],
    )
    await ledger(api, 1, [(msk(13, 10), "battle", {"money": 4}, {}), (msk(17, 2), "book", {}, {})])
    totals = (await get(api, 7))["accounts"][0]["days"]
    daily = (await api.client.get("/api/v1/accounts/1/daily", params={"days": 7})).json()["days"]
    assert [d["day"] for d in totals] == [d["day"] for d in daily]
    assert [d["partial"] for d in totals] == [d["partial"] for d in daily]
    for mine, theirs in zip(totals, daily, strict=True):
        assert mine["balance"] == {k: b["delta"] for k, b in theirs["balance"].items()}


async def test_two_accounts_sum_only_covered_ones(api: Api) -> None:
    second = (await api.container.accounts.create(await owner_of(api.container), "b")).id
    await metrics(
        api,
        1,
        [(msk(5, 9), "money", 100.0), (msk(15, 9), "money", 160.0), (msk(5, 9), "exp", 1.0)],
    )
    # У второго аккаунта нет точки до окна: его вклад неизвестен, а не нулевой.
    await metrics(api, second, [(msk(15, 9), "money", 999.0), (msk(15, 9), "exp", 3.0)])
    await ledger(api, second, [(msk(15, 10), "battle", {"money": 5}, {"Флюс": 1})])
    body = await get(api, 7)
    assert [a["id"] for a in body["accounts"]] == [1, second]
    assert body["total"]["balance"]["money"] == {"delta": 60, "covered": False, "accounts": 1}
    assert body["accounts"][1]["totals"]["balance"]["money"] == NO_DATA
    assert body["total"]["income"] == [{"kind": "battle", "count": 1, "amounts": {"money": 5}}]
    assert body["total"]["items"] == {"Флюс": 1}
    assert body["accounts"][0]["ledger_since"] is None


async def test_total_covered_when_every_account_has_both_points(api: Api) -> None:
    second = (await api.container.accounts.create(await owner_of(api.container), "b")).id
    for acc, before, after in ((1, 10.0, 15.0), (second, 1.0, 4.0)):
        await metrics(api, acc, [(msk(5, 9), "money", before), (msk(15, 9), "money", after)])
    body = await get(api, 7)
    assert body["total"]["balance"]["money"] == {"delta": 8, "covered": True, "accounts": 2}


async def test_window_longer_than_metric_retention_is_not_covered(api: Api) -> None:
    await metrics(api, 1, [(msk(10, 9), "money", 10.0), (msk(16, 9), "money", 50.0)])
    await metrics(api, 1, [(msk(10, 9), "level", 3.0), (msk(16, 9), "level", 5.0)])
    api.container.server_settings.current.retention.metrics_days = 10
    wide = await get(api, 30)
    assert wide["metrics_floor"] == (NOW - timedelta(days=10)).isoformat()
    assert wide["total"]["balance"]["money"] == NO_DATA
    assert wide["total"]["levels"] is None
    assert wide["accounts"][0]["totals"]["levels"] is None
    # Дни из хранимых точек считаются, как обычно.
    assert {d["day"]: d["balance"]["money"] for d in wide["accounts"][0]["days"]}[
        "2026-10-16"
    ] is None
    # Окно целиком внутри хранения: числа на месте.
    week = await get(api, 7)
    assert week["total"]["balance"]["money"] == {"delta": 40, "covered": True, "accounts": 1}
    assert week["total"]["levels"] == 2


async def test_reference_point_older_than_floor_is_not_used(api: Api) -> None:
    # Порог (7 суток от «сейчас») лежит внутри суток перед окном: окно в хранении, но точка
    # 10.10 по МСК раньше порога - её нет, и сравнивать не с чем.
    await metrics(api, 1, [(msk(10, 5), "money", 10.0), (msk(12, 9), "money", 50.0)])
    await metrics(api, 1, [(msk(10, 13), "exp", 1.0), (msk(12, 9), "exp", 4.0)])
    api.container.server_settings.current.retention.metrics_days = 7
    balance = (await get(api, 7))["total"]["balance"]
    assert balance["money"] == NO_DATA
    assert balance["exp"] == {"delta": 3, "covered": True, "accounts": 1}


async def test_records_after_snapshot_instant_not_counted(api: Api) -> None:
    await metrics(
        api,
        1,
        [
            (msk(1, 9), "money", 10.0),
            (msk(16, 9), "money", 20.0),
            (NOW + timedelta(hours=1), "money", 99.0),
        ],
    )
    await ledger(
        api,
        1,
        [
            (msk(16, 10), "battle", {"money": 1}, {}),
            (NOW + timedelta(minutes=1), "battle", {"money": 5}, {}),
        ],
    )
    body = await get(api, 7)
    assert body["total"]["balance"]["money"]["delta"] == 10
    assert body["total"]["income"] == [{"kind": "battle", "count": 1, "amounts": {"money": 1}}]


async def test_foreign_and_deleting_accounts_not_included(api: Api) -> None:
    bob = await make_user(api.container, "bob")
    foreign = (await api.container.accounts.create(bob, "f")).id
    gone = (await api.container.accounts.create(await owner_of(api.container), "g")).id
    await api.container.accounts.mark_deleting(gone)
    for acc in (1, foreign, gone):
        await metrics(api, acc, [(msk(5, 9), "money", 1.0), (msk(15, 9), "money", 11.0)])
        await ledger(api, acc, [(msk(15, 10), "battle", {"money": 3}, {})])
    body = await get(api, 7)
    assert [a["id"] for a in body["accounts"]] == [1]
    assert body["total"]["balance"]["money"]["delta"] == 10
    assert body["total"]["income"][0]["count"] == 1


async def test_today_window_uses_value_before_midnight(api: Api) -> None:
    await metrics(api, 1, [(msk(12, 9), "money", 100.0), (msk(17, 9), "money", 130.0)])
    body = await get(api, 1)
    assert body["total"]["balance"]["money"] == {"delta": 30, "covered": True, "accounts": 1}
    [day] = body["accounts"][0]["days"]
    assert day["day"] == "2026-10-17" and day["partial"] is True
    # Столбец суток считается по «Итогам дня»: точки накануне нет - числа нет.
    assert day["balance"]["money"] is None

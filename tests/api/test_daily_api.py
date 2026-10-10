import json
import time
from datetime import UTC, date, datetime
from typing import Any

import pytest
from httpx import AsyncClient

from app.api.container import Container
from app.db.base import Database
from app.db.journal import DbJournal
from app.db.models import LedgerRow, MetricRow
from app.engine.daily import BALANCE_KEYS
from app.engine.gametime import MSK
from app.engine.state.ledger import Effect
from tests.api.conftest import login
from tests.engine.helpers import make_msg

pytestmark = pytest.mark.db


def msk(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=MSK).astimezone(UTC)


class Frozen:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at

    def monotonic(self) -> float:
        return time.monotonic()


async def _metrics(db: Database, points: list[tuple[datetime, str, float]]) -> None:
    async with db.sessions() as s, s.begin():
        s.add_all(MetricRow(account_id=1, ts=ts, key=k, value=v) for ts, k, v in points)


async def test_daily_needs_session(container: Container, api_client: AsyncClient) -> None:
    assert (await api_client.get("/api/v1/accounts/1/daily")).status_code == 401


async def test_daily_days_bounds(container: Container, api_client: AsyncClient) -> None:
    await login(api_client)
    for days in (0, 31):
        resp = await api_client.get("/api/v1/accounts/1/daily", params={"days": days})
        assert resp.status_code == 422


async def test_daily_empty_history(container: Container, api_client: AsyncClient) -> None:
    container.clock = Frozen(msk(28, 14, 40))
    await login(api_client)
    body = (await api_client.get("/api/v1/accounts/1/daily")).json()
    assert body["ledger_since"] is None
    assert len(body["days"]) == 30
    first = body["days"][0]
    assert first["day"] == "2026-09-28" and body["days"][-1]["day"] == "2026-08-30"
    assert all(d["partial"] for d in body["days"])
    assert first["balance"]["money"] == {"delta": None, "covered": False}
    assert (first["level"], first["trophies"], first["items"]) == (None, 0, {})
    assert (first["income"], first["losses"]) == ([], [])


async def test_daily_from_metrics_and_ledger(
    container: Container, api_client: AsyncClient, clean_db: Database
) -> None:
    container.clock = Frozen(msk(28, 14, 40))
    await _metrics(
        clean_db,
        [
            (msk(26, 12), "level", 70),
            (msk(26, 23), "money", 900),
            (msk(27, 22), "money", 1000),
            (msk(27, 22), "glory", 45090),
            (msk(28, 10), "money", 1240),
            (msk(28, 10), "level", 71),
            (msk(28, 11), "glory", 45180),
            # Позже «сейчас» — не в счёт.
            (msk(28, 15), "money", 5000),
        ],
    )
    journal = DbJournal(clean_db, 1)
    effects = {
        1: (msk(28, 9), [Effect("deed", {"exp": 158}, {"Пуговица": 1})]),
        2: (msk(28, 9, 5), [Effect("task", {"exp": 722, "money": 60, "trophies": 90})]),
        3: (msk(28, 9, 10), [Effect("deed_start", {"money": -30})]),
        4: (msk(27, 12), [Effect("book", {"exp": 457})]),
    }
    for msg_id, (at, items) in effects.items():
        msg = make_msg("x", msg_id=msg_id, date=at, received_at=at)
        await journal.append(msg, [], None, 0, effects=items)
    await login(api_client)
    body = (await api_client.get("/api/v1/accounts/1/daily", params={"days": 2})).json()
    assert body["ledger_since"] == "2026-09-27"
    today, yesterday = body["days"]
    assert today == {
        "day": "2026-09-28",
        "partial": True,
        "balance": {
            "money": {"delta": 240, "covered": True},
            "exp": {"delta": None, "covered": False},
            "knowledge": {"delta": None, "covered": False},
            "details": {"delta": None, "covered": False},
            "raw": {"delta": None, "covered": False},
            "glory": {"delta": 90, "covered": True},
        },
        "level": {"from": 70, "to": 71},
        "trophies": 90,
        "items": {"Пуговица": 1},
        "income": [
            {"kind": "task", "count": 1, "amounts": {"exp": 722, "money": 60, "trophies": 90}}
        ],
        "losses": [{"kind": "deed_start", "count": 1, "amounts": {"money": -30}}],
    }
    assert yesterday["day"] == "2026-09-27" and yesterday["partial"] is True
    assert yesterday["balance"]["money"] == {"delta": 100, "covered": True}
    assert yesterday["income"] == [{"kind": "book", "count": 1, "amounts": {"exp": 457}}]
    assert date.fromisoformat(yesterday["day"]) < date(2026, 9, 28)


# Журнал прихода с видами всех групп, разным порядком ключей сумм и предметов и днями вне окна.
LEDGER = [
    (20, "book", {"exp": 1}, {}),
    (27, "deed", {"exp": 1}, {"Нитки": 2}),
    (27, "lottery_win", {"money": 100}, {}),
    (27, "gadget_buy", {"money": -3}, {"Китайская мобила": 1}),
    (27, "task", {"trophies": 30}, {}),
    (28, "deed", {"exp": 158}, {"Пуговица": 1, "Нитки": 1}),
    (28, "task", {"money": 60, "exp": 722, "trophies": 90}, {}),
    (28, "book", {"exp": 457}, {}),
    (28, "deed_start", {"money": -30}, {}),
    (28, "container", {"raw": 2, "containers_small": 1}, {"Флюс": 1}),
    (28, "gadget_upgrade", {"upgrades_white": -1}, {"up:right": 1, "ok": 1}),
    (28, "book", {"knowledge": 3, "exp": 263}, {}),
    (28, "zzz_new", {"money": 5}, {"Пуговица": 2}),
    (28, "robbery", {"money": -340}, {}),
    (28, "trip", {}, {"Флюс": 1}),
    (28, "deed_start", {"raw": -1, "money": -30}, {}),
]


def _day(day: str, partial: bool, trophies: int, items: Any, income: Any, losses: Any) -> Any:
    no_data = {"delta": None, "covered": False}
    return {
        "day": day,
        "partial": partial,
        "balance": dict.fromkeys(BALANCE_KEYS, no_data),
        "level": None,
        "trophies": trophies,
        "items": items,
        "income": income,
        "losses": losses,
    }


# Тело ответа, снятое до свёртки журнала по мере чтения (журнал окна списком, группировка после),
# сравнивается байт в байт: порядок ключей сумм и предметов — порядок первого появления (ключи
# одного ряда — в порядке jsonb).
GOLDEN = {
    "days": [
        _day(
            "2026-09-28",
            True,
            90,
            {"Нитки": 1, "Пуговица": 3, "Флюс": 2},
            [
                {"kind": "book", "count": 2, "amounts": {"exp": 720, "knowledge": 3}},
                {"kind": "container", "count": 1, "amounts": {"raw": 2, "containers_small": 1}},
                {"kind": "trip", "count": 1, "amounts": {}},
                {"kind": "task", "count": 1, "amounts": {"exp": 722, "money": 60, "trophies": 90}},
                {"kind": "zzz_new", "count": 1, "amounts": {"money": 5}},
            ],
            [
                {"kind": "robbery", "count": 1, "amounts": {"money": -340}},
                {"kind": "deed_start", "count": 2, "amounts": {"money": -60, "raw": -1}},
                {"kind": "gadget_upgrade", "count": 1, "amounts": {"upgrades_white": -1}},
            ],
        ),
        _day(
            "2026-09-27",
            False,
            30,
            {"Нитки": 2},
            [
                {"kind": "lottery_win", "count": 1, "amounts": {"money": 100}},
                {"kind": "task", "count": 1, "amounts": {"trophies": 30}},
            ],
            [{"kind": "gadget_buy", "count": 1, "amounts": {"money": -3}}],
        ),
        _day("2026-09-26", False, 0, {}, [], []),
    ],
    "ledger_since": "2026-09-20",
}


async def test_daily_body_is_byte_identical_to_list_grouping(
    container: Container, api_client: AsyncClient, clean_db: Database
) -> None:
    container.clock = Frozen(msk(28, 14, 40))
    async with clean_db.sessions() as s, s.begin():
        for i, (day, kind, amounts, items) in enumerate(LEDGER):
            at = msk(day, 9, i)
            s.add(
                LedgerRow(
                    account_id=1,
                    at=at,
                    recorded_at=at,
                    day=date(2026, 9, day),
                    kind=kind,
                    amounts=amounts,
                    items=items,
                    chat_id=1,
                    msg_id=i,
                    revision=0,
                    content_hash="h",
                    seq=0,
                )
            )
            await s.flush()
    await login(api_client)
    resp = await api_client.get("/api/v1/accounts/1/daily", params={"days": 3})
    assert resp.text == json.dumps(GOLDEN, ensure_ascii=False, separators=(",", ":"))

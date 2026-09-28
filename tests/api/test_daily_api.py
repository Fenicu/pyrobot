import time
from datetime import UTC, date, datetime

import pytest
from httpx import AsyncClient

from app.api.container import Container
from app.db.base import Database
from app.db.journal import DbJournal
from app.db.models import MetricRow
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
    assert (await api_client.get("/api/v1/daily")).status_code == 401


async def test_daily_days_bounds(container: Container, api_client: AsyncClient) -> None:
    await login(api_client)
    for days in (0, 31):
        resp = await api_client.get("/api/v1/daily", params={"days": days})
        assert resp.status_code == 422


async def test_daily_empty_history(container: Container, api_client: AsyncClient) -> None:
    container.clock = Frozen(msk(28, 14, 40))
    await login(api_client)
    body = (await api_client.get("/api/v1/daily")).json()
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
        await journal.append(make_msg("x", msg_id=msg_id, date=at), [], None, 0, effects=items)
    await login(api_client)
    body = (await api_client.get("/api/v1/daily", params={"days": 2})).json()
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

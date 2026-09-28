from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.container import Container
from app.api.deps import SessionContext, container, current_session
from app.api.errors import AUTH
from app.engine.daily import BALANCE_KEYS, LEVEL_KEY, DaySummary, KindSum, summarize
from app.engine.gametime import day_start, tasks_day

router = APIRouter(prefix="/api/v1", tags=["daily"])
MAX_DAYS = 30


class BalanceOut(BaseModel):
    """Изменение наблюдаемого значения за сутки МСК; `delta = null` — нет данных (нет точки в
    сутках или точки предыдущих суток)."""

    delta: int | None
    covered: bool


class LevelOut(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)
    from_: int = Field(alias="from")
    to: int


class KindOut(BaseModel):
    kind: str
    count: int
    amounts: dict[str, int]


class DayOut(BaseModel):
    day: date
    # Сегодня или день до запуска журнала прихода (разовое и потери неизвестны).
    partial: bool
    # Изменение за день по ключам money, exp, knowledge, details, raw, glory.
    balance: dict[str, BalanceOut]
    level: LevelOut | None
    # 🏆 за задания за день.
    trophies: int
    # Предметы крафта за день.
    items: dict[str, int]
    # Разовое (объясняющие события: к изменению баланса не прибавляются) и траты с потерями.
    income: list[KindOut]
    losses: list[KindOut]


class DailyOut(BaseModel):
    # Сегодня первым.
    days: list[DayOut]
    # Первый день журнала прихода; до него разового нет.
    ledger_since: date | None


def _kinds(sums: tuple[KindSum, ...]) -> list[KindOut]:
    return [KindOut(kind=s.kind, count=s.count, amounts=s.amounts) for s in sums]


def day_out(d: DaySummary) -> DayOut:
    return DayOut(
        day=d.day,
        partial=d.partial,
        balance={k: BalanceOut(delta=b.delta, covered=b.covered) for k, b in d.balance.items()},
        level=(
            LevelOut.model_validate({"from": d.level.before, "to": d.level.after})
            if d.level
            else None
        ),
        trophies=d.trophies,
        items=d.items,
        income=_kinds(d.income),
        losses=_kinds(d.losses),
    )


@router.get("/daily", response_model=DailyOut, responses=AUTH)
async def daily(
    _: Annotated[SessionContext, Depends(current_session)],
    c: Annotated[Container, Depends(container)],
    days: Annotated[int, Query(ge=1, le=MAX_DAYS)] = MAX_DAYS,
) -> DailyOut:
    """Итоги дня за `days` суток МСК, сегодня первым (сегодня — на текущий момент)."""
    now = c.clock.now()
    today = tasks_day(now)
    first = today - timedelta(days=days - 1)
    # Начало первого дня — значения предыдущих суток.
    before = first - timedelta(days=1)
    last = await c.reads.day_values([*BALANCE_KEYS, LEVEL_KEY], before, now)
    level = (await c.reads.metrics_before([LEVEL_KEY], day_start(before))).get(LEVEL_KEY)
    ledger, since = await c.reads.ledger_entries(first)
    summary = summarize(
        today=today,
        days=days,
        last=last,
        level_before=level[1] if level else None,
        ledger=ledger,
        ledger_since=since,
    )
    return DailyOut(days=[day_out(d) for d in summary], ledger_since=since)

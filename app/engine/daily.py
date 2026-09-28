"""Итоги дня: изменение баланса за сутки МСК по метрикам и разовое, потери и предметы из
журнала прихода. Чистые функции: выборки делает `DbReads`, фикстуру админки строит
`tools/daily_fixture.py`."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.engine.gametime import tasks_day

# Ключи метрик, по которым считается изменение за день (уровень — отдельный факт дня).
BALANCE_KEYS = ("money", "exp", "knowledge", "details", "raw", "glory")
LEVEL_KEY = "level"
# Траты и потери — отдельной строкой; порядок — для показа.
LOSS_ORDER = ("robbery", "deed_start", "hotel", "lottery_tickets", "gorbushka_ticket")
# Разовое (объясняющие события): порядок показа, незнакомые виды — после, по имени.
INCOME_ORDER = (
    "book",
    "card",
    "prizebox",
    "container",
    "gorbushka_fight",
    "lottery_win",
    "lottery_skills",
    "metro",
    "bulls",
    "task",
    "factory",
    "battle",
    "dividends",
    "levelup",
    "sleep",
    "robbery_fight",
    "tangerine_gift",
    "exchange",
    "shark",
)
# Итоги дел — обычный приход: в разовое не идут, из них берутся только предметы крафта.
DEED = "deed"


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    day: date
    kind: str
    amounts: Mapping[str, int]
    items: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class Balance:
    """Изменение наблюдаемого значения за сутки; `None` и `covered=False` — нет данных."""

    delta: int | None
    covered: bool


@dataclass(frozen=True, slots=True)
class Level:
    before: int
    after: int


@dataclass(frozen=True, slots=True)
class KindSum:
    kind: str
    count: int
    amounts: dict[str, int]


@dataclass(frozen=True, slots=True)
class DaySummary:
    day: date
    # Сегодня (сутки не кончились) или день до запуска журнала прихода (разовое неизвестно).
    partial: bool
    balance: dict[str, Balance]
    level: Level | None
    trophies: int
    items: dict[str, int]
    income: tuple[KindSum, ...]
    losses: tuple[KindSum, ...]


def last_by_day(points: Iterable[tuple[datetime, str, float]]) -> dict[str, dict[date, float]]:
    """Последнее значение каждого ключа в каждые сутки МСК (то же, что считает `DbReads`)."""
    latest: dict[tuple[str, date], tuple[datetime, float]] = {}
    for ts, key, value in points:
        slot = (key, tasks_day(ts))
        known = latest.get(slot)
        if known is None or ts >= known[0]:
            latest[slot] = (ts, value)
    out: dict[str, dict[date, float]] = {}
    for (key, day), (_, value) in sorted(latest.items()):
        out.setdefault(key, {})[day] = value
    return out


def _balance(values: Mapping[date, float], day: date) -> Balance:
    # Конец — последнее значение суток, начало — последнее значение предыдущих суток (не старше
    # 24 ч до их начала: сутки МСК — ровно 24 ч). Нет любой из точек — нет данных.
    end = values.get(day)
    begin = values.get(day - timedelta(days=1))
    if end is None or begin is None:
        return Balance(None, False)
    return Balance(round(end - begin), True)


def _level(values: Mapping[date, float], before: float | None, day: date) -> Level | None:
    after = values.get(day)
    if after is None:
        return None
    earlier = [values[d] for d in sorted(values) if d < day]
    start = earlier[-1] if earlier else before
    if start is None or int(start) == int(after):
        return None
    return Level(int(start), int(after))


def _order(kind: str, order: tuple[str, ...]) -> tuple[int, str]:
    return (order.index(kind) if kind in order else len(order), kind)


def _sums(entries: list[LedgerEntry], order: tuple[str, ...]) -> tuple[KindSum, ...]:
    grouped: dict[str, KindSum] = {}
    for e in entries:
        known = grouped.get(e.kind) or KindSum(e.kind, 0, {})
        sums = dict(known.amounts)
        for key, value in e.amounts.items():
            sums[key] = sums.get(key, 0) + value
        grouped[e.kind] = KindSum(e.kind, known.count + 1, sums)
    return tuple(grouped[k] for k in sorted(grouped, key=lambda k: _order(k, order)))


def summarize(
    *,
    today: date,
    days: int,
    last: Mapping[str, Mapping[date, float]],
    level_before: float | None,
    ledger: Iterable[LedgerEntry],
    ledger_since: date | None,
) -> list[DaySummary]:
    """Итоги `days` суток МСК, сегодня первым. `last` — последнее значение ключа в каждые сутки
    (с суток до первого показываемого дня), `level_before` — уровень до этих суток, `ledger` —
    записи журнала прихода, `ledger_since` — первый день журнала (None — журнал пуст)."""
    by_day: dict[date, list[LedgerEntry]] = {}
    for entry in ledger:
        by_day.setdefault(entry.day, []).append(entry)
    out: list[DaySummary] = []
    for back in range(days):
        day = today - timedelta(days=back)
        entries = by_day.get(day, [])
        items: dict[str, int] = {}
        trophies = 0
        for e in entries:
            trophies += e.amounts.get("trophies", 0)
            for name, n in e.items.items():
                items[name] = items.get(name, 0) + n
        partial = day == today or ledger_since is None or day <= ledger_since
        out.append(
            DaySummary(
                day=day,
                partial=partial,
                balance={k: _balance(last.get(k, {}), day) for k in BALANCE_KEYS},
                level=_level(last.get(LEVEL_KEY, {}), level_before, day),
                trophies=trophies,
                items=items,
                income=_sums(
                    [e for e in entries if e.kind != DEED and e.kind not in LOSS_ORDER],
                    INCOME_ORDER,
                ),
                losses=_sums([e for e in entries if e.kind in LOSS_ORDER], LOSS_ORDER),
            )
        )
    return out

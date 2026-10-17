"""Итоги дня: изменение баланса за сутки МСК по метрикам и разовое, потери и предметы из
журнала прихода. Чистые функции: выборки делает `DbReads`, фикстуру админки строит
`tools/daily_fixture.py`."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from app.engine.gametime import tasks_day

# Ключи метрик, по которым считается изменение за день (уровень — отдельный факт дня).
BALANCE_KEYS = ("money", "exp", "knowledge", "details", "raw", "glory")
LEVEL_KEY = "level"
# Траты и потери — отдельной строкой; порядок — для показа.
LOSS_ORDER = (
    "robbery",
    "deed_start",
    "trip_start",
    "hotel",
    "lottery_tickets",
    "gorbushka_ticket",
    "gadget_buy",
    "gadget_upgrade",
)
# Предметы этих видов — не крафт (купленный гаджет, слот и исход заточки): в предметы дня не идут.
NOT_CRAFT = frozenset({"gadget_buy", "gadget_upgrade"})
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
    "trip",
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


def _add(into: dict[str, int], values: Mapping[str, int]) -> None:
    # Ключи — в порядке первого появления: от него зависит порядок в теле ответа.
    for key, value in values.items():
        into[key] = into.get(key, 0) + value


@dataclass(slots=True)
class _KindLedger:
    count: int = 0
    amounts: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class _DayLedger:
    trophies: int = 0
    items: dict[str, int] = field(default_factory=dict)
    income: dict[str, _KindLedger] = field(default_factory=dict)
    losses: dict[str, _KindLedger] = field(default_factory=dict)


@dataclass(slots=True)
class LedgerDays:
    """Журнал прихода, свёрнутый по суткам по мере чтения: 🏆, предметы крафта, разовое и потери
    по видам — без списка всех записей окна."""

    days: dict[date, _DayLedger] = field(default_factory=dict)

    @classmethod
    def of(cls, entries: Iterable[LedgerEntry]) -> LedgerDays:
        out = cls()
        for entry in entries:
            out.add(entry)
        return out

    def add(self, e: LedgerEntry) -> None:
        self.add_group(e.day, e.kind, 1, e.amounts, e.items)

    def add_group(
        self,
        day: date,
        kind: str,
        count: int,
        amounts: Mapping[str, int],
        items: Mapping[str, int],
    ) -> None:
        """`count` записей одного вида за сутки, уже сложенных: так их отдаёт свёртка в SQL.
        Правила те же, что у `add` (тот и вызывает эту функцию с одной записью)."""
        got = self.days.get(day)
        if got is None:
            got = self.days[day] = _DayLedger()
        got.trophies += amounts.get("trophies", 0)
        if kind not in NOT_CRAFT:
            _add(got.items, items)
        if kind == DEED:
            return
        group = got.losses if kind in LOSS_ORDER else got.income
        slot = group.get(kind)
        if slot is None:
            slot = group[kind] = _KindLedger()
        slot.count += count
        _add(slot.amounts, amounts)


def _sums(grouped: Mapping[str, _KindLedger], order: tuple[str, ...]) -> tuple[KindSum, ...]:
    return tuple(
        KindSum(k, grouped[k].count, grouped[k].amounts)
        for k in sorted(grouped, key=lambda k: _order(k, order))
    )


def summarize(
    *,
    today: date,
    days: int,
    last: Mapping[str, Mapping[date, float]],
    level_before: float | None,
    ledger: LedgerDays | Iterable[LedgerEntry],
    ledger_since: date | None,
) -> list[DaySummary]:
    """Итоги `days` суток МСК, сегодня первым. `last` — последнее значение ключа в каждые сутки
    (с суток до первого показываемого дня), `level_before` — уровень до этих суток, `ledger` —
    журнал прихода (свёрнутый по суткам или записями по порядку), `ledger_since` — первый день
    журнала (None — журнал пуст)."""
    folded = ledger if isinstance(ledger, LedgerDays) else LedgerDays.of(ledger)
    out: list[DaySummary] = []
    for back in range(days):
        day = today - timedelta(days=back)
        got = folded.days.get(day) or _DayLedger()
        partial = day == today or ledger_since is None or day <= ledger_since
        out.append(
            DaySummary(
                day=day,
                partial=partial,
                balance={k: _balance(last.get(k, {}), day) for k in BALANCE_KEYS},
                level=_level(last.get(LEVEL_KEY, {}), level_before, day),
                trophies=got.trophies,
                items=got.items,
                income=_sums(got.income, INCOME_ORDER),
                losses=_sums(got.losses, LOSS_ORDER),
            )
        )
    return out


@dataclass(frozen=True, slots=True)
class Delta:
    """Изменение значения за период; `None` и `covered=False` - нет данных (ложного нуля нет)."""

    value: int | None
    covered: bool


@dataclass(frozen=True, slots=True)
class TotalDelta:
    """Сумма изменений по аккаунтам, у которых оно известно: `accounts` из них вошли в сумму,
    `covered` - вошли все."""

    delta: int | None
    covered: bool
    accounts: int


@dataclass(frozen=True, slots=True)
class AccountTotals:
    """Итог периода по одному аккаунту; `levels=None` - окно длиннее хранения метрик."""

    balance: dict[str, Delta]
    levels: int | None
    trophies: int
    items: dict[str, int]
    income: tuple[KindSum, ...]
    losses: tuple[KindSum, ...]


@dataclass(frozen=True, slots=True)
class Totals:
    balance: dict[str, TotalDelta]
    levels: int | None
    trophies: int
    items: dict[str, int]
    income: tuple[KindSum, ...]
    losses: tuple[KindSum, ...]


def period_balance(
    window_last: Mapping[str, float | None],
    before_last: Mapping[str, float | None],
    *,
    window_covered: bool,
) -> dict[str, Delta]:
    """Изменение за период: последнее значение в окне минус последнее до окна, любого возраста.
    Нет любой из точек (метрика пишется только при изменении) или окно длиннее хранения метрик
    (опорная точка могла быть стёрта) - `Delta(None, False)`."""
    out: dict[str, Delta] = {}
    for key in BALANCE_KEYS:
        end, begin = window_last.get(key), before_last.get(key)
        known = window_covered and end is not None and begin is not None
        out[key] = Delta(round(end - begin), True) if known else Delta(None, False)  # type: ignore[operator]
    return out


def _window_end(values: Mapping[date, float] | None, first: date) -> float | None:
    inside = [d for d in values or {} if d >= first]
    return values[max(inside)] if values and inside else None


def account_totals(
    days: Sequence[DaySummary],
    *,
    last: Mapping[str, Mapping[date, float]],
    first: date,
    before: Mapping[str, float],
    window_covered: bool,
) -> AccountTotals:
    """Итог периода аккаунта: баланс и повышения уровня - по значениям метрик, остальное -
    сумма суток `days` (те же столбцы, что в «Итогах дня»). `last` - последнее значение ключа в
    каждые сутки, `before` - последнее значение ключа до начала окна `first`."""
    end = {k: _window_end(last.get(k), first) for k in (*BALANCE_KEYS, LEVEL_KEY)}
    level_end, level_before = end[LEVEL_KEY], before.get(LEVEL_KEY)
    levels = (
        int(level_end) - int(level_before)
        if level_end is not None and level_before is not None
        else 0
    )
    items: dict[str, int] = {}
    income: dict[str, _KindLedger] = {}
    losses: dict[str, _KindLedger] = {}
    for day in days:
        _add(items, day.items)
        for src, dst in ((day.income, income), (day.losses, losses)):
            for kind in src:
                _fold_kind(dst, kind)
    return AccountTotals(
        balance=period_balance(end, before, window_covered=window_covered),
        levels=levels if window_covered else None,
        trophies=sum(d.trophies for d in days),
        items=items,
        income=_sums(income, INCOME_ORDER),
        losses=_sums(losses, LOSS_ORDER),
    )


def _fold_kind(into: dict[str, _KindLedger], kind: KindSum) -> None:
    slot = into.get(kind.kind)
    if slot is None:
        slot = into[kind.kind] = _KindLedger()
    slot.count += kind.count
    _add(slot.amounts, kind.amounts)


def merge_totals(parts: Sequence[AccountTotals]) -> Totals:
    """Сумма итогов аккаунтов. Баланс берётся только у тех, у кого он известен; `accounts` -
    сколько их вошло (клиент пишет «по 5 из 7»). Если не вошёл никто - `None`, а не ноль."""
    balance: dict[str, TotalDelta] = {}
    for key in BALANCE_KEYS:
        known = [p.balance[key].value for p in parts if p.balance[key].covered]
        got = [v for v in known if v is not None]
        balance[key] = TotalDelta(
            sum(got) if got else None, bool(got) and len(got) == len(parts), len(got)
        )
    levels = [p.levels for p in parts if p.levels is not None]
    items: dict[str, int] = {}
    income: dict[str, _KindLedger] = {}
    losses: dict[str, _KindLedger] = {}
    for part in parts:
        _add(items, part.items)
        for kind in part.income:
            _fold_kind(income, kind)
        for kind in part.losses:
            _fold_kind(losses, kind)
    return Totals(
        balance=balance,
        levels=None if parts and not levels else sum(levels),
        trophies=sum(p.trophies for p in parts),
        items=items,
        income=_sums(income, INCOME_ORDER),
        losses=_sums(losses, LOSS_ORDER),
    )

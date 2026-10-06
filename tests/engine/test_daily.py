"""Итоги дня: изменение баланса по метрикам с правилом покрытия и журнал прихода по суткам МСК."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from app.engine.daily import (
    Balance,
    KindSum,
    LedgerEntry,
    Level,
    last_by_day,
    summarize,
)
from app.engine.gametime import MSK
from app.engine.parsing import default_parser
from app.engine.settings import ChatsSection
from app.engine.state.reducer import StateReducer
from tests.fixtures import game_msg

TODAY = date(2026, 9, 28)


def msk(day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, second, tzinfo=MSK).astimezone(UTC)


def one(**kw: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "today": TODAY,
        "days": 1,
        "last": {},
        "level_before": None,
        "ledger": [],
        "ledger_since": date(2026, 9, 1),
    }
    return {**defaults, **kw}


def test_msk_day_boundaries() -> None:
    last = last_by_day(
        [
            (msk(27, 23, 59, 59), "money", 10.0),
            (msk(28, 0, 0, 0), "money", 20.0),
            (msk(28, 0, 0, 1), "money", 25.0),
            (msk(27, 1), "money", 5.0),
        ]
    )
    assert last == {"money": {date(2026, 9, 27): 10.0, date(2026, 9, 28): 25.0}}


def test_delta_needs_point_in_day_and_previous_day() -> None:
    last = {
        "money": {date(2026, 9, 27): 1000.0, TODAY: 1240.0},
        "exp": {TODAY: 5.0},
        "raw": {date(2026, 9, 27): 7.0},
        "details": {date(2026, 9, 26): 1.0, TODAY: 3.0},
    }
    [day] = summarize(**one(last=last))  # type: ignore[arg-type]
    assert day.balance["money"] == Balance(240, True)
    # Нет начала (точки прошлых суток) — «нет данных», не ложный ноль.
    assert day.balance["exp"] == Balance(None, False)
    # Нет точки внутри суток — тоже «нет данных».
    assert day.balance["raw"] == Balance(None, False)
    # Начальное значение старше 24 ч не берётся.
    assert day.balance["details"] == Balance(None, False)
    assert day.balance["glory"] == Balance(None, False)
    assert day.level is None


def test_negative_delta_and_rounding() -> None:
    last = {"details": {date(2026, 9, 27): 136671.0, TODAY: 136551.0}}
    [day] = summarize(**one(last=last))  # type: ignore[arg-type]
    assert day.balance["details"] == Balance(-120, True)


def test_exp_through_level_up_from_profiles() -> None:
    """Опыт — накопительный: разница через повышение уровня 70 → 71 — разница профилей."""
    reducer = StateReducer()
    parser = default_parser(ChatsSection())
    points = []
    state: dict[str, object] = {}
    for msg_id, at in ((3516674, msk(27, 23)), (3536910, msk(28, 10))):
        msg = replace(game_msg("profile", msg_id), date=at, created_at=at)
        new = reducer.apply(state, msg, parser.parse(msg))  # type: ignore[arg-type]
        points += [(at, k, v) for k, v in reducer.metrics(state, new).items()]  # type: ignore[arg-type]
        state = new
    [day] = summarize(**one(last=last_by_day(points), level_before=None))  # type: ignore[arg-type]
    assert day.balance["exp"] == Balance(15861554 - 15479928, True)
    assert day.level == Level(70, 71)


def test_level_before_window_carried() -> None:
    last = {"level": {TODAY: 71.0}}
    [day] = summarize(**one(last=last, level_before=70.0))  # type: ignore[arg-type]
    assert day.level == Level(70, 71)
    [same] = summarize(**one(last={"level": {TODAY: 71.0}}, level_before=71.0))  # type: ignore[arg-type]
    assert same.level is None


def test_ledger_grouped_by_kind() -> None:
    ledger = [
        LedgerEntry(TODAY, "deed", {"exp": 158}, {"Пуговица": 1, "Нитки": 1}),
        LedgerEntry(TODAY, "deed", {"exp": 186, "containers_small": 1}, {"Пуговица": 2}),
        LedgerEntry(TODAY, "deed_start", {"money": -30}, {}),
        LedgerEntry(TODAY, "deed_start", {"money": -30}, {}),
        LedgerEntry(TODAY, "book", {"exp": 457}, {}),
        LedgerEntry(TODAY, "book", {"exp": 263}, {}),
        LedgerEntry(TODAY, "task", {"exp": 722, "money": 60, "trophies": 90}, {}),
        LedgerEntry(TODAY, "container", {"raw": 2}, {"Флюс": 1}),
        LedgerEntry(TODAY, "robbery", {"money": -340}, {}),
        LedgerEntry(date(2026, 9, 27), "book", {"exp": 1}, {}),
    ]
    [day] = summarize(**one(ledger=ledger))  # type: ignore[arg-type]
    assert day.trophies == 90
    assert day.items == {"Пуговица": 3, "Нитки": 1, "Флюс": 1}
    assert day.income == (
        KindSum("book", 2, {"exp": 720}),
        KindSum("container", 1, {"raw": 2}),
        KindSum("task", 1, {"exp": 722, "money": 60, "trophies": 90}),
    )
    assert day.losses == (
        KindSum("robbery", 1, {"money": -340}),
        KindSum("deed_start", 2, {"money": -60}),
    )


def test_ledger_row_without_amounts_counts_as_event() -> None:
    """Итог поездки из одних бонус-предметов: ряд без сумм — событие в счётчике, суммы пустые."""
    ledger = [
        LedgerEntry(TODAY, "trip", {}, {"Флюс": 1}),
        LedgerEntry(TODAY, "trip", {"knowledge": 16}, {}),
        LedgerEntry(TODAY, "trip_start", {"raw": -10, "money": -20}, {}),
    ]
    [day] = summarize(**one(ledger=ledger))  # type: ignore[arg-type]
    assert day.income == (KindSum("trip", 2, {"knowledge": 16}),)
    assert day.losses == (KindSum("trip_start", 1, {"raw": -10, "money": -20}),)
    assert day.items == {"Флюс": 1}


def test_gadget_rows_are_losses_without_craft_items() -> None:
    """Покупка и заточка гаджетов — траты; их предметы (название, `up:<слот>`, `ok`/`fail`) не
    предметы крафта."""
    ledger = [
        LedgerEntry(TODAY, "gadget_buy", {"money": -3}, {"Китайская мобила": 1}),
        LedgerEntry(TODAY, "gadget_upgrade", {"upgrades_white": -1}, {"up:right": 1, "ok": 1}),
        LedgerEntry(TODAY, "gadget_upgrade", {"upgrades_white": -1}, {"up:right": 1, "fail": 1}),
        LedgerEntry(TODAY, "deed", {"exp": 158}, {"Пуговица": 1}),
    ]
    [day] = summarize(**one(ledger=ledger))  # type: ignore[arg-type]
    assert day.items == {"Пуговица": 1}
    assert day.income == ()
    assert day.losses == (
        KindSum("gadget_buy", 1, {"money": -3}),
        KindSum("gadget_upgrade", 2, {"upgrades_white": -2}),
    )


def test_days_today_first_and_partial() -> None:
    since = date(2026, 9, 26)
    out = summarize(**one(days=4, ledger_since=since))  # type: ignore[arg-type]
    assert [d.day for d in out] == [TODAY - timedelta(days=i) for i in range(4)]
    # Сегодня — неполный; день запуска журнала и раньше — разовое неизвестно.
    assert [d.partial for d in out] == [True, False, True, True]
    empty = summarize(**one(days=2, ledger_since=None))  # type: ignore[arg-type]
    assert [d.partial for d in empty] == [True, True]

"""Итоги дня: изменение баланса по метрикам с правилом покрытия и журнал прихода по суткам МСК."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from app.engine.daily import (
    BALANCE_KEYS,
    LOSS_ORDER,
    NOT_CRAFT,
    AccountTotals,
    Balance,
    Delta,
    KindSum,
    LedgerDays,
    LedgerEntry,
    Level,
    TotalDelta,
    account_totals,
    last_by_day,
    merge_totals,
    period_balance,
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


def test_ledger_folded_as_read_matches_list() -> None:
    """Журнал, свёрнутый по суткам по мере чтения, даёт те же итоги, что и список записей,
    вплоть до порядка ключей (первое появление): от него зависит тело ответа."""
    ledger = [
        LedgerEntry(TODAY, "deed", {"exp": 158}, {"Пуговица": 1, "Нитки": 1}),
        LedgerEntry(TODAY, "book", {"exp": 457}, {}),
        LedgerEntry(TODAY, "deed_start", {"money": -30}, {}),
        LedgerEntry(TODAY, "book", {"knowledge": 3, "exp": 263}, {"Флюс": 1}),
        LedgerEntry(TODAY, "gadget_upgrade", {"upgrades_white": -1}, {"up:right": 1}),
        LedgerEntry(TODAY, "deed_start", {"raw": -1, "money": -30}, {}),
        LedgerEntry(TODAY, "task", {"trophies": 90}, {}),
        LedgerEntry(date(2026, 9, 27), "book", {"exp": 1}, {"Нитки": 2}),
    ]
    folded = LedgerDays()
    for entry in ledger:
        folded.add(entry)
    assert folded == LedgerDays.of(ledger)
    want = summarize(**one(days=2, ledger=ledger))  # type: ignore[arg-type]
    got = summarize(**one(days=2, ledger=folded))  # type: ignore[arg-type]
    assert got == want
    [day, _] = got
    assert list(day.items) == ["Пуговица", "Нитки", "Флюс"]
    assert [list(s.amounts) for s in day.income] == [["exp", "knowledge"], ["trophies"]]
    assert [list(s.amounts) for s in day.losses] == [["money", "raw"], ["upgrades_white"]]
    assert (day.trophies, got[1].items) == (90, {"Нитки": 2})


def test_days_today_first_and_partial() -> None:
    since = date(2026, 9, 26)
    out = summarize(**one(days=4, ledger_since=since))  # type: ignore[arg-type]
    assert [d.day for d in out] == [TODAY - timedelta(days=i) for i in range(4)]
    # Сегодня — неполный; день запуска журнала и раньше — разовое неизвестно.
    assert [d.partial for d in out] == [True, False, True, True]
    empty = summarize(**one(days=2, ledger_since=None))  # type: ignore[arg-type]
    assert [d.partial for d in empty] == [True, True]


# --- итоги периода по нескольким аккаунтам ---


def test_period_balance_last_in_window_minus_last_before() -> None:
    got = period_balance(
        {"money": 150.0, "exp": 7.0}, {"money": 100.0, "exp": 10.0}, window_covered=True
    )
    assert got["money"] == Delta(50, True)
    assert got["exp"] == Delta(-3, True)


def test_period_balance_missing_point_is_not_zero() -> None:
    got = period_balance(
        {"money": 5.0, "exp": None, "raw": 1.0}, {"money": None, "exp": 3.0}, window_covered=True
    )
    # Нет точки до окна, нет точки в окне, ключа нет совсем — везде «нет данных», не ноль.
    assert got["money"] == got["exp"] == got["raw"] == Delta(None, False)
    assert set(got) == set(BALANCE_KEYS)


def test_period_balance_window_not_covered_hides_everything() -> None:
    got = period_balance({"money": 150.0}, {"money": 100.0}, window_covered=False)
    assert all(d == Delta(None, False) for d in got.values())


def _part(money: Delta, **kw: object) -> AccountTotals:
    balance = {k: Delta(None, False) for k in BALANCE_KEYS} | {"money": money}
    defaults: dict[str, object] = {
        "levels": 0,
        "trophies": 0,
        "items": {},
        "income": (),
        "losses": (),
    }
    return AccountTotals(balance, **{**defaults, **kw})  # type: ignore[arg-type]


def test_merge_totals_counts_only_covered_accounts() -> None:
    parts = [_part(Delta(100, True)), _part(Delta(None, False)), _part(Delta(-30, True))]
    total = merge_totals(parts)
    assert total.balance["money"] == TotalDelta(70, False, 2)
    # Все пять остальных ключей не покрыты ни у кого: ни нуля, ни числа.
    assert total.balance["exp"] == TotalDelta(None, False, 0)


def test_merge_totals_all_covered_and_none_accounts() -> None:
    both = merge_totals([_part(Delta(1, True)), _part(Delta(2, True))])
    assert both.balance["money"] == TotalDelta(3, True, 2)
    nobody = merge_totals([])
    assert nobody.balance["money"] == TotalDelta(None, False, 0)
    assert (nobody.levels, nobody.trophies, nobody.items, nobody.income, nobody.losses) == (
        0,
        0,
        {},
        (),
        (),
    )


def test_merge_totals_sums_ledger_parts_and_levels() -> None:
    a = _part(
        Delta(1, True),
        levels=2,
        trophies=90,
        items={"Нитки": 1},
        income=(KindSum("battle", 2, {"money": 100}),),
        losses=(KindSum("hotel", 1, {"money": -10}),),
    )
    b = _part(
        Delta(1, True),
        levels=1,
        trophies=10,
        items={"Нитки": 2, "Флюс": 1},
        income=(KindSum("book", 1, {"exp": 5}), KindSum("battle", 1, {"money": 50, "exp": 3})),
        losses=(KindSum("hotel", 2, {"money": -20}), KindSum("robbery", 1, {"money": -5})),
    )
    total = merge_totals([a, b])
    assert (total.levels, total.trophies, total.items) == (3, 100, {"Нитки": 3, "Флюс": 1})
    assert total.income == (
        KindSum("book", 1, {"exp": 5}),
        KindSum("battle", 3, {"money": 150, "exp": 3}),
    )
    assert total.losses == (
        KindSum("robbery", 1, {"money": -5}),
        KindSum("hotel", 3, {"money": -30}),
    )


def test_merge_totals_levels_unknown_only_when_nobody_knows() -> None:
    assert merge_totals([_part(Delta(1, True), levels=None)]).levels is None
    assert (
        merge_totals([_part(Delta(1, True), levels=None), _part(Delta(1, True), levels=2)]).levels
        == 2
    )


def _window(**kw: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "last": {},
        "first": date(2026, 9, 26),
        "before": {},
        "window_covered": True,
    }
    return {**defaults, **kw}


def test_account_totals_balance_levels_and_ledger_from_days() -> None:
    last = {
        "money": {date(2026, 9, 25): 10.0, date(2026, 9, 26): 40.0, date(2026, 9, 28): 70.0},
        "level": {date(2026, 9, 27): 8.0},
    }
    ledger = [
        LedgerEntry(date(2026, 9, 26), "battle", {"money": 5}, {}),
        LedgerEntry(date(2026, 9, 28), "battle", {"money": 7, "trophies": 1}, {"Флюс": 1}),
        LedgerEntry(date(2026, 9, 28), "hotel", {"money": -3}, {}),
    ]
    days = summarize(**one(days=3, last=last, ledger=ledger))  # type: ignore[arg-type]
    got = account_totals(days, **_window(last=last, before={"money": 4.0, "level": 5.0}))  # type: ignore[arg-type]
    assert got.balance["money"] == Delta(66, True)
    assert got.balance["exp"] == Delta(None, False)
    assert got.levels == 3
    assert got.trophies == 1
    assert got.items == {"Флюс": 1}
    assert got.income == (KindSum("battle", 2, {"money": 12, "trophies": 1}),)
    assert got.losses == (KindSum("hotel", 1, {"money": -3}),)


def test_account_totals_level_without_change_or_baseline_is_zero() -> None:
    days = summarize(**one(days=1))  # type: ignore[arg-type]
    quiet = account_totals(days, **_window(last={}, before={"level": 5.0}))  # type: ignore[arg-type]
    assert quiet.levels == 0
    first_seen = account_totals(
        days,
        **_window(last={"level": {TODAY: 5.0}}),  # type: ignore[arg-type]
    )
    assert first_seen.levels == 0


def test_account_totals_not_covered_window_hides_balance_and_levels_only() -> None:
    ledger = [LedgerEntry(TODAY, "battle", {"money": 7}, {})]
    days = summarize(**one(days=1, ledger=ledger))  # type: ignore[arg-type]
    got = account_totals(
        days,
        **_window(  # type: ignore[arg-type]
            first=TODAY, last={"money": {TODAY: 9.0}}, before={"money": 1.0}, window_covered=False
        ),
    )
    assert got.balance["money"] == Delta(None, False)
    assert got.levels is None
    assert got.income == (KindSum("battle", 1, {"money": 7}),)


def _group(entries: list[LedgerEntry]) -> LedgerDays:
    """Журнал так, как его отдаёт свёртка в SQL: по (день, вид) с числом и суммами."""
    folded = LedgerDays()
    groups: dict[tuple[date, str], list[LedgerEntry]] = {}
    for e in entries:
        groups.setdefault((e.day, e.kind), []).append(e)
    for (day, kind), rows in groups.items():
        amounts: dict[str, int] = {}
        items: dict[str, int] = {}
        for r in rows:
            for k, v in r.amounts.items():
                amounts[k] = amounts.get(k, 0) + v
            for k, v in r.items.items():
                items[k] = items.get(k, 0) + v
        folded.add_group(day, kind, len(rows), amounts, items)
    return folded


def test_add_group_matches_add_for_every_kind() -> None:
    kinds = sorted({*NOT_CRAFT, "deed", *LOSS_ORDER, "battle", "book", "task", "mystery"})
    entries: list[LedgerEntry] = []
    for i, kind in enumerate(kinds):
        for day in (TODAY, TODAY - timedelta(days=1)):
            for n in range(3):
                entries.append(
                    LedgerEntry(
                        day,
                        kind,
                        {"money": -(i + n), "trophies": n} if n else {},
                        {"Флюс": n + 1, f"item{i}": 1} if n != 1 else {},
                    )
                )
    assert _group(entries) == LedgerDays.of(entries)
    assert {k for e in entries for k in (e.kind,)} >= {"deed", "hotel", "gadget_buy"}

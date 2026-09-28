"""Эффекты редьюсера для журнала прихода: ровно один на применённое изменение."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from app.engine.gametime import MSK
from app.engine.state.ledger import Effect
from app.engine.state.reducer import StateReducer
from app.engine.types import IncomingMessage
from tests.engine.state.helpers import PARSER, at, feed, fixture_at
from tests.fixtures import game_msg, game_versions

PROFILE = 3624478


def reduce(
    reducer: StateReducer, state: dict[str, Any], msg: IncomingMessage
) -> tuple[dict[str, Any], tuple[Effect, ...]]:
    return reducer.reduce(state, msg, PARSER.parse(msg))


def effects_of(family: str, msg_id: int, minutes: float = 1) -> tuple[Effect, ...]:
    reducer = StateReducer()
    state = feed(reducer, {}, "profile", PROFILE, 0)
    return reduce(reducer, state, fixture_at(family, msg_id, minutes))[1]


def test_outcome_effect_once_per_message_even_on_edit() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "profile", PROFILE, 0)
    book = fixture_at("items", 3516680, 1)
    state, effects = reduce(reducer, state, book)
    assert effects == (Effect("book", {"exp": 457}),)
    _, again = reduce(reducer, state, book)
    edit = replace(book, revision=book.revision + 1, kind="edit", date=at(2))
    _, edited = reduce(reducer, state, edit)
    assert again == () and edited == ()


def test_effect_even_when_snapshot_already_counted_it() -> None:
    # Профиль новее итога учёл его (опыт не прибавляется), но итог в журнал прихода всё равно идёт.
    reducer = StateReducer()
    state = feed(reducer, {}, "profile", PROFILE, 5)
    _, effects = reduce(reducer, state, fixture_at("items", 3516680, 6, created=1))
    assert effects == (Effect("book", {"exp": 457}),)


def test_deed_start_and_finish_with_items() -> None:
    assert effects_of("activities", 3517276) == (Effect("deed_start", {"money": -30}),)
    assert effects_of("activities", 3517279) == (
        Effect("deed", {"exp": 158}, {"Пуговица": 1, "Нитки": 1}),
    )
    assert effects_of("activities", 3610665) == (
        Effect(
            "deed",
            {"exp": 233, "containers_small": 1},
            {"Шнурок": 1, "Льняная ткань": 1},
        ),
    )
    assert effects_of("activities", 3524596) == (
        Effect("deed", {"exp": 247}, {"Пуговица": 1, "Нитки": 1}),
    )


def test_one_offs() -> None:
    assert effects_of("items", 3516678) == (Effect("card", {"money": 597}),)
    assert effects_of("items", 3517262) == (Effect("prizebox", {"money": 300}),)
    assert effects_of("items", 3611233) == (
        Effect(
            "container",
            {"details": 6, "upgrades_white": 4, "upgrades_blue": 1},
            {
                "Нитки": 4,
                "Пьезодинамик": 4,
                "Кусок ткани": 1,
                "Флюс": 4,
                "Конденсатор": 1,
                "Диод": 2,
                "Молния": 1,
                "Микроконтроллер": 2,
                "Шнурок": 3,
                "Мех": 1,
            },
        ),
    )
    assert effects_of("daily", 3625831) == (
        Effect("task", {"exp": 722, "money": 60, "trophies": 90}),
    )
    assert effects_of("stocks", 3621194) == (Effect("dividends", {"money": 1065}),)
    assert effects_of("bulls", 3624431) == (
        Effect("bulls", {"exp": 228, "money": 150, "knowledge": 1}),
    )


def test_spends_and_losses() -> None:
    assert effects_of("sleep", 3525189) == (Effect("hotel", {"money": -210}),)
    assert effects_of("sleep", 3520076) == (Effect("robbery_fight", {"exp": 125, "money": -4}),)
    assert effects_of("sleep", 3517730) == (Effect("sleep", {"exp": 135}),)


def test_nothing_for_screens() -> None:
    assert effects_of("profile", PROFILE) == ()
    assert effects_of("items", 3516676) == ()


def test_lottery_by_edits_one_effect_per_increment() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "lottery", 3626217, 0)
    opened, clicked = game_versions("lottery", 3626219)
    state, first = reduce(reducer, state, replace(opened, date=at(0.2), created_at=at(0.2)))
    assert first == ()
    edit = replace(clicked, date=at(0.43), created_at=at(0.2))
    state, bought = reduce(reducer, state, edit)
    assert bought == (Effect("lottery_tickets", {"money": -30}),)
    _, again = reduce(reducer, state, edit)
    assert again == ()


def test_lottery_buy_all_once() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "lottery", 3625282, 1)
    bought = fixture_at("lottery", 3625321, 2)
    state, effects = reduce(reducer, state, bought)
    assert effects == (
        Effect(
            "lottery_tickets",
            {"money": -300, "knowledge": -28, "details": -56, "raw": -28},
        ),
    )
    assert reduce(reducer, state, bought)[1] == ()


def test_gorbushka_ticket_only_on_proven_purchase() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "profile", PROFILE, 0)
    state = feed(reducer, state, "gorbushka", 3537930, 1)
    meeting = fixture_at("gorbushka", 3516738, 3, created=2)
    state, effects = reduce(reducer, state, meeting)
    assert effects == (Effect("gorbushka_ticket", {"money": -120, "knowledge": -20}),)
    again = replace(meeting, msg_id=meeting.msg_id + 1, date=at(4), created_at=at(4))
    assert reduce(reducer, state, again)[1] == ()


def test_factory_report_dated_by_battle_once_per_day() -> None:
    reducer = StateReducer()
    report = game_msg("crew", 3620025)
    state, effects = reduce(reducer, {}, report)
    battle = datetime(2026, 9, 9, 18, 30, tzinfo=MSK).astimezone(UTC)
    assert effects == (
        Effect(
            "factory",
            {
                "exp": 281,
                "money": 347,
                "details": 10,
                "upgrades_white": 2,
                "upgrades_blue": 1,
                "upgrades_red": 1,
            },
            at=battle,
        ),
    )
    # Второй /fb — новое сообщение с тем же отчётом.
    second = replace(report, msg_id=report.msg_id + 5)
    assert reduce(reducer, state, second)[1] == ()


def test_battle_report_dated_by_battle_hour() -> None:
    reducer = StateReducer()
    report = game_msg("screens", 3613861)
    state, effects = reduce(reducer, {}, report)
    # Отчёт о битве в 22 часа запрошен 24.08 в 08:46 — это битва 23.08.
    battle = datetime(2026, 8, 23, 22, 0, tzinfo=MSK).astimezone(UTC)
    assert effects == (Effect("battle", {"exp": 1, "money": -191}, at=battle),)
    assert reduce(reducer, state, replace(report, msg_id=report.msg_id + 1))[1] == ()

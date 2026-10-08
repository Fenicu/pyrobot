from dataclasses import replace
from typing import Any

import pytest

from app.engine.state.ledger import Effect
from app.engine.state.model import load_state
from app.engine.state.reducer import StateReducer
from app.engine.types import IncomingMessage
from tests.engine.parsing.test_metro import (
    COLLAPSED,
    COLLAPSED_EMPTY,
    CONTINUE,
    EARLY_FINISHED_1007,
    METRO_LEFT,
    NO_STAMINA,
)
from tests.engine.state.helpers import PARSER, at, feed, fixture_at, value
from tests.fixtures import game_msg, game_versions

RUN = game_versions("metro", 3624441)


def _version(reducer: StateReducer, state: dict[str, Any], n: int, minutes: float) -> dict:
    """Версия сообщения забега: создано на 2-й минуте, правка — в `minutes`."""
    msg = replace(RUN[n], date=at(minutes), created_at=at(2))
    return reducer.apply(state, msg, PARSER.parse(msg))


def _before_metro(reducer: StateReducer) -> dict:
    state = feed(reducer, {}, "profile", 3624478, 0)
    state = feed(reducer, state, "food", 3624997, 0.5)
    return feed(reducer, state, "activities", 3624750, 1)


def test_entrance_means_metro_ready_and_entering_costs_motivation() -> None:
    reducer = StateReducer()
    state = _version(reducer, _before_metro(reducer), 0, 2)
    assert value(state, "metro_ready_at") == "2026-09-26T09:02:00Z"
    assert value(state, "motivation") == 72
    for n, minute in ((1, 3), (2, 3.1), (4, 3.2)):
        state = _version(reducer, state, n, minute)
    # Списание 2🔥 — один раз на сообщение, сколько бы раз ни правился экран бафов.
    assert value(state, "motivation") == 70


def test_entrance_without_motivation_for_entry_sets_it() -> None:
    reducer = StateReducer()
    short = replace(
        RUN[0], text=(RUN[0].text or "").replace("У тебя 73🔥", "У тебя 1🔥"), date=at(2)
    )
    state = reducer.apply(_before_metro(reducer), short, PARSER.parse(short))
    assert value(state, "motivation") == 1


def test_map_fight_and_traps_track_stamina() -> None:
    reducer = StateReducer()
    state = _version(reducer, _before_metro(reducer), 5, 4)
    assert value(state, "stamina") == 88
    state = _version(reducer, state, 48, 5)
    assert value(state, "stamina") == 55
    state = _version(reducer, state, 97, 6)
    assert value(state, "stamina") == 44
    state = _version(reducer, state, 168, 7)
    assert value(state, "stamina") == 0


def test_loot_counts_only_at_exit() -> None:
    reducer = StateReducer()
    before = _before_metro(reducer)
    state = before
    for n, minute in ((21, 4), (48, 5), (279, 6)):
        state = _version(reducer, state, n, minute)
    assert value(state, "money") == value(before, "money")
    state = _version(reducer, state, 532, 20)
    assert value(state, "money") == value(before, "money") + 157
    assert value(state, "details") == value(before, "details") + 13
    assert value(state, "knowledge") == value(before, "knowledge") + 6
    assert value(state, "raw") == value(before, "raw") + 9
    assert value(state, "upgrades")["white"] == value(before, "upgrades")["white"] + 2
    assert value(state, "stamina") == 100
    stock, old = value(state, "food_stock"), value(before, "food_stock")
    assert [stock[k]["count"] - old[k]["count"] for k in ("burger", "hotdog", "pizza")] == [
        3,
        3,
        4,
    ]
    assert value(state, "metro_ready_at") == "2026-09-27T01:20:00Z"
    assert state["metro_ready_at"]["src"] == "derived"
    again = _version(reducer, state, 532, 21)
    assert value(again, "money") == value(state, "money")


def test_cooldown_refusal_sets_ready_time() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "metro", 3624531, 10)
    assert value(state, "metro_ready_at") == "2026-09-27T00:40:00Z"
    assert load_state(state).metro_ready_at is not None


def test_current_run_message_tracked_until_exit() -> None:
    reducer = StateReducer()
    state = _version(reducer, _before_metro(reducer), 0, 2)
    assert value(state, "metro_message") is None
    state = _version(reducer, state, 1, 3)
    assert value(state, "metro_message")["message_id"] == 3624441
    state = _version(reducer, state, 48, 5)
    assert state["metro_message"]["at"] == "2026-09-26T09:05:00Z"
    state = _version(reducer, state, 532, 20)
    # Итог показан, но выход подтверждает только ответ игры вне метро.
    pending = value(state, "metro_message")
    assert pending["message_id"] == 3624441
    assert pending["exit_at"] == "2026-09-26T09:20:00Z"
    assert pending["exit_loot"]["money"] == 157
    state = feed(reducer, state, "profile", 3624478, 21)
    assert value(state, "metro_message") is None and "metro_message" in state


def test_unknown_run_screen_blocks_resume_until_known_one() -> None:
    reducer = StateReducer()
    state = _version(reducer, _before_metro(reducer), 5, 4)
    unknown = replace(RUN[5], text="🔋88%\nчто-то новое", date=at(5), created_at=at(2))
    state = reducer.apply(state, unknown, PARSER.parse(unknown))
    assert state["metro_message"]["src"] == "doubtful"
    assert value(state, "metro_message")["message_id"] == 3624441
    state = _version(reducer, state, 7, 6)
    assert state["metro_message"]["src"] == "screen"


def test_no_stamina_screen_is_a_run_screen() -> None:
    reducer = StateReducer()
    state = _version(reducer, _before_metro(reducer), 5, 4)
    stuck = replace(RUN[5], text=NO_STAMINA, inline=CONTINUE, date=at(5), created_at=at(2))
    state = reducer.apply(state, stuck, PARSER.parse(stuck))
    assert state["metro_message"]["src"] == "screen"
    assert value(state, "metro_message")["message_id"] == 3624441
    assert state["metro_message"]["at"] == "2026-09-26T09:05:00Z"


def test_run_keeps_battle_known_at_its_start() -> None:
    # Выброс считается по битве, известной на входе: свежий профиль после неё показывает уже
    # следующую битву, а забег её не пережидает.
    reducer = StateReducer()
    state = _version(reducer, _before_metro(reducer), 1, 3)
    entered_with = state["battle_at"]
    assert value(state, "metro_message") == {
        "message_id": 3624441,
        "battle_at": entered_with,
        "exit_at": None,
        "exit_loot": {},
    }
    state = feed(reducer, state, "profile", 3624478, 30)
    assert state["battle_at"] != entered_with
    state = _version(reducer, state, 48, 31)
    assert value(state, "metro_message")["battle_at"] == entered_with
    state = _version(reducer, state, 532, 40)
    assert value(state, "metro_message")["exit_at"] is not None
    again = replace(RUN[1], msg_id=3700000, date=at(50), created_at=at(50))
    state = reducer.apply(state, again, PARSER.parse(again))
    assert value(state, "metro_message") == {
        "message_id": 3700000,
        "battle_at": state["battle_at"],
        "exit_at": None,
        "exit_loot": {},
    }


def _entrance_later(reducer: StateReducer, state: dict[str, Any], minutes: float) -> dict:
    entrance = replace(RUN[0], msg_id=3700000, date=at(minutes), created_at=at(minutes))
    return reducer.apply(state, entrance, PARSER.parse(entrance))


def test_entrance_means_outside_metro_and_clears_hanging_run() -> None:
    # Итог выхода не распознан — отметка забега повисла; экран входа игра показывает только
    # снаружи метро.
    reducer = StateReducer()
    state = _version(reducer, _before_metro(reducer), 5, 4)
    unknown = replace(RUN[5], text="🔋88%\nчто-то новое", date=at(5), created_at=at(2))
    state = reducer.apply(state, unknown, PARSER.parse(unknown))
    assert state["metro_message"]["src"] == "doubtful"
    state = _entrance_later(reducer, state, 50)
    assert value(state, "metro_message") is None
    assert state["metro_message"]["at"] == "2026-09-26T09:50:00Z"
    assert value(state, "metro_ready_at") == "2026-09-26T09:50:00Z"


def test_entrance_without_run_leaves_run_mark_absent() -> None:
    reducer = StateReducer()
    before = _before_metro(reducer)
    assert before["metro_message"] is None
    state = _entrance_later(reducer, before, 50)
    assert state["metro_message"] is None
    assert value(state, "metro_ready_at") == "2026-09-26T09:50:00Z"


def test_collapse_credits_what_was_given_and_ends_run() -> None:
    reducer = StateReducer()
    before = _before_metro(reducer)
    state = _version(reducer, before, 5, 4)
    assert value(state, "metro_message")["message_id"] == 3624441
    kick = replace(
        RUN[532], msg_id=3700001, text=COLLAPSED, inline=(), date=at(30), created_at=at(30)
    )
    state, effects = reducer.reduce(state, kick, PARSER.parse(kick))
    assert value(state, "money") == value(before, "money") + 104
    assert value(state, "details") == value(before, "details") + 19
    assert value(state, "knowledge") == value(before, "knowledge") + 4
    assert value(state, "raw") == value(before, "raw") + 3
    assert value(state, "upgrades")["white"] == value(before, "upgrades")["white"] + 1
    # Строки выносливости в тексте обвала нет: 🔋 остаётся как была на карте.
    assert value(state, "stamina") == 88
    stock, old = value(state, "food_stock"), value(before, "food_stock")
    assert [stock[k]["count"] - old[k]["count"] for k in ("burger", "hotdog", "pizza")] == [
        1,
        4,
        3,
    ]
    assert value(state, "metro_message") is None and "metro_message" in state
    assert value(state, "metro_ready_at") == "2026-09-27T01:30:00Z"
    assert state["metro_ready_at"]["src"] == "derived"
    assert effects == (
        Effect(
            "metro",
            {"money": 104, "knowledge": 4, "details": 19, "raw": 3, "upgrades_white": 1},
        ),
    )
    again = replace(kick, revision=1, date=at(31))
    assert value(reducer.apply(state, again, PARSER.parse(again)), "money") == value(
        state, "money"
    )


def test_collapse_with_nothing_found_still_ends_run() -> None:
    reducer = StateReducer()
    before = _before_metro(reducer)
    state = _version(reducer, before, 5, 4)
    kick = replace(
        RUN[532], msg_id=3700002, text=COLLAPSED_EMPTY, inline=(), date=at(30), created_at=at(30)
    )
    state, effects = reducer.reduce(state, kick, PARSER.parse(kick))
    assert value(state, "money") == value(before, "money")
    assert value(state, "metro_message") is None and "metro_message" in state
    assert value(state, "metro_ready_at") == "2026-09-27T01:30:00Z"
    assert effects == ()


def _early_finish_1007(reducer: StateReducer, state: dict[str, Any], minutes: float) -> dict:
    msg = replace(
        RUN[532], text=EARLY_FINISHED_1007, inline=(), date=at(minutes), created_at=at(2)
    )
    return reducer.apply(state, msg, PARSER.parse(msg))


def _kick(minutes: float) -> Any:
    return replace(
        RUN[532],
        msg_id=3700001,
        text=COLLAPSED,
        inline=(),
        date=at(minutes),
        created_at=at(minutes),
    )


def test_run_screen_after_finish_means_still_running() -> None:
    reducer = StateReducer()
    state = _version(reducer, _before_metro(reducer), 5, 4)
    state = _version(reducer, state, 532, 20)
    state = _version(reducer, state, 7, 21)
    assert value(state, "metro_message")["exit_at"] is None
    assert value(state, "metro_message")["exit_loot"] == {}


def test_metro_and_unrecognized_messages_do_not_confirm_exit() -> None:
    reducer = StateReducer()
    state = _version(reducer, _before_metro(reducer), 5, 4)
    state = _version(reducer, state, 532, 20)
    other = replace(RUN[5], msg_id=3700005, text="что-то новое", inline=(), date=at(21))
    state = reducer.apply(state, other, PARSER.parse(other))
    assert value(state, "metro_message")["exit_at"] is not None


GIFT = "👍Ура! ☂️MstrGreen подарил тебе 🍊мандаринки +3 шт."


@pytest.mark.parametrize(
    "push",
    [
        # Мандарины пришли в 04:30 07.10, пока персонаж сидел в метро.
        replace(
            RUN[5],
            msg_id=3700006,
            text="👍Ура! ☂️MstrGreen подарил тебе 🍊мандаринки +3 шт.",
            inline=(),
        ),
        replace(game_msg("sleep", 3420238), msg_id=3700007),
    ],
    ids=["tangerine_gift", "robbery_alert"],
)
def test_game_pushes_do_not_confirm_exit(push: IncomingMessage) -> None:
    reducer = StateReducer()
    state = _version(reducer, _before_metro(reducer), 5, 4)
    state = _version(reducer, state, 532, 20)
    msg = replace(push, date=at(21), created_at=at(21))
    events = PARSER.parse(msg)
    assert events and not any(type(e).__name__ == "Unrecognized" for e in events)
    state = reducer.apply(state, msg, events)
    assert value(state, "metro_message")["exit_at"] is not None


def test_metro_cooldown_refusal_confirms_exit() -> None:
    reducer = StateReducer()
    state = _version(reducer, _before_metro(reducer), 5, 4)
    state = _version(reducer, state, 532, 20)
    state = feed(reducer, state, "metro", 3624531, 21)
    assert value(state, "metro_message") is None


def test_answer_in_other_chat_does_not_confirm_exit() -> None:
    reducer = StateReducer(game_chat_id=RUN[0].chat_id)
    state = _version(reducer, _before_metro(reducer), 5, 4)
    state = _version(reducer, state, 532, 20)
    elsewhere = replace(fixture_at("profile", 3624478, 21), chat_id=-100500)
    state = reducer.apply(
        state, elsewhere, PARSER.parse(replace(elsewhere, chat_id=RUN[0].chat_id))
    )
    assert value(state, "metro_message")["exit_at"] is not None
    answer = fixture_at("profile", 3624478, 22)
    state = reducer.apply(state, answer, PARSER.parse(answer))
    assert value(state, "metro_message") is None


def test_answer_older_than_finish_does_not_confirm_exit() -> None:
    reducer = StateReducer()
    state = _version(reducer, _before_metro(reducer), 5, 4)
    state = _version(reducer, state, 532, 20)
    state = feed(reducer, state, "profile", 3624478, 19)
    assert value(state, "metro_message")["exit_at"] is not None


def test_kick_after_unconfirmed_exit_reverses_false_finish() -> None:
    """07.10: итог «досрочно» начислил половину найденного, но персонаж остался в метро; выброс
    посчитан от полного «Найдено». Ложный итог снимается встречным эффектом, ресурсы —
    сомнительные до следующего профиля и еды."""
    reducer = StateReducer()
    before = _before_metro(reducer)
    state = _version(reducer, before, 5, 4)
    state = _early_finish_1007(reducer, state, 20)
    assert value(state, "money") == value(before, "money") + 156
    state, effects = reducer.reduce(state, _kick(30), PARSER.parse(_kick(30)))
    reversal = {"money": -156, "knowledge": -6, "details": -29, "raw": -5, "upgrades_white": -2}
    assert effects == (
        Effect("metro", reversal, key="metro_reversal:3624441:2026-09-26T09:20:00+00:00"),
        Effect(
            "metro", {"money": 104, "knowledge": 4, "details": 19, "raw": 3, "upgrades_white": 1}
        ),
    )
    assert value(state, "money") == value(before, "money") + 104
    assert value(state, "knowledge") == value(before, "knowledge") + 4
    assert value(state, "upgrades")["white"] == value(before, "upgrades")["white"] + 1
    stock, old = value(state, "food_stock"), value(before, "food_stock")
    assert [stock[k]["count"] - old[k]["count"] for k in ("burger", "hotdog", "pizza")] == [
        1,
        4,
        3,
    ]
    for name in ("money", "knowledge", "raw", "details", "food_stock"):
        assert state[name]["src"] == "doubtful", name
    assert value(state, "metro_message") is None
    assert value(state, "metro_ready_at") == "2026-09-27T01:30:00Z"


def test_kick_after_confirmed_exit_is_not_reversed() -> None:
    # Выход подтвердил ответ игры (профиль после /main): итог мог быть настоящим.
    reducer = StateReducer()
    state = _version(reducer, _before_metro(reducer), 5, 4)
    state = _early_finish_1007(reducer, state, 20)
    state = feed(reducer, state, "profile", 3624478, 21)
    money = value(state, "money")
    state, effects = reducer.reduce(state, _kick(30), PARSER.parse(_kick(30)))
    assert [e.amounts["money"] for e in effects] == [104]
    assert value(state, "money") == money + 104


def test_game_refusal_confirms_exit() -> None:
    # В забеге игра на команды молчит: любой её отказ — персонаж снаружи (07.10 опыт метро
    # мог дать уровень — /compact получил бы «Нажми /levelup»).
    reducer = StateReducer()
    state = _version(reducer, _before_metro(reducer), 5, 4)
    state = _version(reducer, state, 532, 20)
    state = feed(reducer, state, "refusals", 3532814, 21)
    assert value(state, "metro_message") is None


def test_metro_left_answer_confirms_exit() -> None:
    # «Ты уже покинул метро» — ответ игры на «🚇Метро» снаружи: выход подтверждён.
    reducer = StateReducer()
    state = _version(reducer, _before_metro(reducer), 5, 4)
    state = _version(reducer, state, 532, 20)
    left = replace(fixture_at("refusals", 3532814, 21), text=METRO_LEFT, inline=())
    state = reducer.apply(state, left, PARSER.parse(left))
    assert value(state, "metro_message") is None


def test_profile_older_than_finish_processed_late_does_not_confirm_exit() -> None:
    reducer = StateReducer()
    state = _version(reducer, _before_metro(reducer), 5, 4)
    state = _version(reducer, state, 532, 20)
    # Профиль от 19:30 при итоге в 20:00 — правки метро позже, отметка свежее.
    older = fixture_at("profile", 3624478, 19.5)
    state = reducer.apply(state, older, PARSER.parse(older))
    assert value(state, "metro_message")["exit_at"] is not None

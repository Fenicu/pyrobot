from dataclasses import replace
from typing import Any

from app.engine.state.model import load_state
from app.engine.state.reducer import StateReducer
from tests.engine.state.helpers import PARSER, at, feed, value
from tests.fixtures import game_versions

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


def test_run_keeps_battle_known_at_its_start() -> None:
    # Выброс считается по битве, известной на входе: свежий профиль после неё показывает уже
    # следующую битву, а забег её не пережидает.
    reducer = StateReducer()
    state = _version(reducer, _before_metro(reducer), 1, 3)
    entered_with = state["battle_at"]
    assert value(state, "metro_message") == {"message_id": 3624441, "battle_at": entered_with}
    state = feed(reducer, state, "profile", 3624478, 30)
    assert state["battle_at"] != entered_with
    state = _version(reducer, state, 48, 31)
    assert value(state, "metro_message")["battle_at"] == entered_with
    state = _version(reducer, state, 532, 40)
    assert value(state, "metro_message") is None
    again = replace(RUN[1], msg_id=3700000, date=at(50), created_at=at(50))
    state = reducer.apply(state, again, PARSER.parse(again))
    assert value(state, "metro_message") == {
        "message_id": 3700000,
        "battle_at": state["battle_at"],
    }

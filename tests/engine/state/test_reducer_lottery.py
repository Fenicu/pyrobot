from dataclasses import replace
from datetime import timedelta
from typing import Any

from app.engine.state.model import CharacterState, dump_state, load_state
from app.engine.state.reducer import StateReducer
from app.engine.types import IncomingMessage
from tests.engine.state.helpers import PARSER, at, feed, fixture_at, value
from tests.fixtures import game_versions

SCREEN, BOUGHT = 3625282, 3625321


def apply(reducer: StateReducer, state: dict[str, Any], msg: IncomingMessage) -> dict[str, Any]:
    return reducer.apply(state, msg, PARSER.parse(msg))


def edited(msg: IncomingMessage, old: str, new: str) -> IncomingMessage:
    assert msg.text is not None and old in msg.text
    return replace(msg, text=msg.text.replace(old, new))


def test_screen_is_a_snapshot_of_the_draw_and_resources() -> None:
    state = feed(StateReducer(), {}, "lottery", SCREEN, 1)
    lottery = value(state, "lottery")
    until = at(1) + timedelta(seconds=6960) - timedelta(minutes=10)
    assert lottery == {
        "draw": 3285,
        "until": until.isoformat().replace("+00:00", "Z"),
        "bought": {"money": 0, "knowledge": 0, "raw": 0, "details": 0},
        "limits": {"money": 10, "knowledge": 7, "raw": 7, "details": 7},
        "prices": {"money": 30, "knowledge": 4, "raw": 4, "details": 8},
        "short": {},
    }
    assert (value(state, "money"), value(state, "knowledge")) == (675, 21951)
    assert (value(state, "raw"), value(state, "details")) == (21324, 136669)


def test_buy_all_adds_to_known_draw_and_spends_once() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "lottery", SCREEN, 1)
    bought = fixture_at("lottery", BOUGHT, 2)
    state = apply(reducer, state, bought)
    again = apply(reducer, state, bought)
    lottery = again["lottery"]
    assert lottery["src"] == "derived"
    assert lottery["value"]["bought"] == {"money": 10, "knowledge": 7, "raw": 7, "details": 7}
    assert lottery["value"]["short"] == {}
    assert (value(again, "money"), value(again, "knowledge")) == (675 - 300, 21951 - 28)
    assert (value(again, "raw"), value(again, "details")) == (21324 - 28, 136669 - 56)


def test_buy_all_below_limit_marks_currency_short() -> None:
    reducer = StateReducer()
    poor = edited(fixture_at("lottery", SCREEN, 1), "💵Деньги: $675", "💵Деньги: $100")
    state = apply(reducer, {}, poor)
    three = edited(fixture_at("lottery", BOUGHT, 2), "💵За деньги: 10", "💵За деньги: 3")
    state = apply(reducer, state, edited(three, "Всего: 31 шт.", "Всего: 24 шт."))
    lottery = value(state, "lottery")
    assert lottery["bought"]["money"] == 3
    assert lottery["short"] == {"money": 10}
    # Новый экран того же тиража сохраняет нехватку: это итог попытки, а не экрана.
    state = feed(reducer, state, "lottery", SCREEN, 3)
    assert value(state, "lottery")["short"] == {"money": 10}


def test_other_draw_replaces_snapshot_with_unknown() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "lottery", SCREEN, 1)
    state = feed(reducer, state, "lottery", 3536223, 2)
    lottery = state["lottery"]
    assert (lottery["value"]["draw"], lottery["value"]["bought"]) == (3063, None)
    assert lottery["src"] == "derived"
    assert state["money"]["src"] == "doubtful"


def test_screen_of_the_same_second_makes_increment_doubtful() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "lottery", SCREEN, 1)
    state = feed(reducer, state, "lottery", BOUGHT, 1)
    assert state["lottery"]["src"] == "doubtful"
    assert value(state, "lottery")["bought"]["money"] == 0
    assert state["money"]["src"] == "doubtful"


def test_older_answer_does_not_touch_newer_screen() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "lottery", SCREEN, 5)
    later = feed(reducer, state, "lottery", BOUGHT, 2)
    assert value(later, "lottery")["bought"]["money"] == 0
    assert later["money"] == state["money"]


def test_currency_screen_updates_same_draw() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "lottery", SCREEN, 1)
    # «Розыгрыш через 1ч. 39 мин.» через 17 минут после экрана с «1ч. 56 мин.» — тот же тираж.
    state = feed(reducer, state, "lottery", 3385821, 18)
    lottery = value(state, "lottery")
    assert lottery["bought"]["money"] == 10
    # Куплено больше известного (10 против 0) — разница списана, нехватка — после неё.
    assert value(state, "money") == 675 - 300
    assert lottery["short"] == {"money": 375}
    # Отсчёт другого тиража — не про этот снимок.
    other = feed(reducer, state, "lottery", 3402013, 19)
    assert value(other, "lottery") == lottery


def test_quantity_click_edit_spends_difference() -> None:
    # Живой тираж 3286: экран 19:20:09, экран 💵 19:20:21, его правка после клика «1» — 19:20:35.
    reducer = StateReducer()
    state = feed(reducer, {}, "lottery", 3626217, 0)
    opened, clicked = game_versions("lottery", 3626219)
    state = apply(reducer, state, replace(opened, date=at(0.2), created_at=at(0.2)))
    assert value(state, "money") == 4785
    edit = replace(clicked, date=at(0.43), created_at=at(0.2))
    state = apply(reducer, state, edit)
    again = apply(reducer, state, edit)
    assert value(again, "lottery")["bought"]["money"] == 1
    assert value(again, "money") == 4785 - 30
    # «Уже купил все доступные за 📚» — снимок валюты на лимите, без траты.
    full = apply(reducer, again, fixture_at("lottery", 3626225, 1.05))
    assert value(full, "lottery")["bought"]["knowledge"] == 7
    assert value(full, "knowledge") == 21973 - 7 * 4


def test_snapshot_without_lottery_still_loads() -> None:
    data = dump_state(CharacterState())
    del data["lottery"]
    assert load_state(data).lottery is None

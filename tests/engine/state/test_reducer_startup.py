from dataclasses import replace

from app.engine.state.reducer import StateReducer
from app.engine.types import IncomingMessage
from tests.engine.startup_texts import (
    DECLINED,
    MAX_REFUSAL,
    RESULT_KEYS,
    RESULT_LEVELUP,
    RESULT_PARTS,
    SCREEN_FROSTY,
    SCREEN_IN_PROGRESS,
    SCREEN_LEVEL6,
    START,
    startup_msg,
)
from tests.engine.state.helpers import PARSER, at, feed, value


def _msg(text: str, minutes: float, msg_id: int) -> IncomingMessage:
    return replace(startup_msg(text, msg_id=msg_id), date=at(minutes), created_at=at(minutes))


def _feed(reducer: StateReducer, state: dict, text: str, minutes: float, msg_id: int) -> dict:
    msg = _msg(text, minutes, msg_id)
    return reducer.apply(state, msg, PARSER.parse(msg))


def test_screen_snaps_startup_state() -> None:
    reducer = StateReducer()
    state = _feed(reducer, {}, SCREEN_IN_PROGRESS, 1, 1)
    assert value(state, "startup") == {
        "level": 5,
        "max": False,
        "progress": 149,
        "progress_needed": None,
    }
    state = _feed(reducer, state, SCREEN_FROSTY, 2, 2)
    assert value(state, "startup") == {
        "level": 0,
        "max": False,
        "progress": 0,
        "progress_needed": 100,
    }


def test_start_busy_of_startup() -> None:
    reducer = StateReducer()
    state = _feed(reducer, {}, START, 1, 1)
    assert value(state, "busy") == {"activity": "startup", "until": "2026-09-26T09:09:00Z"}


def test_result_adds_progress_when_known() -> None:
    reducer = StateReducer()
    state = _feed(reducer, {}, SCREEN_IN_PROGRESS, 1, 1)
    state = _feed(reducer, state, RESULT_PARTS, 9, 2)
    assert value(state, "startup")["progress"] == 155
    assert value(state, "busy") is None


def test_result_without_known_progress_keeps_it_unknown() -> None:
    reducer = StateReducer()
    state = _feed(reducer, {}, RESULT_PARTS, 1, 1)
    assert value(state, "startup") is None


def test_levelup_raises_level_and_doubts_progress() -> None:
    reducer = StateReducer()
    state = _feed(reducer, {}, SCREEN_IN_PROGRESS, 1, 1)
    state = _feed(reducer, state, RESULT_LEVELUP, 9, 2)
    assert value(state, "startup") == {
        "level": 6,
        "max": False,
        "progress": None,
        "progress_needed": None,
    }


def test_startup_max_refusal_marks_max() -> None:
    reducer = StateReducer()
    state = _feed(reducer, {}, SCREEN_LEVEL6, 1, 1)
    state = _feed(reducer, state, MAX_REFUSAL, 2, 2)
    assert value(state, "startup")["max"] is True


def test_decline_cancels_busy() -> None:
    reducer = StateReducer()
    state = _feed(reducer, {}, START, 1, 1)
    state = _feed(reducer, state, DECLINED, 2, 2)
    assert value(state, "busy") is None


def test_startup_result_writes_ledger_row() -> None:
    reducer = StateReducer()
    state = _feed(reducer, {}, SCREEN_IN_PROGRESS, 1, 1)
    msg = _msg(RESULT_KEYS, 9, 2)
    _state, effects = reducer.reduce(state, msg, PARSER.parse(msg))
    [effect] = effects
    assert effect.kind == "deed"
    assert effect.amounts == {
        "exp": 52,
        "knowledge": -5,
        "details": -8,
        "startup_progress": 5,
        "keys": 50,
    }


def test_startup_start_doubts_raw() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "profile", 3624478, 0)
    assert state["raw"]["src"] == "screen"
    state = _feed(reducer, state, START, 1, 1)
    assert state["raw"]["src"] == "doubtful"

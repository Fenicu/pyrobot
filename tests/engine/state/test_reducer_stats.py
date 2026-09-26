import pytest

from app.engine.state.model import DEED_PRIORS, load_state
from app.engine.state.reducer import STATS_ALPHA, StateReducer
from tests.engine.state.helpers import feed


def test_finished_deed_moves_average_from_prior() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "activities", 3517279, 6)
    stat = load_state(state).activity_stats["harvest"]
    prior = DEED_PRIORS["harvest"]
    assert stat.count == 1
    assert stat.exp == pytest.approx(prior.exp + STATS_ALPHA * (158 - prior.exp))


def test_edit_of_finished_deed_counts_once() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "activities", 3517279, 6)
    again = feed(reducer, state, "activities", 3517279, 7, created=6)
    assert load_state(again).activity_stats["harvest"].count == 1

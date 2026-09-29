from dataclasses import replace

from app.engine.events import AntiFlood
from app.engine.state.reducer import StateReducer
from tests.engine.state.helpers import PARSER, feed, fixture_at, value

PROFILE = 3624478  # 💵867 🔋100% 🔥72/85 (49 мин.), свободен, «🛌 Через 2д. 11ч.», цель 📯


def _profiled(reducer: StateReducer) -> dict:
    return feed(reducer, {}, "profile", PROFILE, 0)


def test_profile_snapshot() -> None:
    state = _profiled(StateReducer())
    assert (value(state, "money"), value(state, "stamina"), value(state, "motivation")) == (
        867,
        100,
        72,
    )
    assert (value(state, "level"), value(state, "bag"), value(state, "tangerines")) == (71, 11, 2)
    assert value(state, "skills") == {
        "practice": 461,
        "theory": 460,
        "cunning": 344,
        "wisdom": 345,
    }
    assert value(state, "busy") is None
    assert value(state, "battle_target") == "📯Pied Piper"
    # Своя компания — по значку ☣️ перед тегом команды.
    assert value(state, "company") == "bmesa"
    assert value(state, "battle_at") == "2026-09-26T20:04:00Z"
    assert value(state, "sleep_deadline") == "2026-09-28T20:00:00Z"
    assert value(state, "sleep_allowed_at") == "2026-09-26T08:00:00Z"
    assert value(state, "motivation_next_at") == "2026-09-26T09:49:00Z"
    assert state["money"]["src"] == "screen"


def test_older_snapshot_ignored() -> None:
    reducer = StateReducer()
    state = _profiled(reducer)
    older = feed(reducer, state, "profile", 3610633, -30)
    assert value(older, "money") == 867
    assert older is state


def test_harvest_start_and_finish() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "activities", 3517276, 1)
    assert (value(state, "money"), value(state, "motivation")) == (837, 71)
    assert state["money"]["src"] == "derived"
    assert value(state, "busy") == {"activity": "harvest", "until": "2026-09-26T09:06:00Z"}
    state = feed(reducer, state, "activities", 3517279, 6)
    assert value(state, "busy") is None
    assert value(state, "exp") == 17496049 + 158


def test_outcome_applied_once_per_message() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "activities", 3517276, 1)
    edited = replace(fixture_at("activities", 3517276, 1.5, created=1), kind="edit", revision=2)
    again = reducer.apply(state, edited, PARSER.parse(edited))
    assert value(again, "money") == 837
    assert "227859379:3517276:activity_started" in again["applied"]


def test_same_second_delta_marks_doubtful() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "activities", 3517276, 0)
    assert (value(state, "money"), state["money"]["src"]) == (867, "doubtful")
    assert state["motivation"]["src"] == "doubtful"
    assert value(state, "busy")["activity"] == "harvest"
    refreshed = feed(reducer, state, "profile", PROFILE, 1)
    assert refreshed["money"]["src"] == "screen"


def test_outcome_in_later_edit_after_snapshot_marks_doubtful() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "activities", 3517276, 2, created=-1)
    assert state["money"]["src"] == "doubtful"
    before = feed(reducer, _profiled(reducer), "activities", 3517276, 2, created=1)
    assert (value(before, "money"), before["money"]["src"]) == (837, "derived")


def test_outcome_horizon() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "activities", 3517279, 60 * 24 * 20)
    assert "227859379:3517279:activity_finished" in state["applied"]
    stale = feed(reducer, state, "activities", 3517276, 60 * 24 * 20 + 1, created=0.5)
    assert value(stale, "busy") is None
    assert "227859379:3517276:activity_started" not in stale["applied"]


def test_delta_needs_known_and_older_base() -> None:
    reducer = StateReducer()
    unknown = feed(reducer, {}, "activities", 3517276, 1)
    assert value(unknown, "money") is None
    assert value(unknown, "busy")["activity"] == "harvest"
    late = feed(reducer, _profiled(reducer), "activities", 3517276, -1)
    assert value(late, "money") == 867


def test_learn_costs_two_motivation_by_default() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "activities", 3603614, 1)
    assert value(state, "motivation") == 70


def test_cancel_and_motivation_full() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "activities", 3517276, 1)
    state = feed(reducer, state, "activities", 3517963, 1.1)
    assert (value(state, "busy"), value(state, "motivation")) == (None, 72)
    state = feed(reducer, state, "activities", 3517795, 2)
    assert (value(state, "motivation"), state["motivation"]["src"]) == (85, "derived")


def test_busy_keeps_known_activity() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "activities", 3517276, 1)
    state = feed(reducer, state, "refusals", 3517360, 2)
    assert value(state, "busy") == {"activity": "harvest", "until": "2026-09-26T09:03:34Z"}
    unknown = feed(reducer, {}, "refusals", 3517360, 2)
    assert value(unknown, "busy")["activity"] == "unknown"


def test_refusals_update_state() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "refusals", 3518565, 1)
    assert value(state, "motivation") == 0
    assert value(state, "last_refusal") == {"reason": "no_motivation", "need": None}
    state = feed(reducer, state, "refusals", 3577823, 2)
    assert value(state, "card_ready_at") == "2026-09-26T09:51:00Z"
    state = feed(reducer, state, "refusals", 3532814, 3)
    assert value(state, "levelup_pending") is True
    state = feed(reducer, state, "refusals", 3517896, 4)
    assert value(state, "last_refusal") == {"reason": "no_money", "need": 16}


def test_battle_target_set() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "battle", 3516891, 1)
    assert value(state, "battle_target") == "📯Pied Piper"
    assert value(state, "stamina") == 0
    assert value(state, "battle_at") == "2026-10-05T00:01:00Z"


def test_magnet_bonus_adds_exp() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "activities", 3524596, 1)
    assert value(state, "exp") == 17496049 + 247


def test_unchanged_state_returns_same_object() -> None:
    reducer = StateReducer()
    state = _profiled(reducer)
    msg = fixture_at("refusals", 3518804, 1)
    assert reducer.apply(state, msg, [AntiFlood()]) is state


def test_metrics_only_changed_fields() -> None:
    reducer = StateReducer()
    state = _profiled(reducer)
    assert reducer.metrics({}, state)["money"] == 867.0
    after = feed(reducer, state, "activities", 3517276, 1)
    assert reducer.metrics(state, after) == {"money": 837.0, "motivation": 71.0}
    assert reducer.metrics(after, after) == {}


def test_unrecognized_company_mark_keeps_known_company() -> None:
    reducer = StateReducer()
    state = _profiled(reducer)
    msg = fixture_at("profile", PROFILE, 10)
    unmarked = replace(msg, text=(msg.text or "").replace("☣️[SU]", "[SU]", 1))
    after = reducer.apply(state, unmarked, PARSER.parse(unmarked))
    assert value(after, "money") == 867 and value(after, "company") == "bmesa"

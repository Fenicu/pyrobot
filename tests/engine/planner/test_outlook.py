from collections.abc import Iterable
from typing import Any

from app.engine.planner.decide import Outlook, decide, earliest, outlook, run_key
from app.engine.planner.types import Act, Decision, Wait, Wakeup
from app.engine.settings import Settings
from app.engine.state.model import BusyState, CharacterState, GorbushkaState
from tests.engine.planner.test_decide import BASE, FOCUS, NOW, awake, config, m, obs, r, w

SLEEP = BusyState(activity="sleep_hotel", until=m(300))
JOB = BusyState(activity="job", until=m(20))


def view_of(state: CharacterState, settings: Settings = BASE, **inputs: Any) -> Outlook:
    """«План бота» и проверка, что его решение — решение цикла на тех же входах."""
    view = outlook(state, settings, NOW, **inputs)
    assert view.decision == decide(state, settings, NOW, **inputs)
    return view


def reasons(timers: Iterable[Wakeup]) -> list[str]:
    return [t.reason for t in timers]


def act(decision: Decision) -> tuple[str, dict[str, Any]]:
    assert isinstance(decision, Act), decision
    return decision.scenario, decision.params


def test_every_planner_test_decision_is_checked(outlook_matches_decide: list[Decision]) -> None:
    decide(awake(), BASE, NOW)
    assert len(outlook_matches_decide) == 1


def test_unknown_busy_gives_only_the_decision() -> None:
    state = awake().model_copy(update={"busy": None})
    view = view_of(state)
    assert view.phase == "unknown" and view.busy is None
    assert act(view.decision) == ("refresh", {"source": "profile"})
    assert [(c.scenario, c.verdict) for c in view.considered] == [
        ("state", "stale:busy"),
        ("refresh", "chosen"),
    ]
    assert view.also_ready == () and view.after_wake == ()
    limited = view_of(state, last_refresh={"profile": m(-1)})
    assert limited.decision == Wait(w(1), "refresh:profile", limited.considered)
    assert reasons(limited.wakeups) == ["refresh:profile"]


def test_asleep_shows_timers_after_wake_without_their_acts() -> None:
    state = awake(busy=SLEEP, books=3, book_ready_at=obs(m(60)), prizebox=True)
    view = view_of(state)
    assert view.phase == "asleep" and view.busy == SLEEP
    assert view.decision == Wait(w(300), "busy", ())
    assert view.considered == () and view.also_ready == ()
    assert reasons(view.wakeups) == ["busy"]
    # Проход «как после пробуждения»: таймеры книги и Горбушки есть, а готовые призовая коробка и
    # дело не показываются ни решением, ни кандидатами.
    assert reasons(view.after_wake)[:1] == ["book_ready"]
    assert view.after_wake[0].at == r(60)
    assert "gorbushka_comeback" in reasons(view.after_wake)
    assert "busy" not in reasons(view.after_wake)


def test_asleep_sets_only_battle_target() -> None:
    view = view_of(awake(busy=SLEEP, battle_target="🤖Hooli", prizebox=True))
    assert act(view.decision) == ("battle_target", {"target": "📯Pied Piper"})
    assert [(c.scenario, c.verdict) for c in view.considered] == [("battle_target", "chosen")]
    assert view.also_ready == ()


def test_busy_pass_splits_considered_and_also_ready() -> None:
    settings = config({"features": {"tangerine": True}})
    comeback = GorbushkaState(state="done", comeback_at=m(-1))
    state = awake(busy=JOB, levelup_pending=True, prizebox=True, gorbushka=comeback)
    view = view_of(state, settings)
    assert view.phase == "busy" and view.busy == JOB
    assert act(view.decision) == ("prizebox", {})
    # Горбушка отказала из-за занятости уже после решения: в «почему не другое» её нет.
    assert [(c.scenario, c.verdict) for c in view.considered] == [
        ("levelup", "busy"),
        ("prizebox", "chosen"),
    ]
    assert [a.scenario for a in view.also_ready] == ["tangerine"]
    assert reasons(view.wakeups)[0] == "busy"


def test_also_ready_one_refresh_per_source_and_timers_after_decision() -> None:
    stale = {"books": None, "cards": None, "sleep_deadline": None}
    state = awake(levelup_pending=True).model_copy(update=stale)
    view = view_of(state)
    assert view.phase == "free"
    assert act(view.decision) == ("levelup", {})
    assert [c.verdict for c in view.considered] == ["chosen"]
    refreshes = [a.params["source"] for a in view.also_ready if a.scenario == "refresh"]
    assert refreshes == ["profile", "inventory"]
    assert view.also_ready[-1].scenario == "deed:job"
    assert "gorbushka_comeback" in reasons(view.wakeups)


def test_also_ready_skips_the_decision_itself() -> None:
    view = view_of(awake().model_copy(update={"books": None, "cards": None}))
    assert act(view.decision) == ("refresh", {"source": "inventory"})
    assert [run_key(a) for a in view.also_ready] == ["deed:job"]


def test_wait_considers_the_whole_pass() -> None:
    view = view_of(awake(motivation=0))
    assert view.decision == Wait(w(30), "motivation", view.considered)
    assert view.also_ready == ()
    assert {c.verdict for c in view.considered} == {"no_motivation"}
    assert len(view.considered) == len(BASE.strategy.deeds)
    # Каждое дело без 🔥 ставит один и тот же таймер: в плане он один.
    assert reasons(view.wakeups).count("motivation") == 1
    assert view.wakeups[0].at == view.decision.until


def test_earliest_keeps_first_moment_per_kind_and_key() -> None:
    timers = [
        Wakeup(m(5), "motivation"),
        Wakeup(m(3), "motivation"),
        Wakeup(m(4), "cooldown", "deed:job"),
        Wakeup(m(1), "cooldown", "refresh:profile"),
    ]
    assert [(t.at, t.reason) for t in earliest(timers)] == [
        (m(1), "cooldown:refresh:profile"),
        (m(3), "motivation"),
        (m(4), "cooldown:deed:job"),
    ]


def test_focus_counts_come_from_done_today() -> None:
    view = view_of(awake(), FOCUS, done_today={"deed:harvest": 3, "deed:dconv": 2})
    assert view.focus == (("deed:harvest", 3), ("deed:dconv", 2))
    assert view_of(awake()).focus == ()

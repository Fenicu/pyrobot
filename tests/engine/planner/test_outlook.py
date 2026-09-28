from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.engine.planner.base import READY_SLACK, TIMER_MARGIN
from app.engine.planner.decide import Outlook, decide, earliest, outlook, run_key
from app.engine.planner.types import Act, Decision, Wait, Wakeup
from app.engine.settings import Settings
from app.engine.state.model import BusyState, CharacterState, GorbushkaState, PriceState
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
    state = awake(busy=SLEEP, books=3, book_ready_at=obs(m(360)), prizebox=True)
    view = view_of(state)
    assert view.phase == "asleep" and view.busy == SLEEP
    assert view.decision == Wait(w(300), "busy", ())
    assert view.considered == () and view.also_ready == ()
    assert reasons(view.wakeups) == ["busy"]
    # Проход «как после пробуждения»: таймеры книги и Горбушки есть, а готовые призовая коробка и
    # дело не показываются ни решением, ни кандидатами.
    moments = {t.reason: t.at for t in view.after_wake}
    assert moments["book_ready"] == r(360)
    assert moments["gorbushka_comeback"] == w(300)
    assert "busy" not in moments


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


def test_hints_follow_settings_and_money() -> None:
    # Битва — через 3 ч от 10:00 UTC, в 16:00 по Москве.
    settings = config(
        {
            "battle": {"overrides": {16: "🤖Hooli"}},
            "lottery": {"tickets": {"money": 4}},
            "sleep": {"duration_h": 8},
        }
    )
    hints = view_of(awake(), settings).hints
    assert hints.battle_target == "🤖Hooli"
    assert hints.lottery_tickets == dict(money=4, knowledge="max", raw="max", details="max")
    assert hints.sleep_hours == 8
    # Цена отеля не видена: оценка 3💵 за уровень для подсказки места не годится.
    assert hints.sleep_place is None
    priced = {"hotel": obs(PriceState(money=210))}
    assert view_of(awake().model_copy(update={"prices": priced})).hints.sleep_place == "hotel"
    poor = awake(money=100).model_copy(update={"prices": priced})
    assert view_of(poor).hints.sleep_place == "bridge"
    # Порог ниже цены отель не удешевляет — как у сценария сна.
    low = config({"sleep": {"hotel_if_cash_after_reserve_ge": 50}})
    assert view_of(poor, low).hints.sleep_place == "bridge"
    unknown = awake().model_copy(update={"money": None, "prices": priced})
    assert outlook(unknown, BASE, NOW).hints.sleep_place is None
    assert view_of(awake()).hints.battle_target == "📯Pied Piper"


def test_asleep_timers_during_sleep_happen_on_wake() -> None:
    # Сон 22:30–05:10 МСК через полночь: сброс заданий в 00:02 и возврат Горбушки в 03:30
    # случатся при подъёме, а слив перед битвой в 01:00 и окно сна к подъёму пройдут.
    at = datetime(2026, 9, 26, 19, 30, tzinfo=UTC)
    woke = datetime(2026, 9, 27, 2, 10, tzinfo=UTC)
    settings = config({"features": {"daily_tasks": True, "stocks_dump": True}})
    book = woke + timedelta(minutes=50)
    state = awake(
        at,
        busy=BusyState(activity="sleep_bridge", until=woke),
        books=3,
        book_ready_at=book,
    )
    view = outlook(state, settings, at)
    assert view.decision == decide(state, settings, at)
    moments = {w.reason: w.at for w in view.after_wake}
    wake = woke + TIMER_MARGIN
    assert moments["daily_reset"] == wake
    assert moments["gorbushka_comeback"] == wake
    assert moments["book_ready"] == book + READY_SLACK + TIMER_MARGIN
    assert not {"stocks_dump", "battle", "sleep_window"} & moments.keys()
    assert all(w.at >= wake for w in view.after_wake)
    assert [w.reason for w in view.wakeups] == ["busy"]


def test_battle_target_hint_only_for_upcoming_battle() -> None:
    assert view_of(awake()).hints.battle_target == "📯Pied Piper"
    past = awake(battle_at=obs(m(-30), age_min=90))
    assert view_of(past).hints.battle_target is None


def test_next_focus_is_the_available_one() -> None:
    # Добыча — 0 раз, переработка — 1, но на добычу нет 💵: следующей будет переработка, как и
    # решение.
    done = {"deed:harvest": 0, "deed:dconv": 1}
    poor = view_of(awake(money=20), FOCUS, done_today=done)
    assert act(poor.decision) == ("deed:dconv", {})
    assert poor.hints.next_focus == "deed:dconv"
    assert view_of(awake(), FOCUS, done_today=done).hints.next_focus == "deed:harvest"
    assert view_of(awake(motivation=0), FOCUS, done_today=done).hints.next_focus is None
    stale = awake(motivation=obs(40, age_min=20))
    assert view_of(stale, FOCUS, done_today=done).hints.next_focus is None
    # Занятость не мешает: подсказка — что будет, когда персонаж освободится.
    busy = view_of(awake(busy=JOB, money=20), FOCUS, done_today=done)
    assert busy.hints.next_focus == "deed:dconv"

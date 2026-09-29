from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.engine.gametime import tasks_day
from app.engine.planner.base import READY_SLACK, TIMER_MARGIN
from app.engine.planner.decide import (
    Basis,
    NextDeed,
    Outlook,
    _Planner,
    decide,
    earliest,
    outlook,
    run_key,
)
from app.engine.planner.types import Act, Decision, Reserve, Wait, Wakeup
from app.engine.settings import Settings
from app.engine.state.model import (
    BusyState,
    CharacterState,
    GorbushkaState,
    Obs,
    PriceState,
    TeamTask,
)
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
    assert view.basis is None


def test_doubtful_busy_is_unknown_too() -> None:
    doubtful = Obs(value=None, at=m(-1), src="doubtful")
    view = view_of(awake().model_copy(update={"busy": doubtful}))
    assert view.phase == "unknown" and view.basis is None
    assert view.also_ready == () and view.wakeups == ()


# Профиль (занятость, 💵, 🔥, 🔋…) снят 26 минут назад: всё быстрое устарело.
SEEN = m(-26)


def basis_of(view: Outlook) -> Basis:
    assert view.basis is not None
    return view.basis


def test_stale_busy_plans_the_rest_by_last_known_data() -> None:
    view = view_of(awake(SEEN))
    assert view.phase == "unknown" and view.busy is None
    # Решение — то же, что у цикла: сначала обновить занятость.
    assert act(view.decision) == ("refresh", {"source": "profile"})
    assert [(c.scenario, c.verdict) for c in view.considered] == [
        ("state", "stale:busy"),
        ("refresh", "chosen"),
    ]
    # Остальное — второй проход на последних известных значениях: свободен, 💵 и 🔥 те же.
    basis = basis_of(view)
    assert (basis.since, basis.busy_at, basis.ended) == (SEEN, SEEN, None)
    assert [a.scenario for a in view.also_ready] == ["deed:job"]
    assert view.hints.next_deed == NextDeed("deed:job", "best")
    assert {"gorbushka_comeback", "sleep_window"} <= set(reasons(view.wakeups))
    # Своё «выбрано» второй проход в план не несёт: выбранное им — в «Готово».
    assert all(c.verdict == "ok" for c in basis.considered)
    assert {c.scenario for c in basis.considered} == {
        "deed:harvest",
        "deed:learn",
        "deed:dconv",
        "deed:walk",
    }


def test_ended_deed_means_free_since_its_end() -> None:
    # Работа наблюдалась 26 минут назад и кончилась 10 минут назад: дела начинает только бот —
    # персонаж свободен с её конца, а не «по данным на момент наблюдения».
    job = BusyState(activity="job", until=m(-10))
    view = view_of(awake(SEEN, busy=job))
    assert act(view.decision) == ("refresh", {"source": "profile"})
    basis = basis_of(view)
    assert (basis.busy_at, basis.ended) == (SEEN, job)
    assert [a.scenario for a in view.also_ready] == ["deed:job"]


def test_basis_pass_takes_the_latest_value_of_every_field() -> None:
    # 💵 сняты минуту назад, уже после занятости: проход берёт их (на добычу 💵 не хватает), а
    # подпись не приписывает им время занятости.
    view = view_of(awake(SEEN, money=obs(20, age_min=1)))
    basis = basis_of(view)
    verdicts = {c.scenario: c.verdict for c in basis.considered}
    assert verdicts["deed:harvest"] == "no_money"
    assert basis.since == SEEN
    # 🔥 снята за 20 минут до занятости — тоже последняя известная: время подписи — её.
    older = basis_of(view_of(awake(SEEN, motivation=obs(40, age_min=46))))
    assert (older.since, older.busy_at) == (m(-46), SEEN)
    assert not [c for c in older.considered if c.verdict.startswith("stale:")]


def test_basis_ignores_fields_the_planner_does_not_read() -> None:
    # Опыт снят давно, но план его не читает: во время подписи он не попадает.
    view = view_of(awake(SEEN, exp=obs(100, age_min=300)))
    assert basis_of(view).since == SEEN


def test_basis_pass_takes_motivation_even_after_the_regen_tick() -> None:
    # Тик регенерации 🔥 — после наблюдения: последняя известная 🔥 — 0, дела ждут её.
    state = awake(SEEN, motivation=0, motivation_next_at=m(-10))
    view = view_of(state)
    deeds = {c.verdict for c in basis_of(view).considered if c.scenario.startswith("deed:")}
    assert deeds == {"no_motivation"}
    assert view.also_ready == ()


def test_basis_pass_keeps_unknown_fields_unknown() -> None:
    # Ненаблюдавшиеся и сомнительные поля последними известными не становятся.
    state = awake(SEEN).model_copy(update={"details": Obs(value=5, at=m(-1), src="doubtful")})
    view = view_of(state)
    verdicts = [(c.scenario, c.verdict) for c in basis_of(view).considered]
    assert ("deeds", "stale:details") in verdicts
    assert view.hints.next_deed is None


def test_basis_pass_under_refresh_limit_keeps_the_wait() -> None:
    view = view_of(awake(SEEN), last_refresh={"profile": m(-1)})
    assert view.decision == Wait(w(1), "refresh:profile", view.considered)
    assert view.wakeups[0].reason == "refresh:profile"
    assert basis_of(view).since == SEEN and view.also_ready


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
    settings = config({"features": {"tangerine": True}, "chats": {"tangerine_reply_to": 927136}})
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


def test_next_deed_is_what_the_deeds_step_would_pick() -> None:
    # Добыча — 0 раз, переработка — 1, но на добычу нет 💵: следующей будет переработка, как и
    # решение.
    done = {"deed:harvest": 0, "deed:dconv": 1}
    poor = view_of(awake(money=20), FOCUS, done_today=done)
    assert act(poor.decision) == ("deed:dconv", {})
    assert poor.hints.next_deed == NextDeed("deed:dconv", "focus")
    assert view_of(awake(), FOCUS, done_today=done).hints.next_deed == NextDeed(
        "deed:harvest", "focus"
    )
    assert view_of(awake(motivation=0), FOCUS, done_today=done).hints.next_deed is None
    stale = awake(motivation=obs(40, age_min=20))
    assert view_of(stale, FOCUS, done_today=done).hints.next_deed is None
    # Занятость не мешает: подсказка — что будет, когда персонаж освободится.
    busy = view_of(awake(busy=JOB, money=20), FOCUS, done_today=done)
    assert busy.hints.next_deed == NextDeed("deed:dconv", "focus")
    # Основные недоступны — лучшее по оценке.
    no_focus = config({"strategy": {"focus": ["confa"]}})
    assert view_of(awake(), no_focus).hints.next_deed == NextDeed("deed:job", "best")


def test_next_deed_follows_team_task_first() -> None:
    # Командное задание на прогулку: шаг дел выберет её раньше основных дел.
    team = TeamTask(current=10, goal=120, resource="💵", day=tasks_day(NOW), activities=("walk",))
    state = awake(team_task=team)
    view = view_of(state, FOCUS, done_today={"deed:harvest": 0, "deed:dconv": 0})
    assert act(view.decision) == ("deed:walk", {})
    assert view.hints.next_deed == NextDeed("deed:walk", "team")


def test_window_open_at_wake_is_shown_at_wake() -> None:
    # Сон 13:00–19:30 МСК: продажа лотереи (19:17–21:05) при подъёме ещё идёт — она в 19:30, а
    # запись на фабрику (18:00–18:15) к подъёму закрыта — её нет.
    at = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)
    woke = datetime(2026, 9, 26, 16, 30, tzinfo=UTC)
    settings = config({"features": {"lottery": True, "factory": True}})
    state = awake(at, busy=BusyState(activity="sleep_hotel", until=woke))
    view = outlook(state, settings, at)
    assert view.decision == decide(state, settings, at)
    moments = {w.reason: w.at for w in view.after_wake}
    assert moments["lottery_open"] == woke + TIMER_MARGIN
    assert "factory_open" not in moments


def test_after_wake_keeps_open_windows_and_readiness() -> None:
    woke = m(300)
    planner = _Planner(awake(), BASE, NOW, None, {}, {}, {}, (), None)
    timers = [
        # Слив начался за 5 минут до подъёма: окно (−15…−1 мин до битвы) ещё открыто.
        Wakeup(woke - timedelta(minutes=5), "stocks_dump"),
        Wakeup(m(100), "battle"),
        Wakeup(m(100), "metro_kick"),
        Wakeup(m(120), "sleep_allowed"),
        Wakeup(m(200), "book_ready"),
        Wakeup(m(360), "card_ready"),
    ]
    assert [(t.reason, t.at) for t in planner.after_wake(timers, woke)] == [
        ("book_ready", woke),
        ("sleep_allowed", woke),
        ("stocks_dump", woke),
        ("card_ready", m(360)),
    ]
    closed = Wakeup(woke - timedelta(minutes=20), "stocks_dump")
    assert planner.after_wake([closed], woke) == ()


METRO_ON = config({"features": {"metro": True}})
FIGHT_SOON = GorbushkaState(state="waiting", won=1, total=4, next_fight_at=m(30), fight_cost=1)


def test_reserves_show_amount_and_moment() -> None:
    # Бой Горбушки через 30 мин, метро откроется через 40 (+ минута на округление экрана).
    state = awake(gorbushka=FIGHT_SOON, metro_ready_at=m(40))
    view = view_of(state, METRO_ON)
    assert view.reserves == (Reserve("gorbushka", 1, m(30)), Reserve("metro", 2, m(41)))
    assert act(view.decision)[0].startswith("deed:")
    # Уже доступные бой и спуск держатся к «сейчас»; занятость запасу не мешает.
    due = GorbushkaState(state="meeting", won=1, total=4, fight_cost=2)
    busy = view_of(awake(busy=JOB, gorbushka=due), METRO_ON)
    assert busy.reserves == (Reserve("gorbushka", 2, NOW), Reserve("metro", 2, NOW))


def test_no_reserves_beyond_horizon_or_with_feature_off() -> None:
    state = awake(gorbushka=FIGHT_SOON, metro_ready_at=m(40))
    assert view_of(state).reserves == (Reserve("gorbushka", 1, m(30)),)
    far = config({"features": {"metro": True}, "strategy": {"reserve_ahead_min": {"metro": 30}}})
    assert view_of(state, far).reserves == (Reserve("gorbushka", 1, m(30)),)
    zero = config({"strategy": {"reserve_ahead_min": {"gorbushka": 0}}})
    assert view_of(state, zero).reserves == ()
    assert view_of(awake()).reserves == ()


def test_deed_refused_only_by_reserve_is_reserved() -> None:
    # 2🔥, обе под метро: без запаса хватило бы и на дело за 1🔥, и на учёбу за 2🔥.
    state = awake(motivation=2, metro_ready_at=m(30))
    view = view_of(state, METRO_ON)
    assert isinstance(view.decision, Wait)
    deeds = {c.scenario: c.verdict for c in view.considered if c.scenario.startswith("deed:")}
    assert set(deeds.values()) == {"reserved"}
    assert "motivation" in reasons(view.wakeups)
    # 1🔥 под бой: учёбе за 2🔥 не хватило бы и без запаса.
    tired = view_of(awake(motivation=1, gorbushka=FIGHT_SOON))
    verdicts = {c.scenario: c.verdict for c in tired.considered}
    assert (verdicts["deed:job"], verdicts["deed:learn"]) == ("reserved", "no_motivation")

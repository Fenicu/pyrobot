from datetime import date, datetime, timedelta
from typing import Any

import pytest

from app.engine.gametime import MSK, tasks_day
from app.engine.parsing.daily import PERSONAL_DEEDS
from app.engine.planner.base import TIMER_MARGIN
from app.engine.planner.daily import UNKNOWN_FIRE
from app.engine.planner.decide import decide
from app.engine.planner.types import Act, Decision, Wait
from app.engine.scenarios.registry import CERTIFIED
from app.engine.settings import Settings
from app.engine.state.model import (
    ActivityStat,
    BusyState,
    CharacterState,
    ChosenTaskState,
    GorbushkaState,
    Obs,
    PersonalTask,
    PriceState,
    TaskOfferState,
    TeamTask,
)
from tests.engine.planner.test_decide import NOW, QUIET, awake, m, verdicts

# NOW — 13:00 MSK 26.09: до сброса заданий 11 часов.
TODAY = tasks_day(NOW)
YESTERDAY = TODAY - timedelta(days=1)
DAILY = Settings.model_validate({"features": {**QUIET, "daily_tasks": True}})
GOALS = {
    "convDets": 72,
    "robPro": 39,
    "jobMoney": 132,
    "materials": 12,
    "learnKnows": 36,
    "walkMoney": 48,
    "confKnows": 60,
}
RESOURCES = {"convDets": "⚙️", "robPro": "⚙️", "jobMoney": "💵", "walkMoney": "💵"}
TROPHIES = {"easy": 30, "medium": 60, "hard": 90}
NO_TEAM = TeamTask(current=0, goal=0, resource="", day=TODAY, status="none")


def offers(*tasks: str, day: date = TODAY) -> PersonalTask:
    variants = []
    for task in tasks:
        kind, level = task.split("_")
        goal = GOALS[kind] * TROPHIES[level] // 90
        variants.append(
            TaskOfferState(type=kind, level=level, goal=goal, trophies=TROPHIES[level])
        )
    return PersonalTask(day=day, status="offers", offers=tuple(variants))


def chosen(kind: str, current: int = 0, status: str = "active", day: date = TODAY) -> PersonalTask:
    task = ChosenTaskState(
        type=kind,
        level="hard",
        goal=GOALS[kind],
        resource=RESOURCES.get(kind, "📚"),
        activities=PERSONAL_DEEDS[kind],
    )
    return PersonalTask.model_validate(
        {"day": day, "status": status, "chosen": task, "current": current}
    )


def team(*activities: str, current: int = 480, goal: int = 720) -> TeamTask:
    return TeamTask(current=current, goal=goal, resource="⚙️", day=TODAY, activities=activities)


def tasks(
    personal: PersonalTask,
    team_task: TeamTask | Obs[TeamTask] = NO_TEAM,
    at: datetime = NOW,
    **over: Any,
) -> CharacterState:
    return awake(at, daily_personal=personal, team_task=team_task, **over)


def picked(decision: Decision) -> tuple[str, dict[str, Any], str]:
    assert isinstance(decision, Act), decision
    return decision.scenario, decision.params, decision.reason


def test_unknown_tasks_are_refreshed() -> None:
    assert picked(decide(awake(), DAILY, NOW)) == ("daily_refresh", {}, "tasks unknown")
    old = tasks(chosen("jobMoney", day=YESTERDAY))
    assert picked(decide(old, DAILY, NOW))[0] == "daily_refresh"
    no_team = awake(daily_personal=chosen("jobMoney"))
    assert picked(decide(no_team, DAILY, NOW))[0] == "daily_refresh"


def test_refresh_at_most_every_ten_minutes() -> None:
    decision = decide(awake(), DAILY, NOW, last_refresh={"daily": m(-3)})
    assert picked(decision)[0] == "deed:harvest"
    assert verdicts(decision)["daily_refresh"] == "rate_limited"
    idle = decide(awake(motivation=0), DAILY, NOW, last_refresh={"daily": m(-3)})
    assert idle == Wait(m(7) + TIMER_MARGIN, "refresh:daily", idle.candidates)


def test_failed_refresh_waits_its_cooldown() -> None:
    decision = decide(awake(), DAILY, NOW, cooldowns={"daily_refresh": m(20)})
    assert picked(decision)[0] == "deed:harvest"
    assert verdicts(decision)["daily_refresh"] == "cooldown"


def test_team_with_unknown_deeds_is_refreshed() -> None:
    # Задание из строки прогресса: дел не знаем — экран перечитывается.
    from_line = Obs(value=team(), at=NOW, src="derived")
    unknown = tasks(chosen("jobMoney"), team_task=from_line)
    assert picked(decide(unknown, DAILY, NOW)) == ("daily_refresh", {}, "team deeds unknown")
    # Экран с незнакомой подсказкой: дела известны (их нет), перечитывать бесполезно.
    shown = tasks(chosen("jobMoney"), team())
    assert picked(decide(shown, DAILY, NOW))[0] == "deed:job"
    known = tasks(chosen("jobMoney"), NO_TEAM)
    assert picked(decide(known, DAILY, NOW))[0] == "deed:job"


def test_daily_runs_during_deed_but_not_in_sleep() -> None:
    busy = awake(busy=BusyState(activity="job", until=m(2)))
    assert picked(decide(busy, DAILY, NOW))[0] == "daily_refresh"
    sleeping = awake(busy=BusyState(activity="sleep_hotel", until=m(300)))
    assert decide(sleeping, DAILY, NOW) == Wait(m(300) + TIMER_MARGIN, "busy", ())


def test_feature_off_skips_daily_step() -> None:
    off = decide(awake(), Settings.model_validate({"features": QUIET}), NOW)
    assert picked(off)[0] == "deed:harvest"
    assert "daily_refresh" not in verdicts(off)


def test_hard_offer_first_by_order() -> None:
    state = tasks(
        offers(
            "convDets_easy", "materials_medium", "jobMoney_hard", "convDets_hard", "materials_hard"
        )
    )
    assert picked(decide(state, DAILY, NOW)) == (
        "daily_pick",
        {"task": "convDets_hard"},
        "personal convDets",
    )
    own = Settings.model_validate(
        {
            "features": DAILY.features.model_dump(),
            "daily": {"personal_order": ["materials", "jobMoney"]},
        }
    )
    assert picked(decide(state, own, NOW))[1] == {"task": "materials_hard"}


def test_types_missing_from_order_go_last() -> None:
    own = Settings.model_validate(
        {"features": DAILY.features.model_dump(), "daily": {"personal_order": ["learnKnows"]}}
    )
    state = tasks(offers("convDets_hard", "learnKnows_hard"))
    assert picked(decide(state, own, NOW))[1] == {"task": "learnKnows_hard"}


def test_without_hard_offer_nothing_is_taken() -> None:
    decision = decide(tasks(offers("convDets_easy", "jobMoney_medium")), DAILY, NOW)
    assert picked(decision)[0] == "deed:harvest"
    assert verdicts(decision)["daily_pick"] == "no_hard_offer"


def test_unfeasible_hard_offer_goes_after_feasible() -> None:
    # Без ⚙️ переработать 72 нельзя: берётся следующее по порядку.
    state = tasks(offers("convDets_hard", "jobMoney_hard"), details=0)
    decision = decide(state, DAILY, NOW)
    assert picked(decision)[:2] == ("daily_pick", {"task": "jobMoney_hard"})
    rejected = [c for c in decision.candidates if c.params == {"task": "convDets_hard"}]
    assert [c.verdict for c in rejected] == ["not_feasible"]


def test_nothing_feasible_takes_first_by_order() -> None:
    # Конфы нет среди разрешённых дел, ⚙️ нет — ни одно не выполнимо, но задание ничего не стоит.
    state = tasks(offers("confKnows_hard", "convDets_hard"), details=0)
    assert picked(decide(state, DAILY, NOW)) == (
        "daily_pick",
        {"task": "convDets_hard"},
        "personal convDets (not feasible)",
    )


def test_uncertified_deed_makes_task_unfeasible() -> None:
    certified = CERTIFIED - {"deed:dconv"}
    state = tasks(offers("convDets_hard", "jobMoney_hard"))
    decision = decide(state, DAILY, NOW, certified=certified)
    assert picked(decision)[1] == {"task": "jobMoney_hard"}


@pytest.mark.parametrize(
    ("gorbushka", "feature", "task"),
    [
        (
            GorbushkaState(state="waiting", won=0, total=4, next_fight_at=m(10)),
            True,
            "robPro_hard",
        ),
        # Трёх боёв по 12⚙️ на цель 39 не хватит.
        (
            GorbushkaState(state="waiting", won=1, total=4, next_fight_at=m(10)),
            True,
            "jobMoney_hard",
        ),
        (GorbushkaState(state="done", comeback_at=m(900)), True, "jobMoney_hard"),
        (
            GorbushkaState(state="waiting", won=0, total=4, next_fight_at=m(10)),
            False,
            "jobMoney_hard",
        ),
    ],
)
def test_rob_pro_feasible_by_gorbushka_fights_left(
    gorbushka: GorbushkaState, feature: bool, task: str
) -> None:
    settings = Settings.model_validate(
        {"features": {**DAILY.features.model_dump(), "gorbushka": feature}}
    )
    state = tasks(offers("robPro_hard", "jobMoney_hard"), gorbushka=gorbushka, motivation=40)
    assert picked(decide(state, settings, NOW))[1] == {"task": task}


@pytest.mark.parametrize(
    ("stats", "task"),
    [
        # Боёв ещё не было — 12⚙️ за победу: 2 × 12 < 39.
        ({}, "jobMoney_hard"),
        # С ⚫️VIP-сетом среднее по боям — 20⚙️: двух побед хватит.
        ({"gorbushka": ActivityStat(count=30, details=20)}, "robPro_hard"),
        # Без сета — около 15⚙️: 2 × 15 < 39.
        ({"gorbushka": ActivityStat(count=30, details=15)}, "jobMoney_hard"),
    ],
)
def test_rob_pro_details_per_fight_from_own_fights(
    stats: dict[str, ActivityStat], task: str
) -> None:
    # 2 из 4, билет живёт за полночь: сегодня ещё две победы.
    gorbushka = GorbushkaState(
        state="waiting", won=2, total=4, next_fight_at=m(10), ticket_until=m(20 * 60)
    )
    state = tasks(offers("robPro_hard", "jobMoney_hard"), gorbushka=gorbushka)
    state = state.model_copy(update={"activity_stats": stats})
    assert picked(decide(state, DAILY, NOW))[1] == {"task": task}


def test_feasibility_estimates_time_and_motivation_until_midnight() -> None:
    # 23:00 MSK: до сброса час, 🔥 10 и ещё 1 приростом. Прогулке на $48 нужно 19 запусков,
    # работе на $132 — 6. Сон выключен: иначе в 23:00 бот уже ложился бы (см. тест про сон).
    walk_first = Settings.model_validate(
        {
            "features": {**DAILY.features.model_dump(), "sleep": False},
            "daily": {"personal_order": ["walkMoney", "jobMoney"]},
        }
    )
    variants = offers("walkMoney_hard", "jobMoney_hard")
    late = datetime(2026, 9, 26, 23, 0, tzinfo=MSK)
    decision = decide(tasks(variants, at=late, motivation=10), walk_first, late)
    assert picked(decision)[1] == {"task": "jobMoney_hard"}
    day = decide(tasks(variants, motivation=10), walk_first, NOW)
    assert picked(day)[1] == {"task": "walkMoney_hard"}


def test_walk_and_confa_estimates_are_averages_with_failures() -> None:
    # Средние всех итогов 2024–2025 вместе с провалами: прогулка даёт $2.6 — на $48 нужно 19
    # запусков, конфа 31📚 — на 60📚 хватит двух (по 3🔥).
    settings = Settings.model_validate(
        {
            "features": {**DAILY.features.model_dump(), "sleep": False},
            "strategy": {"deeds": ["harvest", "job", "learn", "dconv", "walk", "confa"]},
            "daily": {"personal_order": ["walkMoney", "confKnows"]},
        }
    )
    variants = offers("walkMoney_hard", "confKnows_hard")
    # 13:00: 🔥 3 и ещё 11 до полуночи — на 19 прогулок не хватит.
    day = decide(tasks(variants, motivation=3), settings, NOW)
    assert picked(day)[1] == {"task": "confKnows_hard"}
    # 23:00: 🔥 5 и ещё 1 — ровно на две конфы.
    late = datetime(2026, 9, 26, 23, 0, tzinfo=MSK)
    night = decide(tasks(variants, at=late, motivation=5), settings, late)
    assert picked(night)[1] == {"task": "confKnows_hard"}


NO_SLEEP_OR_DEEDS = Settings.model_validate(
    {"features": {**DAILY.features.model_dump(), "deeds": False, "sleep": False}}
)


@pytest.mark.parametrize(
    "at",
    [
        datetime(2026, 9, 26, 23, 58, tzinfo=MSK),
        datetime(2026, 9, 26, 23, 59, 59, tzinfo=MSK),
        datetime(2026, 9, 27, 0, 0, tzinfo=MSK),
        datetime(2026, 9, 27, 0, 1, tzinfo=MSK),
    ],
)
def test_midnight_window(at: datetime) -> None:
    # В окне 23:58–00:02 задание не выбирается и экран не читается: ждём 00:02.
    reset = datetime(2026, 9, 27, 0, 2, tzinfo=MSK)
    far = GorbushkaState(state="done", comeback_at=at + timedelta(days=1))
    state = tasks(offers("convDets_hard"), at=at, gorbushka=far)
    decision = decide(state, NO_SLEEP_OR_DEEDS, at)
    assert decision == Wait(reset + TIMER_MARGIN, "daily_midnight", decision.candidates)
    after = reset + timedelta(seconds=1)
    assert picked(decide(awake(after), NO_SLEEP_OR_DEEDS, after))[0] == "daily_refresh"


def test_waits_for_reset_to_reread_tasks() -> None:
    state = tasks(chosen("jobMoney"), gorbushka=GorbushkaState(state="done", comeback_at=m(2000)))
    reset = datetime(2026, 9, 27, 0, 2, tzinfo=MSK)
    assert decide(state, NO_SLEEP_OR_DEEDS, NOW) == Wait(reset + TIMER_MARGIN, "daily_reset", ())


def test_task_layers_work_with_daily_tasks_off() -> None:
    # Задание, выбранное с телефона, бот доделывает и без шага daily.
    off = Settings.model_validate({"features": QUIET})
    personal = decide(tasks(chosen("jobMoney")), off, NOW)
    assert picked(personal) == ("deed:job", {}, "personal jobMoney")
    assert "daily_refresh" not in verdicts(personal)
    team_only = tasks(chosen("robPro"), team("dconv"))
    assert picked(decide(team_only, off, NOW)) == ("deed:dconv", {}, "team dconv 480/720")


def test_personal_task_deed_goes_first() -> None:
    decision = decide(tasks(chosen("jobMoney", current=29)), DAILY, NOW)
    assert picked(decision) == ("deed:job", {}, "personal jobMoney")


def test_materials_take_best_of_job_and_walk() -> None:
    state = tasks(chosen("materials"))
    assert picked(decide(state, DAILY, NOW))[0] == "deed:job"
    held = decide(state, DAILY, NOW, cooldowns={"deed:job": m(5)})
    assert picked(held) == ("deed:walk", {}, "personal materials")


@pytest.mark.parametrize(
    "personal",
    [
        chosen("jobMoney", status="done", current=132),
        chosen("jobMoney", current=132),
        # Горбушка — не дело: задание на неё порядок дел не меняет.
        chosen("robPro"),
        chosen("confKnows"),
    ],
)
def test_personal_without_doable_deed_leaves_focus(personal: PersonalTask) -> None:
    assert picked(decide(tasks(personal), DAILY, NOW))[:2] == ("deed:harvest", {})


def test_team_task_before_focus() -> None:
    state = tasks(chosen("robPro"), team("dconv"))
    assert picked(decide(state, DAILY, NOW)) == ("deed:dconv", {}, "team dconv 480/720")
    job = tasks(chosen("robPro"), team("job", "walk", current=18, goal=120))
    assert picked(decide(job, DAILY, NOW)) == ("deed:job", {}, "team job 18/120")


@pytest.mark.parametrize(
    "team_task",
    [
        team("gorbushka", current=0, goal=390),
        team("dconv", current=720, goal=720),
        TeamTask(current=720, goal=720, resource="⚙️", day=TODAY, status="done"),
    ],
)
def test_team_task_without_doable_deed_leaves_focus(team_task: TeamTask) -> None:
    state = tasks(chosen("robPro"), team_task)
    assert picked(decide(state, DAILY, NOW))[0] == "deed:harvest"


def test_personal_goes_before_team_and_unavailable_personal_yields() -> None:
    state = tasks(chosen("learnKnows"), team("job", current=748, goal=1320))
    assert picked(decide(state, DAILY, NOW)) == ("deed:learn", {}, "personal learnKnows")
    # Учёбе нужно 2🔥, есть одна: командное задание получает работу.
    tired = tasks(chosen("learnKnows"), team("job", current=748, goal=1320), motivation=1)
    decision = decide(tired, DAILY, NOW)
    assert picked(decision) == ("deed:job", {}, "team job 748/1320")
    assert verdicts(decision)["deed:learn"] == "no_motivation"


def test_yesterdays_personal_is_not_prioritized() -> None:
    state = tasks(chosen("jobMoney", day=YESTERDAY))
    decision = decide(state, DAILY, NOW, last_refresh={"daily": m(-1)})
    assert picked(decision)[:2] == ("deed:harvest", {})


def test_personal_task_of_unknown_type_follows_hint() -> None:
    task = ChosenTaskState(goal=30, resource="🔩", activities=("job",))
    personal = PersonalTask(day=TODAY, status="active", chosen=task)
    assert picked(decide(tasks(personal), DAILY, NOW)) == ("deed:job", {}, "personal unknown")


def test_active_team_task_is_reread_every_half_hour() -> None:
    # Командное закрывают и другие игроки, а сообщения о его выполнении нет.
    old = tasks(chosen("robPro"), team_task=Obs(value=team("dconv"), at=m(-31)))
    assert picked(decide(old, DAILY, NOW)) == ("daily_refresh", {}, "team progress stale")
    fresh = tasks(chosen("robPro"), team_task=Obs(value=team("dconv"), at=m(-29)))
    assert picked(decide(fresh, DAILY, NOW)) == ("deed:dconv", {}, "team dconv 480/720")
    reached = Obs(value=team("dconv", current=720, goal=720), at=m(-90))
    assert picked(decide(tasks(chosen("robPro"), team_task=reached), DAILY, NOW))[0] == (
        "deed:harvest"
    )
    done = TeamTask(current=720, goal=720, resource="⚙️", day=TODAY, status="done")
    assert picked(decide(tasks(chosen("robPro"), Obs(value=done, at=m(-90))), DAILY, NOW))[0] == (
        "deed:harvest"
    )


def test_team_not_chosen_is_reread_every_half_hour() -> None:
    # Глава может выбрать задание позже, а строки прогресса приходят только в итогах дел по
    # условию: без экрана задание на работу или учёбу бот не увидел бы весь день.
    old = tasks(chosen("robPro"), team_task=Obs(value=NO_TEAM, at=m(-31)))
    assert picked(decide(old, DAILY, NOW)) == ("daily_refresh", {}, "team not chosen")
    fresh = tasks(chosen("robPro"), team_task=Obs(value=NO_TEAM, at=m(-29)))
    assert picked(decide(fresh, DAILY, NOW))[0] == "deed:harvest"


def test_stale_team_reread_does_not_block_pick_when_rate_limited() -> None:
    stale = Obs(value=team("dconv"), at=m(-31))
    state = tasks(offers("convDets_hard"), team_task=stale)
    decision = decide(state, DAILY, NOW, last_refresh={"daily": m(-5)})
    assert picked(decision)[:2] == ("daily_pick", {"task": "convDets_hard"})


NO_SLEEP = Settings.model_validate({"features": {**DAILY.features.model_dump(), "sleep": False}})
AFTER_MIDNIGHT = datetime(2026, 9, 27, 0, 5, tzinfo=MSK)


def after_midnight(personal: PersonalTask, **over: Any) -> CharacterState:
    day = tasks_day(AFTER_MIDNIGHT)
    none = TeamTask(current=0, goal=0, resource="", day=day, status="none")
    return tasks(personal.model_copy(update={"day": day}), none, at=AFTER_MIDNIGHT, **over)


def test_sleep_before_midnight_takes_its_hours_from_tasks() -> None:
    # 16:30, лечь в 16:40 (за 2 ч до дедлайна), встать в 23:40: без сна до полуночи 30 мин.
    # Переработке на 72⚙️ нужно 8 × 6 мин, работе на $132 — 6 × 2 мин.
    at = datetime(2026, 9, 26, 16, 30, tzinfo=MSK)
    variants = offers("convDets_hard", "jobMoney_hard")
    sleepy = tasks(variants, at=at, sleep_deadline=at + timedelta(minutes=130))
    decision = decide(sleepy, DAILY, at)
    assert picked(decision)[1] == {"task": "jobMoney_hard"}
    assert [c.verdict for c in decision.candidates if c.params == {"task": "convDets_hard"}] == [
        "not_feasible"
    ]
    # Сон выключен — отдыхать не придётся, время до полуночи целиком.
    assert picked(decide(sleepy, NO_SLEEP, at))[1] == {"task": "convDets_hard"}


def test_night_sleep_after_midnight_leaves_the_day_for_tasks() -> None:
    # 00:05: сон начнётся сейчас и закончится в 07:05, до сброса ещё почти 17 ч.
    state = after_midnight(offers("convDets_hard", "jobMoney_hard"), details=0)
    assert picked(decide(state, DAILY, AFTER_MIDNIGHT))[1] == {"task": "jobMoney_hard"}


def test_sleep_across_midnight_ends_the_task_day() -> None:
    # 21:30: сон с 22:05 до 05:05 — на задания 35 мин, переработке нужно 48.
    at = datetime(2026, 9, 26, 21, 30, tzinfo=MSK)
    state = tasks(offers("convDets_hard", "jobMoney_hard"), at=at)
    assert picked(decide(state, DAILY, at))[1] == {"task": "jobMoney_hard"}
    assert picked(decide(state, NO_SLEEP, at))[1] == {"task": "convDets_hard"}


def test_rob_pro_after_midnight_counts_fights_after_sleep() -> None:
    # Сон 00:05–07:05, дальше бои раз в час до полуночи: 4 боя × 12⚙️ ≥ 39. Переработать
    # без ⚙️ нельзя, поэтому первое по порядку не берётся.
    soon = AFTER_MIDNIGHT + timedelta(minutes=10)
    waiting = GorbushkaState(state="waiting", won=0, total=4, next_fight_at=soon)
    variants = offers("convDets_hard", "robPro_hard")
    state = after_midnight(variants, gorbushka=waiting, details=0)
    assert picked(decide(state, DAILY, AFTER_MIDNIGHT))[1] == {"task": "robPro_hard"}


def _msk(hour: int, minute: int = 0, day: int = 27) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=MSK)


@pytest.mark.parametrize(
    ("gorbushka", "over", "task"),
    [
        # Вчера одолел всех, «приходи через …» — в 07:12: новый билет, 4 боя до полуночи.
        (GorbushkaState(state="done", comeback_at=_msk(7, 12)), {}, "robPro_hard"),
        # Возврат только завтра — сегодня боёв нет.
        (GorbushkaState(state="done", comeback_at=_msk(0, 30, 28)), {}, "learnKnows_hard"),
        # Новый билет ($120) не по карману.
        (GorbushkaState(state="done", comeback_at=_msk(7, 12)), {"money": 100}, "learnKnows_hard"),
        # 1 из 4, билет до 22:00: три боя по нему (36⚙️ мало) и ещё два нового до полуночи.
        (
            GorbushkaState(
                state="waiting", won=1, total=4, next_fight_at=_msk(0, 30), ticket_until=_msk(22)
            ),
            {},
            "robPro_hard",
        ),
        # Тот же билет живёт за полночь — нового сегодня не будет: 3 × 12⚙️ < 39.
        (
            GorbushkaState(
                state="waiting",
                won=1,
                total=4,
                next_fight_at=_msk(0, 30),
                ticket_until=_msk(0, 20, 28),
            ),
            {},
            "learnKnows_hard",
        ),
    ],
)
def test_rob_pro_counts_next_ticket_after_comeback(
    gorbushka: GorbushkaState, over: dict[str, Any], task: str
) -> None:
    # Задание выбирается в 00:05, а Горбушка живёт своим циклом: билет на 24 часа, 4 боя раз
    # в час, потом «приходи через …» до конца билета. Бои нового билета — тоже сегодняшние.
    variants = offers("robPro_hard", "learnKnows_hard")
    state = after_midnight(variants, gorbushka=gorbushka, **over)
    assert picked(decide(state, DAILY, AFTER_MIDNIGHT))[1] == {"task": task}


@pytest.mark.parametrize(
    ("seen", "task"),
    [
        # «Все одолены» прочитано вчера в 10:12 — старше 6 часов, но возврат в 07:12 в силе.
        (
            Obs(
                value=GorbushkaState(state="done", comeback_at=_msk(7, 12)),
                at=_msk(10, 12, 26),
            ),
            "robPro_hard",
        ),
        # Бои шли вчера, билет кончился в 23:00 — новый можно купить сейчас.
        (
            Obs(
                value=GorbushkaState(
                    state="waiting",
                    won=2,
                    total=4,
                    next_fight_at=_msk(13, 0, 26),
                    ticket_until=_msk(23, 0, 26),
                ),
                at=_msk(12, 0, 26),
            ),
            "robPro_hard",
        ),
        # Сомнительное состояние (команда ушла, ответа не было) — боёв не считаем.
        (
            Obs(
                value=GorbushkaState(state="done", comeback_at=_msk(7, 12)),
                at=_msk(0, 1),
                src="doubtful",
            ),
            "learnKnows_hard",
        ),
    ],
)
def test_rob_pro_uses_last_known_gorbushka(seen: Obs[GorbushkaState], task: str) -> None:
    state = after_midnight(offers("robPro_hard", "learnKnows_hard"), gorbushka=seen)
    assert picked(decide(state, DAILY, AFTER_MIDNIGHT))[1] == {"task": task}


@pytest.mark.parametrize(
    ("at", "gorbushka", "task"),
    [
        # Билет сейчас — бои в 20:30, 21:30, 22:30 и 23:30: 4 × 12⚙️ ≥ 39.
        (datetime(2026, 9, 26, 20, 30, tzinfo=MSK), GorbushkaState(state="need_ticket"), "robPro"),
        # В 21:00 — только три боя до полуночи: бой в 24:00 — уже завтра.
        (
            datetime(2026, 9, 26, 21, 0, tzinfo=MSK),
            GorbushkaState(state="need_ticket"),
            "jobMoney",
        ),
        (
            datetime(2026, 9, 26, 20, 50, tzinfo=MSK),
            GorbushkaState(
                state="waiting",
                won=0,
                total=4,
                next_fight_at=datetime(2026, 9, 26, 21, 0, tzinfo=MSK),
            ),
            "jobMoney",
        ),
    ],
)
def test_rob_pro_counts_fight_now_and_hourly_before_deadline(
    at: datetime, gorbushka: GorbushkaState, task: str
) -> None:
    state = tasks(offers("robPro_hard", "jobMoney_hard"), at=at, gorbushka=gorbushka)
    assert picked(decide(state, NO_SLEEP, at))[1] == {"task": f"{task}_hard"}


def test_rob_pro_ticket_fights_until_sleep_across_midnight() -> None:
    # 20:00, лечь в 22:00 и спать за полночь: на бои два часа, 39⚙️ не набрать.
    at = datetime(2026, 9, 26, 20, 0, tzinfo=MSK)
    state = tasks(
        offers("robPro_hard", "jobMoney_hard"),
        at=at,
        gorbushka=GorbushkaState(state="need_ticket"),
        sleep_deadline=datetime(2026, 9, 27, 0, 0, tzinfo=MSK),
    )
    assert picked(decide(state, DAILY, at))[1] == {"task": "jobMoney_hard"}
    assert picked(decide(state, NO_SLEEP, at))[1] == {"task": "robPro_hard"}


@pytest.mark.parametrize(
    ("gorbushka", "over", "task"),
    [
        # Билет по карману: 4 боя до полуночи × 12⚙️ ≥ 39.
        (GorbushkaState(state="need_ticket"), {}, "robPro_hard"),
        # На билет ($120) не хватает.
        (GorbushkaState(state="need_ticket"), {"money": 100}, "jobMoney_hard"),
        # Перед сном отель ($210) вместе с билетом не по карману: сон под мостом, резерва на
        # отель нет, билет по карману.
        (
            GorbushkaState(state="need_ticket"),
            {"money": 200, "sleep_deadline": m(4 * 60)},
            "robPro_hard",
        ),
        # Дневной лимит с экрана — 3 продавана: 36 < 39.
        (GorbushkaState(state="need_ticket", total=3), {}, "jobMoney_hard"),
    ],
)
def test_rob_pro_with_ticket_to_buy(
    gorbushka: GorbushkaState, over: dict[str, Any], task: str
) -> None:
    hotel = Settings.model_validate(
        {
            "features": DAILY.features.model_dump(),
            "sleep": {"hotel_if_cash_after_reserve_ge": 50},
        }
    )
    state = tasks(offers("robPro_hard", "jobMoney_hard"), gorbushka=gorbushka, **over)
    assert picked(decide(state, hotel, NOW))[1] == {"task": task}


def test_first_refresh_of_new_day_ignores_rate_limit() -> None:
    before = datetime(2026, 9, 26, 23, 57, tzinfo=MSK)
    after = datetime(2026, 9, 27, 0, 2, tzinfo=MSK)
    state = tasks(chosen("jobMoney"), at=before)
    decision = decide(state, NO_SLEEP_OR_DEEDS, after, last_refresh={"daily": before})
    assert picked(decision) == ("daily_refresh", {}, "tasks unknown")
    later = after + timedelta(minutes=3)
    held = decide(state, NO_SLEEP_OR_DEEDS, later, last_refresh={"daily": after})
    assert isinstance(held, Wait) and verdicts(held)["daily_refresh"] == "rate_limited"


def test_teamless_character_has_no_daily_tasks() -> None:
    # Задания дня — в меню команды: вне команды (в профиле нет тега) /crew бессмыслен.
    decision = decide(awake(team_tag=None), DAILY, NOW)
    assert picked(decision)[0] == "deed:harvest"
    assert verdicts(decision)["daily_refresh"] == "no_team"
    # Задания, оставшиеся от команды, дела не направляют и не выбираются.
    left = tasks(offers("jobMoney_hard"), team("dconv"), team_tag=None)
    decision = decide(left, DAILY, NOW)
    assert picked(decision) == ("deed:harvest", {}, "focus harvest (0 today)")
    assert verdicts(decision)["daily_refresh"] == "no_team"
    # Давний профиль без команды — сначала свежий профиль, а не /crew и не отказ.
    old = awake(team_tag=Obs(value=None, at=m(-30)))
    decision = decide(old, DAILY, NOW)
    assert picked(decision) == ("refresh", {"source": "profile"}, "daily_refresh needs team_tag")
    assert verdicts(decision)["daily_refresh"] == "stale:team_tag"
    # Тег в профиле или команда ещё неизвестна — как раньше.
    assert picked(decide(awake(team_tag="SU"), DAILY, NOW))[0] == "daily_refresh"
    assert picked(decide(tasks(chosen("jobMoney"), team_tag="SU"), DAILY, NOW))[0] == "deed:job"


TEAM_TROPHIES = {"easy": 300, "medium": 600, "hard": 900}


def team_offers(*variants: str | tuple[str, int, int]) -> TeamTask:
    """Командные варианты главы: `тип_сложность` (цель — как на живом экране главы) или
    (`тип_сложность`, цель, 🏆)."""
    live = {"materials": 120, "convDets": 720, "walkMoney": 480}
    out = []
    for v in variants:
        task, goal, trophies = v if isinstance(v, tuple) else (v, 0, 0)
        kind, level = task.split("_")
        out.append(
            TaskOfferState(
                type=kind,
                level=level,
                goal=goal or live.get(kind, 300),
                trophies=trophies or TEAM_TROPHIES[level],
            )
        )
    return TeamTask(current=0, goal=0, resource="", day=TODAY, status="offers", offers=tuple(out))


# Живой экран главы 05.10: hard — переработка 720⚙️ и прогулка $480.
LEADER = team_offers(
    ("materials_easy", 40, 300),
    ("convDets_medium", 480, 600),
    ("materials_medium", 80, 600),
    "convDets_hard",
    "walkMoney_hard",
)


def team_verdicts(decision: Decision) -> dict[str, str]:
    return {
        str(c.params.get("task")): c.verdict
        for c in decision.candidates
        if c.scenario == "team_pick" and c.verdict != "chosen"
    }


def test_leader_picks_team_task_with_least_bare_motivation_before_personal() -> None:
    # Переработка: 720 / 10⚙️ за запуск = 72 запуска по 1🔥; прогулка: $480 / $2.6 = 185 по 1🔥.
    decision = decide(tasks(offers("convDets_hard", "jobMoney_hard"), LEADER), DAILY, NOW)
    assert picked(decision) == ("team_pick", {"task": "convDets_hard"}, "team convDets 72🔥")
    assert team_verdicts(decision) == {"walkMoney_hard": "team walkMoney 185🔥"}


def test_team_motivation_uses_own_stats_and_prices() -> None:
    # Своя прогулка — $8 за запуск: 60 запусков дешевле 72 переработок.
    walker = tasks(offers("jobMoney_hard"), LEADER).model_copy(
        update={"activity_stats": {"walk": ActivityStat(money=8)}}
    )
    assert picked(decide(walker, DAILY, NOW))[1:] == (
        {"task": "walkMoney_hard"},
        "team walkMoney 60🔥",
    )
    # Переработка по цене экрана: 20⚙️ за запуск, но 2🔥 — 36 × 2 = 72, как у прогулки по $6.7.
    dconv = Obs(value=PriceState(motivation=2, money=5, details=20, minutes=6), at=NOW)
    priced = tasks(offers("jobMoney_hard"), LEADER).model_copy(update={"prices": {"dconv": dconv}})
    assert picked(decide(priced, DAILY, NOW))[2] == "team convDets 72🔥"


def test_materials_take_the_cheaper_of_job_and_walk() -> None:
    # 120🔩: работа по 0.8 — 150 запусков, прогулка по 0.7 — 172; своя прогулка по 2 — 60.
    leader = team_offers("materials_hard", ("walkMoney_hard", 480, 900))
    decision = decide(tasks(offers("jobMoney_hard"), leader), DAILY, NOW)
    assert picked(decision)[1:] == ({"task": "materials_hard"}, "team materials 150🔥")
    walker = tasks(offers("jobMoney_hard"), leader).model_copy(
        update={"activity_stats": {"walk": ActivityStat(raw=2)}}
    )
    assert picked(decide(walker, DAILY, NOW))[2] == "team materials 60🔥"


def test_team_pick_ignores_money_motivation_deeds_and_deadline() -> None:
    # Только мотивация: ни ⚙️, ни 🔥, ни разрешённых дел, ни времени до полуночи не нужно.
    own = Settings.model_validate(
        {
            "features": {**DAILY.features.model_dump(), "sleep": False},
            "strategy": {"deeds": ["harvest"]},
        }
    )
    late = datetime(2026, 9, 26, 23, 0, tzinfo=MSK)
    state = tasks(offers("jobMoney_hard"), LEADER, at=late, details=0, money=0, motivation=0)
    decision = decide(state, own, late, certified=CERTIFIED - {"deed:dconv", "deed:walk"})
    assert picked(decision)[:2] == ("team_pick", {"task": "convDets_hard"})


def test_team_tie_goes_to_more_trophies_then_screen_order() -> None:
    # $480 и 720⚙️ при одинаковых 72🔥: прогулка по $6.67 — 72 запуска.
    tie = {"activity_stats": {"walk": ActivityStat(money=6.67)}}
    first = tasks(offers("jobMoney_hard"), LEADER).model_copy(update=tie)
    assert picked(decide(first, DAILY, NOW))[1] == {"task": "convDets_hard"}
    more = team_offers("convDets_hard", ("walkMoney_hard", 480, 1200))
    richer = tasks(offers("jobMoney_hard"), more).model_copy(update=tie)
    assert picked(decide(richer, DAILY, NOW))[1] == {"task": "walkMoney_hard"}


def test_without_hard_team_offer_personal_is_picked() -> None:
    easy = team_offers("convDets_easy", "materials_medium")
    decision = decide(tasks(offers("convDets_hard"), easy), DAILY, NOW)
    assert picked(decision)[:2] == ("daily_pick", {"task": "convDets_hard"})
    assert verdicts(decision)["team_pick"] == "no_hard_team_offer"


def test_unknown_team_types_are_skipped_when_others_are_known() -> None:
    # Гаджеты в лабораториях делами не закрываются, дохода у них нет.
    leader = team_offers("labRaw_hard", "labKnows_hard", "walkMoney_hard")
    decision = decide(tasks(offers("jobMoney_hard"), leader), DAILY, NOW)
    assert picked(decision)[1:] == ({"task": "walkMoney_hard"}, "team walkMoney 185🔥")
    assert team_verdicts(decision) == {
        "labRaw_hard": "team labRaw ?🔥",
        "labKnows_hard": "team labKnows ?🔥",
    }


def test_team_rob_pro_counts_fights_and_wins_when_cheaper() -> None:
    # Продаваны 300⚙️ при 20⚙️ за победу: 15 боёв по 1🔥 против 185🔥 прогулки.
    own = {"gorbushka": ActivityStat(count=30, details=20)}
    leader = team_offers("walkMoney_hard", "robPro_hard")
    state = tasks(offers("jobMoney_hard"), leader).model_copy(update={"activity_stats": own})
    decision = decide(state, DAILY, NOW)
    assert picked(decision) == ("team_pick", {"task": "robPro_hard"}, "team robPro 15🔥")
    assert team_verdicts(decision) == {"walkMoney_hard": "team walkMoney 185🔥"}
    # Без своих боёв — 12⚙️ за победу: 25 боёв.
    prior = tasks(offers("jobMoney_hard"), leader)
    assert picked(decide(prior, DAILY, NOW))[2] == "team robPro 25🔥"
    # Дороже прогулки — прогулка.
    dear = team_offers("walkMoney_hard", ("robPro_hard", 3000, 0))
    state = tasks(offers("jobMoney_hard"), dear)
    assert picked(decide(state, DAILY, NOW))[1] == {"task": "walkMoney_hard"}
    assert team_verdicts(decide(state, DAILY, NOW)) == {"robPro_hard": "team robPro 250🔥"}


def test_team_rob_pro_uses_known_fight_cost_and_ignores_feature() -> None:
    # 🔥 за бой с экрана Горбушки: 15 боёв × 3🔥; фича Горбушки и лимит боёв на сегодня не важны.
    own = {"gorbushka": ActivityStat(count=30, details=20)}
    off = Settings.model_validate(
        {"features": {**DAILY.features.model_dump(), "gorbushka": False}}
    )
    leader = team_offers("walkMoney_hard", "robPro_hard")
    gorbushka = GorbushkaState(state="done", won=4, total=4, fight_cost=3)
    state = tasks(offers("jobMoney_hard"), leader, gorbushka=gorbushka)
    state = state.model_copy(update={"activity_stats": own})
    assert picked(decide(state, off, NOW))[1:] == ({"task": "robPro_hard"}, "team robPro 45🔥")


def test_team_only_rob_pro_is_not_blind() -> None:
    leader = team_offers("robPro_hard")
    decision = decide(tasks(offers("jobMoney_hard"), leader), DAILY, NOW)
    assert picked(decision) == ("team_pick", {"task": "robPro_hard"}, "team robPro 25🔥")
    assert not picked(decision)[2].endswith(UNKNOWN_FIRE)


def test_all_hard_unknown_takes_first_on_screen() -> None:
    leader = team_offers("materials_easy", "labRaw_hard", "labKnows_hard")
    decision = decide(tasks(offers("jobMoney_hard"), leader), DAILY, NOW)
    assert picked(decision) == ("team_pick", {"task": "labRaw_hard"}, "team labRaw ?🔥")
    assert team_verdicts(decision) == {"labKnows_hard": "team labKnows ?🔥"}


def test_team_offer_without_goal_is_unknown() -> None:
    blank = TaskOfferState(type="convDets", level="hard", goal=0, trophies=900)
    leader = LEADER.model_copy(update={"offers": (blank, LEADER.offers[-1])})
    decision = decide(tasks(offers("jobMoney_hard"), leader), DAILY, NOW)
    assert picked(decision)[1] == {"task": "walkMoney_hard"}


def test_team_pick_feature_off_or_cooldown_leaves_personal() -> None:
    off = Settings.model_validate(
        {"features": {**DAILY.features.model_dump(), "team_pick": False}}
    )
    state = tasks(offers("convDets_hard"), LEADER)
    decision = decide(state, off, NOW)
    assert picked(decision)[:2] == ("daily_pick", {"task": "convDets_hard"})
    assert "team_pick" not in verdicts(decision)
    cooled = decide(state, DAILY, NOW, cooldowns={"team_pick": m(20)})
    assert picked(cooled)[0] == "daily_pick"
    assert verdicts(cooled)["team_pick"] == "cooldown"
    # Командное уже выбрано — выбирать нечего.
    chosen_team = decide(tasks(offers("convDets_hard"), team("dconv")), DAILY, NOW)
    assert picked(chosen_team)[0] == "daily_pick"
    assert "team_pick" not in verdicts(chosen_team)


def test_leader_offers_not_picked_are_reread_every_half_hour() -> None:
    # Глава может выбрать командное с телефона.
    off = Settings.model_validate(
        {"features": {**DAILY.features.model_dump(), "team_pick": False}}
    )
    old = tasks(chosen("robPro"), team_task=Obs(value=LEADER, at=m(-31)))
    assert picked(decide(old, off, NOW)) == ("daily_refresh", {}, "team not chosen")

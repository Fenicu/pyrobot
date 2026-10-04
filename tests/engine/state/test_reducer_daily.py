from dataclasses import replace
from datetime import date
from typing import Any

from app.engine.gametime import tasks_day
from app.engine.parsing.activities import ActivityFinished
from app.engine.parsing.common import Rewards
from app.engine.parsing.daily import DailyTasksScreen
from app.engine.state.model import load_state, stale_fields
from app.engine.state.reducer import StateReducer
from tests.engine import daily_texts as d
from tests.engine.artifact_texts import game_text
from tests.engine.state.helpers import PARSER, at, feed, fixture_at, value

# T0 — 12:00 MSK 26.09; полночь по Москве — через 12 часов.
DAY = date(2026, 9, 26)
MIDNIGHT = 12 * 60


def _team(state: dict[str, Any]) -> dict[str, Any]:
    team: dict[str, Any] = value(state, "team_task")
    return team


def _personal(state: dict[str, Any]) -> dict[str, Any]:
    personal: dict[str, Any] = value(state, "daily_personal")
    return personal


def test_tasks_day_changes_at_msk_midnight() -> None:
    assert tasks_day(at(MIDNIGHT - 1)) == DAY
    assert tasks_day(at(MIDNIGHT)) == date(2026, 9, 27)


def test_offers_screen_without_team() -> None:
    state = feed(StateReducer(), {}, "daily", 9100001, 1)
    personal = _personal(state)
    assert (personal["day"], personal["status"], personal["chosen"]) == (
        "2026-09-26",
        "offers",
        None,
    )
    assert [(o["type"], o["level"], o["goal"]) for o in personal["offers"]][3] == (
        "convDets",
        "hard",
        72,
    )
    assert _team(state) == {
        "current": 0,
        "goal": 0,
        "resource": "",
        "day": "2026-09-26",
        "status": "none",
        "activities": [],
        "offers": [],
    }


def test_chosen_screen_snapshots_both_tasks() -> None:
    state = feed(StateReducer(), {}, "daily", 3625786, 1)
    assert _personal(state) == {
        "day": "2026-09-26",
        "status": "active",
        "offers": [],
        "chosen": {
            "type": "jobMoney",
            "level": "hard",
            "goal": 132,
            "resource": "💵",
            "activities": ["job"],
        },
        "current": 0,
    }
    team = _team(state)
    assert (team["status"], team["current"], team["goal"], team["activities"]) == (
        "active",
        28,
        120,
        ["job", "walk"],
    )


def test_done_screens() -> None:
    state = feed(StateReducer(), {}, "daily", 3348708, 1)
    assert (_personal(state)["status"], _personal(state)["current"]) == ("done", 132)
    assert (_team(state)["status"], _team(state)["current"]) == ("done", 120)


def test_confirm_edit_makes_personal_active() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "daily", 3625760, 1)
    assert _personal(state)["status"] == "offers"
    state = feed(reducer, state, "daily", 3625782, 2)
    personal = _personal(state)
    assert (personal["status"], personal["chosen"]["type"], personal["current"]) == (
        "active",
        "jobMoney",
        0,
    )
    # Экран варианта до выбора старше правки — не откатывает выбор.
    state = feed(reducer, state, "daily", 3625760, 1.5)
    assert _personal(state)["status"] == "active"


def test_personal_line_updates_progress_of_chosen_task() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "daily", 3625786, 1)
    state = feed(reducer, state, "activities", 3625819, 5)
    assert (_personal(state)["status"], _personal(state)["current"]) == ("active", 29)
    state = feed(reducer, state, "activities", 3625828, 9)
    assert _personal(state)["current"] == 100


def test_personal_line_of_other_resource_or_day_is_ignored() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "daily", 3625786, 1)
    # Бой Горбушки со строкой по ⚙️ — не к заданию на 💵.
    same = feed(reducer, state, "gorbushka", 3433205, 5)
    assert _personal(same)["current"] == 0
    # Строка уже следующего дня к вчерашнему заданию не относится.
    tomorrow = feed(reducer, state, "activities", 3625819, MIDNIGHT + 1)
    assert _personal(tomorrow) == _personal(state)


def test_personal_line_without_chosen_task_is_ignored() -> None:
    reducer = StateReducer()
    assert value(feed(reducer, {}, "activities", 3625819, 1), "daily_personal") is None
    offers = feed(reducer, {}, "daily", 3625760, 1)
    assert _personal(feed(reducer, offers, "activities", 3625819, 2)) == _personal(offers)


def test_completed_marks_done_and_applies_rewards_once() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "profile", 3624478, 0)
    state = feed(reducer, state, "daily", 3625786, 1)
    state = feed(reducer, state, "activities", 3625828, 5)
    money, exp = value(state, "money"), value(state, "exp")
    state = feed(reducer, state, "daily", 3625831, 7)
    personal = _personal(state)
    assert (personal["status"], personal["current"]) == ("done", 132)
    assert state["daily_personal"]["src"] == "derived"
    assert (value(state, "money"), value(state, "exp")) == (money + 60, exp + 722)
    again = feed(reducer, state, "daily", 3625831, 7)
    assert (value(again, "money"), value(again, "exp")) == (money + 60, exp + 722)


def test_completed_without_known_task_creates_bare_done() -> None:
    state = feed(StateReducer(), {}, "daily", 3625831, 1)
    assert _personal(state) == {
        "day": "2026-09-26",
        "status": "done",
        "offers": [],
        "chosen": None,
        "current": 0,
    }


def test_completed_next_day_does_not_touch_yesterday() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "daily", 3625786, 1)
    state = feed(reducer, state, "daily", 3625831, MIDNIGHT + 5)
    assert (_personal(state)["day"], _personal(state)["chosen"]) == ("2026-09-27", None)


def test_team_line_keeps_status_and_activities() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "daily", 3625786, 1)
    state = feed(reducer, state, "activities", 3625689, 5)
    assert state["team_task"]["src"] == "screen"
    team = _team(state)
    assert (team["current"], team["goal"], team["status"], team["activities"]) == (
        18,
        120,
        "active",
        ["job", "walk"],
    )


def test_team_line_without_known_task_creates_active_without_activities() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "activities", 3625689, 5)
    assert state["team_task"]["src"] == "derived"
    assert _team(state) == {
        "current": 18,
        "goal": 120,
        "resource": "🔩",
        "day": "2026-09-26",
        "status": "active",
        "activities": [],
        "offers": [],
    }
    # Прогресс по другому ресурсу — другое задание: дела неизвестны.
    screen = feed(reducer, {}, "daily", 9100009, 1)
    other = _team(feed(reducer, screen, "activities", 3625689, 5))
    assert (other["resource"], other["activities"]) == ("🔩", [])


def test_team_line_fills_unrecognized_condition_of_screen_task() -> None:
    # Условие командного на экране не распознано — ресурса нет: строка прогресса того же дня
    # дополняет задание, дела и статус с экрана остаются. Иначе каждая строка создавала бы
    # задание без дел, и экран перечитывался бы весь день.
    reducer = StateReducer()
    msg = fixture_at("daily", 3625786, 1)
    screen = next(e for e in PARSER.parse(msg) if isinstance(e, DailyTasksScreen))
    assert screen.team is not None
    blank = replace(screen, team=replace(screen.team, resource=""))
    state = reducer.apply({}, msg, [blank])
    state = feed(reducer, state, "activities", 3625689, 5)
    assert state["team_task"]["src"] == "screen"
    team = _team(state)
    assert (team["current"], team["goal"], team["resource"], team["status"]) == (
        18,
        120,
        "🔩",
        "active",
    )
    assert team["activities"] == ["job", "walk"]
    # Глава ещё не выбрал задание: строка значит, что выбрал, — дела неизвестны.
    none = feed(reducer, {}, "daily", 3349359, 1)
    assert _team(none)["status"] == "none"
    chosen = feed(reducer, none, "activities", 3625689, 5)
    assert chosen["team_task"]["src"] == "derived"
    assert (_team(chosen)["status"], _team(chosen)["activities"]) == ("active", [])


def test_team_line_reaching_goal_is_done() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "daily", 3625786, 1)
    # Строк с X ≥ Y в поиске нет: итог строится из живого итога прогулки с другой строкой.
    msg = fixture_at("activities", 3625689, 5)
    finished = ActivityFinished(
        activity="walk", failed=False, rewards=Rewards(exp=253, team_task=(120, 120, "🔩"))
    )
    team = _team(reducer.apply(state, msg, [finished]))
    assert (team["current"], team["status"], team["activities"]) == (120, "done", ["job", "walk"])


def test_team_line_next_day_starts_new_task() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "daily", 3625786, 1)
    state = feed(reducer, state, "activities", 3625689, MIDNIGHT + 5)
    team = _team(state)
    assert (team["day"], team["activities"]) == ("2026-09-27", [])


def test_day_scoped_values_do_not_go_stale_by_age() -> None:
    state = feed(StateReducer(), {}, "daily", 3625786, 1)
    stale = stale_fields(load_state(state), at(MIDNIGHT - 1), volatile_max_age=at(1) - at(0))
    assert "team_task" not in stale and "daily_personal" not in stale


def test_old_team_task_snapshot_still_loads() -> None:
    old = {"value": {"current": 10, "goal": 360, "resource": "📚"}, "at": "2026-09-26T09:00:00Z"}
    state = load_state({"schema_version": 1, "team_task": old})
    assert state.team_task is not None
    assert (state.team_task.value.day, state.team_task.value.status) == (None, "active")


def test_same_second_screen_does_not_undo_completion() -> None:
    # Экран той же секунды, доставленный позже, не откатывает выполнение и выбор.
    reducer = StateReducer()
    state = feed(reducer, {}, "daily", 3625786, 1)
    done = feed(reducer, state, "daily", 3625831, 7)
    again = feed(reducer, done, "daily", 3625786, 7)
    assert (_personal(again)["status"], _personal(again)["current"]) == ("done", 132)
    offers = feed(reducer, done, "daily", 3625760, 7)
    assert _personal(offers)["status"] == "done"
    # Более новый экран за тот же день — по-прежнему снимок.
    newer = feed(reducer, done, "daily", 3625786, 8)
    assert (_personal(newer)["status"], _personal(newer)["current"]) == ("active", 0)


def test_same_second_offers_do_not_undo_choice() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "daily", 3625760, 1)
    chosen = feed(reducer, state, "daily", 3625782, 2)
    again = feed(reducer, chosen, "daily", 3625760, 2)
    assert _personal(again)["status"] == "active"
    # Другой день — не понижение, а новое задание.
    tomorrow = feed(reducer, chosen, "daily", 3625760, MIDNIGHT + 5)
    assert (_personal(tomorrow)["day"], _personal(tomorrow)["status"]) == (
        "2026-09-27",
        "offers",
    )


def test_same_second_screen_does_not_undo_team_done() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "daily", 9100005, 1)
    assert _team(state)["status"] == "done"
    same = feed(reducer, state, "daily", 9100004, 1)
    assert (_team(same)["status"], _team(same)["current"]) == ("done", 390)
    later = feed(reducer, state, "daily", 9100004, 2)
    assert _team(later)["status"] == "active"


def feed_text(
    reducer: StateReducer, state: dict[str, Any], text: str, minutes: float, **kw: Any
) -> dict[str, Any]:
    msg = game_text(text, at=at(minutes), **kw)
    return reducer.apply(state, msg, PARSER.parse(msg))


def test_leader_screen_keeps_team_offers() -> None:
    state = feed_text(StateReducer(), {}, d.LEADER_OFFERS, 1)
    assert _personal(state)["status"] == "offers" and len(_personal(state)["offers"]) == 5
    team = _team(state)
    assert (team["day"], team["status"], team["current"], team["goal"]) == (
        "2026-09-26",
        "offers",
        0,
        0,
    )
    assert [(o["type"], o["level"], o["goal"], o["trophies"]) for o in team["offers"]] == [
        ("materials", "easy", 40, 300),
        ("convDets", "medium", 480, 600),
        ("materials", "medium", 80, 600),
        ("convDets", "hard", 720, 900),
        ("walkMoney", "hard", 480, 900),
    ]


def test_team_chosen_edit_makes_team_active() -> None:
    reducer = StateReducer()
    state = feed_text(reducer, {}, d.LEADER_OFFERS, 1)
    state = feed_text(reducer, state, d.TEAM_CONFIRM, 2, buttons=d.TEAM_CONFIRM_BUTTONS)
    assert _team(state)["status"] == "offers"
    state = feed_text(reducer, state, d.TEAM_CHOSEN, 3)
    assert state["team_task"]["src"] == "screen"
    assert _team(state) == {
        "current": 0,
        "goal": 720,
        "resource": "⚙️",
        "day": "2026-09-26",
        "status": "active",
        "activities": ["dconv"],
        "offers": [],
    }
    # Личное задание правка командного не трогает.
    assert _personal(state)["status"] == "offers"
    # Экран вариантов той же секунды, доставленный позже, выбор не откатывает.
    same = feed_text(reducer, state, d.LEADER_OFFERS, 3)
    assert _team(same)["status"] == "active"
    after = feed_text(reducer, state, d.AFTER_CHOICE, 4)
    assert (_team(after)["status"], _team(after)["activities"]) == ("active", ["dconv"])


def test_team_chosen_with_unknown_deeds_is_derived() -> None:
    # Дела по условию неизвестны: план перечитает экран, чтобы узнать их из подсказки.
    text = d.TEAM_CHOSEN.replace(
        "Переработать всей командой 720⚙️деталей в сырьё в Мастерской.",
        "Вложить всей командой в лабораториях в разработку любого гаджета 300🔩сырья.",
    )
    state = feed_text(StateReducer(), {}, text, 1)
    assert state["team_task"]["src"] == "derived"
    assert (_team(state)["status"], _team(state)["activities"]) == ("active", [])


def test_team_line_while_leader_offers_starts_chosen_task() -> None:
    # Глава выбрал командное сам (с телефона): строка прогресса — уже выбранное задание.
    reducer = StateReducer()
    state = feed_text(reducer, {}, d.LEADER_OFFERS, 1)
    state = feed(reducer, state, "activities", 3625689, 5)
    assert state["team_task"]["src"] == "derived"
    team = _team(state)
    assert (team["status"], team["current"], team["goal"], team["offers"]) == (
        "active",
        18,
        120,
        [],
    )

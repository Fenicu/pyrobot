from dataclasses import replace

import pytest

from app.engine.events import Event
from app.engine.parsing.common import Rewards
from app.engine.parsing.daily import (
    ChosenTask,
    DailyTasksScreen,
    TaskChosen,
    TaskCompleted,
    TaskConfirm,
    TaskOffer,
    personal_type,
    recognize_daily,
)
from tests.fixtures import game_msg, game_versions


def _events(msg_id: int) -> list[Event]:
    return recognize_daily(game_msg("daily", msg_id))


def _screen(msg_id: int) -> DailyTasksScreen:
    events = _events(msg_id)
    assert len(events) == 1 and isinstance(events[0], DailyTasksScreen), events
    return events[0]


def offer(kind: str, level: str, goal: int) -> TaskOffer:
    trophies = {"easy": 30, "medium": 60, "hard": 90}[level]
    return TaskOffer(
        type=kind, level=level, command=f"/t_{kind}_{level}", goal=goal, trophies=trophies
    )


def team(goal: int, current: int, res: str, *activities: str) -> ChosenTask:
    done = not activities
    return ChosenTask(
        type=None,
        level=None,
        goal=goal,
        current=current,
        done=done,
        resource=res,
        activities=activities,
    )


def test_live_offers_with_team_in_progress() -> None:
    assert _screen(3625736) == DailyTasksScreen(
        offers=(
            offer("confKnows", "easy", 23),
            offer("learnKnows", "medium", 23),
            offer("robPro", "medium", 28),
            offer("walkMoney", "hard", 48),
            offer("jobMoney", "hard", 132),
        ),
        team=team(120, 26, "🔩", "job", "walk"),
    )
    assert _screen(3625760).team == team(120, 28, "🔩", "job", "walk")


@pytest.mark.parametrize(
    ("msg_id", "expected"),
    [
        (9100001, None),
        (9100003, team(720, 480, "⚙️", "dconv")),
        (9100004, team(390, 0, "⚙️", "gorbushka")),
        (9100005, team(390, 390, "⚙️")),
        (9100006, team(720, 720, "⚙️")),
        # Цель в условии с разделителем разрядов: «$1 320💵».
        (9100007, team(1320, 748, "💵", "job")),
        (9100008, team(1320, 1320, "💵")),
        (9100009, team(360, 355, "📚", "learn")),
    ],
)
def test_team_states(msg_id: int, expected: ChosenTask | None) -> None:
    screen = _screen(msg_id)
    assert screen.team == expected
    assert len(screen.offers) == 5 and screen.chosen is None


def test_offers_keep_type_level_and_goal() -> None:
    offers = _screen(9100009).offers
    assert offers[3] == offer("robPro", "hard", 39)
    assert [o.goal for o in _screen(9100001).offers] == [4, 8, 23, 72, 12]


def test_chosen_personal_live() -> None:
    screen = _screen(3625786)
    assert screen.offers == ()
    assert screen.chosen == ChosenTask(
        type="jobMoney",
        level="hard",
        goal=132,
        current=0,
        done=False,
        resource="💵",
        activities=("job",),
    )
    assert screen.team == team(120, 28, "🔩", "job", "walk")


def test_chosen_personal_without_team() -> None:
    screen = _screen(3349359)
    assert screen.team is None
    assert screen.chosen is not None
    assert (screen.chosen.type, screen.chosen.goal, screen.chosen.activities) == (
        "learnKnows",
        36,
        ("learn",),
    )


def test_chosen_rob_pro_is_gorbushka() -> None:
    chosen = _screen(3402131).chosen
    assert chosen is not None
    assert (chosen.type, chosen.resource, chosen.activities) == ("robPro", "⚙️", ("gorbushka",))


def test_personal_done() -> None:
    screen = _screen(3348708)
    assert screen.chosen == ChosenTask(
        type="jobMoney",
        level="hard",
        goal=132,
        current=132,
        done=True,
        resource="💵",
        activities=("job",),
    )
    assert screen.team == team(120, 120, "🔩")


def test_old_screen_without_vs16() -> None:
    screen = _screen(1622026)
    assert screen.chosen is not None
    assert (screen.chosen.type, screen.chosen.resource, screen.chosen.done) == (
        "convDets",
        "⚙️",
        True,
    )


@pytest.mark.parametrize(
    ("condition", "kind"),
    [
        ("Переработать 72⚙️деталей в сырьё в Мастерской.", "convDets"),
        ("Переработать 72⚙деталей в сырьё в Мастерской.", "convDets"),
        ("Добыть с Продаванов 39⚙️.", "robPro"),
        ("Заработать на Работе $132💵.", "jobMoney"),
        ("Добыть на Работе или Прогулке 12🔩сырья.", "materials"),
        ("Получить на Учёбе 36📚знаний.", "learnKnows"),
        ("Заработать на Прогулке $48💵.", "walkMoney"),
        ("Получить на Конфе 60📚знаний.", "confKnows"),
        ("Вложить в лабораториях в разработку любого гаджета 30🔩сырья.", None),
    ],
)
def test_personal_type_from_condition(condition: str, kind: str | None) -> None:
    assert personal_type(condition) == kind


def test_confirm_and_chosen_edit() -> None:
    assert _events(3625762) == [TaskConfirm(task="jobMoney_hard")]
    first, edit = game_versions("daily", 3625782)
    assert recognize_daily(first) == [TaskConfirm(task="jobMoney_hard")]
    assert recognize_daily(edit) == [
        TaskChosen(
            chosen=ChosenTask(
                type="jobMoney",
                level="hard",
                goal=132,
                current=0,
                done=False,
                resource="💵",
                activities=("job",),
            )
        )
    ]


def test_confirm_without_button_is_not_confirm() -> None:
    assert recognize_daily(replace(game_msg("daily", 3625762), inline=())) == []


@pytest.mark.parametrize(
    ("msg_id", "exp", "rewards"),
    [
        (3625831, 722, {"money": 60}),
        (3437624, 820, {"knowledge": 9}),
        (3438034, 604, {"knowledge": 9}),
    ],
)
def test_completed(msg_id: int, exp: int, rewards: dict[str, int]) -> None:
    assert _events(msg_id) == [TaskCompleted(trophies=90, rewards=Rewards(exp=exp, **rewards))]


@pytest.mark.parametrize(
    ("msg_id", "drop"),
    [
        (3625736, "Выбрать: /t_robPro_medium"),
        (3625736, "🎖Награда: 900🏆."),
        (3625786, "🔜Прогресс: 0 из 132."),
        (9100001, "Глава команды ещё не выбрал задание."),
        (9100005, "✅Завершено"),
    ],
)
def test_partial_screen_gives_nothing(msg_id: int, drop: str) -> None:
    msg = game_msg("daily", msg_id)
    assert msg.text is not None and drop in msg.text
    assert recognize_daily(replace(msg, text=msg.text.replace(drop, "…"))) == []

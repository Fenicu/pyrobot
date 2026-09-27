from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

from app.engine.events import Event
from app.engine.parsing.common import NUM, Rewards, num, parse_rewards, resource
from app.engine.types import IncomingMessage

# Сложность личного задания видна только по 🏆: они зависят лишь от неё.
LEVELS = {30: "easy", 60: "medium", 90: "hard"}
# Дела, которыми выполняется личное задание. У robPro это бои Горбушки, а не дело.
PERSONAL_DEEDS: dict[str, tuple[str, ...]] = {
    "convDets": ("dconv",),
    "robPro": ("gorbushka",),
    "jobMoney": ("job",),
    "materials": ("job", "walk"),
    "learnKnows": ("learn",),
    "walkMoney": ("walk",),
    "confKnows": ("confa",),
}
_PERSONAL_TYPES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern))
    for name, pattern in (
        ("convDets", r"\AПереработать \d+⚙️?деталей в сырьё в Мастерской\.\Z"),
        ("robPro", r"\AДобыть с Продаванов \d+⚙️?\.\Z"),
        ("jobMoney", r"\AЗаработать на Работе \$\d+💵\.\Z"),
        ("materials", r"\AДобыть на Работе или Прогулке \d+🔩сырья\.\Z"),
        ("learnKnows", r"\AПолучить на Учёбе \d+📚знаний\.\Z"),
        ("walkMoney", r"\AЗаработать на Прогулке \$\d+💵\.\Z"),
        ("confKnows", r"\AПолучить на Конфе \d+📚знаний\.\Z"),
    )
)
_GOAL = re.compile(r"\$?(?P<goal>\d+(?:[\xa0 ]\d{3})*)(?P<res>💵|📚|🔩|⚙️?)")
_HINTS = {
    "/job": "job",
    "/walk": "walk",
    "/dconv": "dconv",
    "/learns": "learn",
    "/gorbushka": "gorbushka",
    "/harvest": "harvest",
    "/confa": "confa",
}
_HEAD = "⏳Ежедневные задания\n"
_OFFERS_HEAD = "\nЛичные задания - выбери одно из предложенных\n"
_OFFER = re.compile(
    r"^⏳(?P<cond>[^\n]+)\n🎖Награда: [^\n]*?(?P<trophies>\d+)🏆\.\n"
    r"Выбрать: (?P<command>/t_(?P<type>[A-Za-z]+)_(?P<level>easy|medium|hard))$",
    re.M,
)
_TASK = (
    r"\n⏳(?P<cond>[^\n]+)\n🎖Награда: [^\n]*?(?P<trophies>\d+)🏆\.\n"
    r"(?:🔜Прогресс: (?P<cur>" + NUM + r") из (?P<goal>" + NUM + r")\.\n(?P<hint>[^\n]*)"
    r"|(?P<done>✅Завершено))"
)
_PERSONAL = re.compile(r"^Личное задание" + _TASK, re.M)
_TEAM = re.compile(r"^Командное задание" + _TASK, re.M)
_TEAM_HEAD = "\nКомандн"
_NO_TEAM = "\nКомандные задания\n\nГлава команды ещё не выбрал задание."
_CONFIRM = "Ты собираешься выбрать задание:\n⏳"
_CONFIRM_BUTTON = re.compile(r"t_(?P<task>[A-Za-z]+_(?:easy|medium|hard))_confirm\Z")
_CHOSEN = re.compile(
    r"\A⏳Выбранное задание\n\n⏳(?P<cond>[^\n]+)\n🎖Награда: [^\n]*?(?P<trophies>\d+)🏆\.\n"
    r".*?Погнали (?P<hint>[^\n]*)",
    re.S,
)
_COMPLETED = re.compile(r"\AТы завершил задание в команде и заработал (?P<trophies>\d+)🏆\.")
_COMMISSION = re.compile(
    r"^Ты получил от комиссии по командам (?P<n>" + NUM + r")(?P<res>💵|📚|🔩|⚙️?)\.", re.M
)
_COMMISSION_KEYS = {"💵": "money", "📚": "knowledge", "🔩": "raw", "⚙️": "details"}


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskOffer:
    type: str
    level: str
    command: str
    goal: int
    trophies: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ChosenTask:
    """Выбранное задание: личное (`type` — из условия) или командное (`type` — None)."""

    type: str | None
    level: str | None
    goal: int
    current: int
    done: bool
    resource: str
    activities: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class DailyTasksScreen(Event):
    """Экран ⏳Ежедневные задания: личное — варианты или выбранное, командное — None, если
    глава ещё не выбрал."""

    kind: ClassVar[str] = "daily_tasks_screen"
    offers: tuple[TaskOffer, ...] = ()
    chosen: ChosenTask | None = None
    team: ChosenTask | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskConfirm(Event):
    """Подтверждение выбора личного задания с кнопкой `t_<task>_confirm`."""

    kind: ClassVar[str] = "task_confirm"
    task: str


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskChosen(Event):
    """Правка подтверждения после «👍Беру!»: задание выбрано."""

    kind: ClassVar[str] = "task_chosen"
    chosen: ChosenTask


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskCompleted(Event):
    """Личное задание выполнено: отдельное сообщение перед итогом дела, которое его закрыло."""

    kind: ClassVar[str] = "task_completed"
    outcome: ClassVar[bool] = True
    trophies: int
    rewards: Rewards


def personal_type(condition: str) -> str | None:
    return next((name for name, p in _PERSONAL_TYPES if p.match(condition)), None)


def _goal(condition: str) -> tuple[int, str]:
    m = _GOAL.search(condition)
    return (num(m["goal"]), resource(m["res"])) if m else (0, "")


def _hint(line: str) -> tuple[str, ...]:
    return tuple(_HINTS[c] for c in re.findall(r"/\w+", line) if c in _HINTS)


def _task(m: re.Match[str], *, personal: bool) -> ChosenTask:
    cond = m["cond"]
    goal, res = _goal(cond)
    kind = personal_type(cond) if personal else None
    done = m["done"] is not None
    if not done:
        goal = num(m["goal"])
    activities = PERSONAL_DEEDS[kind] if kind is not None else _hint(m["hint"] or "")
    return ChosenTask(
        type=kind,
        level=LEVELS.get(int(m["trophies"])) if personal else None,
        goal=goal,
        current=goal if done else num(m["cur"]),
        done=done,
        resource=res,
        activities=activities,
    )


def _offers(text: str) -> tuple[TaskOffer, ...] | None:
    start = text.index(_OFFERS_HEAD)
    end = text.find(_TEAM_HEAD, start)
    section = text[start : end if end >= 0 else len(text)]
    offers = tuple(
        TaskOffer(
            type=m["type"],
            level=m["level"],
            command=m["command"],
            goal=_goal(m["cond"])[0],
            trophies=int(m["trophies"]),
        )
        for m in _OFFER.finditer(section)
    )
    # Экран — только целиком: каждый вариант личного задания должен разобраться.
    if not offers or len(offers) != section.count("⏳"):
        return None
    return offers


def _screen(text: str) -> list[Event]:
    team: ChosenTask | None = None
    if (t := _TEAM.search(text)) is not None:
        team = _task(t, personal=False)
    elif _NO_TEAM not in text:
        return []
    if _OFFERS_HEAD in text:
        offers = _offers(text)
        return [DailyTasksScreen(offers=offers, team=team)] if offers else []
    if (p := _PERSONAL.search(text)) is not None:
        return [DailyTasksScreen(chosen=_task(p, personal=True), team=team)]
    return []


def _completed(text: str, trophies: int) -> TaskCompleted:
    base = parse_rewards(text)
    extra = dict.fromkeys(_COMMISSION_KEYS.values(), 0)
    for m in _COMMISSION.finditer(text):
        extra[_COMMISSION_KEYS[resource(m["res"])]] += num(m["n"])
    rewards = Rewards(
        exp=base.exp,
        money=base.money + extra["money"],
        knowledge=base.knowledge + extra["knowledge"],
        details=base.details + extra["details"],
        raw=base.raw + extra["raw"],
    )
    return TaskCompleted(trophies=trophies, rewards=rewards)


def recognize_daily(msg: IncomingMessage) -> list[Event]:
    text = msg.text or ""
    if text.startswith(_HEAD):
        return _screen(text)
    if text.startswith(_CONFIRM):
        for button in msg.inline:
            if button.data and (m := _CONFIRM_BUTTON.match(button.data)):
                return [TaskConfirm(task=m["task"])]
        return []
    if m := _CHOSEN.match(text):
        cond = m["cond"]
        goal, res = _goal(cond)
        kind = personal_type(cond)
        chosen = ChosenTask(
            type=kind,
            level=LEVELS.get(int(m["trophies"])),
            goal=goal,
            current=0,
            done=False,
            resource=res,
            activities=PERSONAL_DEEDS[kind] if kind is not None else _hint(m["hint"]),
        )
        return [TaskChosen(chosen=chosen)]
    if m := _COMPLETED.match(text):
        return [_completed(text, int(m["trophies"]))]
    return []


RECOGNIZERS = (recognize_daily,)

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import ClassVar

from app.engine.events import Event
from app.engine.parsing.common import (
    DURATION,
    NUM,
    SKILL,
    SKILLS,
    Rewards,
    dur,
    num,
    parse_rewards,
)
from app.engine.types import IncomingMessage

_PET = r"(?:🐀|🐕)"
_VIRUS = r"(?:🐜|🐝|🐌|🐞)"
# Справочные экраны и ответы, которые движку не нужны, но распознаются, чтобы не считаться
# неизвестными: петы, рынок, рейтинги, вирусы, офис, сезонные ивенты.
_INFO = tuple(
    (name, re.compile(pattern))
    for name, pattern in (
        ("pets", r"\A🐾Твои петы\n"),
        ("pets", r"\A" + _PET + r"\S+(?: /(?:mouse|dog)(?:_name)?)?\n"),
        ("pets", r"\A" + _PET + r"\S* ещё сыт после прошлого пира"),
        ("pets", r"\A" + _PET + r" отдыхает после апа"),
        ("pets", r"\AУ пета \S+ закончилась еда"),
        ("pets", r"\A❌Не хватает \$\d+[\xa0 ]💵 для прокачки\."),
        ("network", r"\AЭто интернет, детка\."),
        ("market", r"\A⚖Рынок\n"),
        ("market", r"\A⏱Рынок закрыт\."),
        ("market", r"\AТы выставил на продажу:"),
        ("market", r"\AТы выставляешь на продажу:"),
        ("market", r"\AТвои ресурсы - \d+\n"),
        ("market", r"\AТвои на рынке - \d+\n"),
        ("gifts_shop", r"\AПокупка подарков за 🍊\n"),
        ("artifacts", r"\A👾Артефакты\n"),
        ("tops", r"\AЛучшие работники предыдущей недели:"),
        ("tops", r"\AТоп добытчиков:"),
        ("tops", r"\AРейтинг игроков:"),
        ("tops", r"\AИгроки\n"),
        ("viruses", r"\A🧬(?:Твои )?Вирусы /"),
        ("viruses", r"\A" + _VIRUS + r"\S+ /help_viruses\n"),
        ("viruses", r"\A" + _VIRUS + r"\S+ \(\d+\) становится твоим главным вирусом"),
        ("viruses", r"\A❗️Ты убрал активный вирус!"),
        ("office", r"\AОфис \S"),
        ("office", r"\AЛаборатории\n"),
        ("office", r"\A🧰Сборка ресурсов\n"),
        ("help", r"\AКарманная MMORPG \"Битвы стартапов\"\."),
        ("casino", r"\A🎪Казино работает круглые сутки\."),
        ("bonuses", r"\AВсе твои текущие бонусы\n"),
        ("seasonal", r"\AТы открыл 🧧Новогодн"),
        ("seasonal", r"\AВесна \d{4} /help_spring\n"),
        ("seasonal", r"\A❗️Весенний ивент завершён\."),
        ("seasonal", r"\A❌Нельзя просто так взять и обменять несуществующий символ года"),
        ("gadgets", r"\AГаджеты в рюкзаке: \(продать\)"),
        ("gadgets", r"\AГаджеты: \(на апгрейд\)"),
        ("gadgets", r"\AПокупка ⚪️ простых улучшений\n"),
        ("account", r"\AЗакончился срок твоего бейкерства\."),
        ("account", r"\AСообщение от CEO убрано из профиля\."),
        ("full_profile", r"\AДо следующей Битвы осталось [^!\n]+!\n"),
    )
)
_TOP_WORKER = re.compile(
    r"\AТы занял (?P<place>Первое|Второе|Третье) место в рейтинге лучших работников компании"
)
_PLACES = {"Первое": 1, "Второе": 2, "Третье": 3}
_BATTLE_MENU = re.compile(
    r"\AТвои результаты в предыдущей битве - /battle\n.*?^Следующая битва через (?P<t>"
    + DURATION
    + r")$",
    re.S | re.M,
)
_BATTLE_REPORT = re.compile(
    r"\A[^\n]+ \(\d+\)\n🔨\d+[^\n]*\nТвои результаты в битве на (?P<hour>\d+) часов"
)
_CONTRIBUTION = re.compile(r"^🏆Твой вклад: (?P<n>" + NUM + r")", re.M)
_STAMINA_AFTER = re.compile(r"^🔋Выносливость: \d+% → (?P<n>\d+)%", re.M)
# Ответы с изменением ресурсов в формате наград: обмен символа, подарок за 🍊, итог акулы.
_RESULTS = (
    ("symbol_exchange", re.compile(r"\AТы обменял символ \S+")),
    ("tangerine_gift", re.compile(r"\AТы открыл подарок за 🍊\.")),
    ("shark", re.compile(r"\A[^\n]+ \(\d+\)\n\n🐻")),
)
_LOTTERY_WIN = "🎉Поздравляю, ты выиграл в лотерею!🎉"
_PRIZE = re.compile(r"^(?P<count>\d+)[\xa0 ]\* (?P<amount>\d+)(?P<emo>📚|🔩|⚙️|🔥)", re.M)
_PRIZE_SKILL = re.compile(r"^\+(?P<n>\d+)[\xa0 ]к навыку (?P<skill>" + SKILL + r")$", re.M)
_PRIZE_CONTAINER = re.compile(r"^\+(?P<n>\d+)[\xa0 ]🗳(?P<size>Малый|Средний) контейнер$", re.M)
_SKILLS_EXPIRED = re.compile(
    r"\AТы утратил навыки, выигранные более \d+ дней назад в лотерее:\n(?P<skills>.*?)\n\n",
    re.S,
)
_ETHER = re.compile(
    r"\A💧Покупка Эфира(?: за 💵)?\n.*?^💵Деньги: \$(?P<money>" + NUM + r")$", re.S | re.M
)
_INSTANT = re.compile(r"\A🧭Отлично! Ты завершил задачу мгновенно за \d+🌐")
# Деньги на экранах гаджетов: баланс после покупки улучшений, баланс после продажи гаджета.
_GADGET_UPGRADE_MONEY = re.compile(r"^Деньги: \$(?P<money>" + NUM + r")💵$", re.M)
_GADGET_SALE_MONEY = re.compile(
    r"^Теперь у тебя \$(?P<money>" + NUM + r")[\xa0 ]💵 на счету\.$", re.M
)


@dataclass(frozen=True, slots=True, kw_only=True)
class InfoScreen(Event):
    kind: ClassVar[str] = "info_screen"
    name: str
    money: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TopWorker(Event):
    kind: ClassVar[str] = "top_worker"
    place: int


@dataclass(frozen=True, slots=True, kw_only=True)
class BattleMenu(Event):
    kind: ClassVar[str] = "battle_menu"
    battle_in_s: int


@dataclass(frozen=True, slots=True, kw_only=True)
class BattleReport(Event):
    """Отчёт уже прошедшей битвы по запросу (/battle): час битвы по Москве, награды (💡, ±💵, 🔋
    после битвы) и вклад «🏆Твой вклад». В состояние не идёт."""

    kind: ClassVar[str] = "battle_report"
    hour: int
    rewards: Rewards = field(default_factory=Rewards)
    contribution: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ResourcesChanged(Event):
    kind: ClassVar[str] = "resources_changed"
    outcome: ClassVar[bool] = True
    source: str
    rewards: Rewards


@dataclass(frozen=True, slots=True, kw_only=True)
class LotteryWin(Event):
    kind: ClassVar[str] = "lottery_win"
    outcome: ClassVar[bool] = True
    rewards: Rewards
    motivation: int = 0
    containers_small: int = 0
    containers_medium: int = 0
    skills: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class LotterySkillsExpired(Event):
    kind: ClassVar[str] = "lottery_skills_expired"
    outcome: ClassVar[bool] = True
    skills: tuple[str, ...]
    rewards: Rewards


@dataclass(frozen=True, slots=True, kw_only=True)
class EtherScreen(Event):
    kind: ClassVar[str] = "ether_screen"
    money: int


@dataclass(frozen=True, slots=True, kw_only=True)
class DeedFinishedInstantly(Event):
    kind: ClassVar[str] = "deed_finished_instantly"


_PRIZE_FIELDS = {"📚": "knowledge", "🔩": "raw", "⚙️": "details"}


def _lottery_win(text: str) -> LotteryWin:
    totals = {"knowledge": 0, "raw": 0, "details": 0}
    motivation = 0
    for m in _PRIZE.finditer(text):
        amount = int(m["count"]) * int(m["amount"])
        if m["emo"] == "🔥":
            motivation += amount
        else:
            totals[_PRIZE_FIELDS[m["emo"]]] += amount
    containers = {"Малый": 0, "Средний": 0}
    for m in _PRIZE_CONTAINER.finditer(text):
        containers[m["size"]] += int(m["n"])
    skills: dict[str, int] = {}
    for m in _PRIZE_SKILL.finditer(text):
        skill = SKILLS[m["skill"]]
        skills[skill] = skills.get(skill, 0) + int(m["n"])
    return LotteryWin(
        rewards=Rewards(
            knowledge=totals["knowledge"], raw=totals["raw"], details=totals["details"]
        ),
        motivation=motivation,
        containers_small=containers["Малый"],
        containers_medium=containers["Средний"],
        skills=skills,
    )


def recognize_screens(msg: IncomingMessage) -> list[Event]:
    text = msg.text or ""
    if m := _TOP_WORKER.match(text):
        return [TopWorker(place=_PLACES[m["place"]])]
    if m := _BATTLE_MENU.match(text):
        return [BattleMenu(battle_in_s=dur(m["t"]))]
    if m := _BATTLE_REPORT.match(text):
        rewards = parse_rewards(text)
        if (after := _STAMINA_AFTER.search(text)) is not None:
            rewards = replace(rewards, stamina=int(after["n"]))
        contribution = _CONTRIBUTION.search(text)
        return [
            BattleReport(
                hour=int(m["hour"]),
                rewards=rewards,
                contribution=num(contribution["n"]) if contribution else None,
            )
        ]
    if text.startswith(_LOTTERY_WIN):
        return [_lottery_win(text)]
    if m := _SKILLS_EXPIRED.match(text):
        lost = tuple(SKILLS[line] for line in m["skills"].split("\n") if line in SKILLS)
        return [LotterySkillsExpired(skills=lost, rewards=parse_rewards(text))]
    for source, pattern in _RESULTS:
        if pattern.match(text):
            return [ResourcesChanged(source=source, rewards=parse_rewards(text))]
    if m := _ETHER.match(text):
        return [EtherScreen(money=num(m["money"]))]
    if _INSTANT.match(text):
        return [DeedFinishedInstantly()]
    for name, pattern in _INFO:
        if pattern.match(text):
            money = _gadget_money(text) if name == "gadgets" else None
            return [InfoScreen(name=name, money=money)]
    return []


def _gadget_money(text: str) -> int | None:
    for pattern in (_GADGET_UPGRADE_MONEY, _GADGET_SALE_MONEY):
        if m := pattern.search(text):
            return num(m["money"])
    return None


RECOGNIZERS = (recognize_screens,)

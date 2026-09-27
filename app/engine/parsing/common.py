from __future__ import annotations

import re
from dataclasses import dataclass

NUM = r"\d[\d\xa0 ]*"
DURATION = r"(?:\d+\s*(?:д|ч|мин|сек)\S*\s*)+|пару сек\."
_UNIT_S = {"д": 86400, "ч": 3600, "мин": 60, "сек": 1}
_DUR_PART = re.compile(r"(\d+)\s*(д|ч|мин|сек)")
_REWARD = re.compile(
    r"^(?P<k>💡Опыт|💵[\xa0 ]?Деньги|📚[\xa0 ]?Знания|⚙️[\xa0 ]?Детали|🔩[\xa0 ]?Сырьё"
    r"|🔋Выносливость|🔋Осталось выносливости)(?: за [^:\n]+)?: ?(?P<sign>[+-])?\s?\$?"
    r"(?P<v>\d[\d\xa0 ]*)%?",
    re.M,
)
_UPGRADE = re.compile(
    r"^(?P<tier>⚪️|🔵|🔴) ?(?:(?:Простые|Редкие|Уникальные) у|У)лучшения: \+(?P<n>\d+)", re.M
)
_TASK_LINE = r" задание: (?P<cur>" + NUM + r") из (?P<goal>" + NUM + r")(?P<res>\S+?)\."
_TEAM_TASK = re.compile(r"🔜Командное" + _TASK_LINE)
_PERSONAL_TASK = re.compile(r"🔜Личное" + _TASK_LINE)
# До 2023 игра писала ⚙ без VS16.
_RESOURCE_ALIASES = {"⚙": "⚙️"}
_REWARD_KEYS = {"💡": "exp", "💵": "money", "📚": "knowledge", "⚙️": "details", "🔩": "raw"}
# Компании биржи и битв: название на экранах игры → код в командах (/buys_<код>_N).
COMPANIES = {
    "📯Pied Piper": "piper",
    "🤖Hooli": "hooli",
    "⚡️Stark Ind.": "stark",
    "☂️Umbrella": "umbrl",
    "🎩Wayne Ent.": "wayne",
    "☣️Black Mesa": "bmesa",
}
COMPANY = "|".join(re.escape(name) for name in COMPANIES)
SKILLS = {
    "🔨Практика": "practice",
    "🎓Теория": "theory",
    "🐿Хитрость": "cunning",
    "🐢Мудрость": "wisdom",
}
SKILL = "|".join(SKILLS)


def num(text: str) -> int:
    return int(re.sub(r"[\xa0 ]", "", text))


def dur(text: str) -> int:
    if text.strip().startswith("пару сек"):
        return 2
    return sum(int(n) * _UNIT_S[unit] for n, unit in _DUR_PART.findall(text))


def resource(emoji: str) -> str:
    return _RESOURCE_ALIASES.get(emoji, emoji)


def first_line(text: str) -> str:
    return text.split("\n", 1)[0][:200]


@dataclass(frozen=True, slots=True, kw_only=True)
class Rewards:
    exp: int = 0
    money: int = 0
    knowledge: int = 0
    details: int = 0
    raw: int = 0
    stamina: int | None = None
    upgrades_white: int = 0
    upgrades_blue: int = 0
    upgrades_red: int = 0
    prizebox: bool = False
    # Строки прогресса заданий в итоге: (текущее, цель, ресурс).
    team_task: tuple[int, int, str] | None = None
    personal_task: tuple[int, int, str] | None = None


def _task_line(pattern: re.Pattern[str], text: str) -> tuple[int, int, str] | None:
    m = pattern.search(text)
    return (num(m["cur"]), num(m["goal"]), resource(m["res"])) if m else None


def parse_rewards(text: str) -> Rewards:
    totals = dict.fromkeys(_REWARD_KEYS.values(), 0)
    stamina: int | None = None
    for m in _REWARD.finditer(text):
        value = num(m["v"])
        if m["k"].startswith("🔋"):
            stamina = value
            continue
        name = next(v for k, v in _REWARD_KEYS.items() if m["k"].startswith(k))
        totals[name] += -value if m["sign"] == "-" else value
    ups = {"⚪️": 0, "🔵": 0, "🔴": 0}
    for m in _UPGRADE.finditer(text):
        ups[m["tier"]] += int(m["n"])
    return Rewards(
        exp=totals["exp"],
        money=totals["money"],
        knowledge=totals["knowledge"],
        details=totals["details"],
        raw=totals["raw"],
        stamina=stamina,
        upgrades_white=ups["⚪️"],
        upgrades_blue=ups["🔵"],
        upgrades_red=ups["🔴"],
        prizebox="🎁Призовую коробку" in text,
        team_task=_task_line(_TEAM_TASK, text),
        personal_task=_task_line(_PERSONAL_TASK, text),
    )

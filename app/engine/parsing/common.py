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
_UPGRADE = re.compile(r"^(?P<tier>⚪️|🔵|🔴) Улучшения: \+(?P<n>\d+)", re.M)
_TEAM_TASK = re.compile(
    r"🔜Командное задание: (?P<cur>" + NUM + r") из (?P<goal>" + NUM + r")(?P<res>\S+?)\."
)
_REWARD_KEYS = {"💡": "exp", "💵": "money", "📚": "knowledge", "⚙️": "details", "🔩": "raw"}


def num(text: str) -> int:
    return int(re.sub(r"[\xa0 ]", "", text))


def dur(text: str) -> int:
    if text.strip().startswith("пару сек"):
        return 2
    return sum(int(n) * _UNIT_S[unit] for n, unit in _DUR_PART.findall(text))


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
    team_task: tuple[int, int, str] | None = None


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
    team = _TEAM_TASK.search(text)
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
        team_task=(num(team["cur"]), num(team["goal"]), team["res"]) if team else None,
    )

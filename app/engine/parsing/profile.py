from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

from app.engine.events import Event
from app.engine.parsing.common import DURATION, NUM, company_of_mark, dur, num
from app.engine.types import IncomingMessage

_HEAD = (
    r"\AБитва через (?P<battle_in>[^!\n]+)!\n\n"
    # Тег команды — только у игрока в команде: «☣️💰[SU] Fenicu», без команды — «☣️Fenicu».
    r"(?P<who>(?:[^\[\n]*\[(?P<tag>[^\]]+)\][\xa0 ])?[^\n]+?)(?: (?P<pet>🐀|🐕))?\n"
)
# Строка уровня: с профессией (подпрофессия в скобках бывает, а бывает и нет) — опыт отдельной
# строкой; у новичка без профессии — в скобках, без 🧵.
_LEVELS = (
    r"🎚(?P<level>\d+)\s+🧵\d+(?: \([^)]+\))?\n"
    r"💡(?P<exp>" + NUM + r") из (?P<exp_next>" + NUM + r")\n",
    r"🎚(?P<level>\d+) \((?P<exp>" + NUM + r") из (?P<exp_next>" + NUM + r")💡\)\n",
)
_REST = (
    r"💵\$(?P<money>" + NUM + r")(?: 🌐\d+)? 🔋(?P<stamina>\d+)% /to_eat\n"
    r"📚(?P<knowledge>" + NUM + r")\s+🔩(?P<raw>" + NUM + r")\s+⚙️(?P<details>" + NUM + r")\n"
    r"🔥(?P<mot>\d+) из (?P<mot_max>\d+) \(/pr\)(?: \((?P<mot_in>[^)]*)\))?\n"
    r"🎒(?P<bag>\d+) из (?P<bag_cap>\d+) /inv\n"
    r"(?P<extra>(?:[^\n]+\n)*?)"
    r"\n🔨\xa0(?P<practice>\d+)\s+🎓\xa0(?P<theory>\d+)\n"
    r"🐿\xa0(?P<cunning>\d+)\s+🐢\xa0(?P<wisdom>\d+)\n"
    r"[^\n]*/cool\n\n?"
    r"(?P<tail>.*)\Z"
)
_COMPACT = tuple(re.compile(_HEAD + level + _REST, re.S) for level in _LEVELS)
_CEO_BREAK = "\n\nБитва через"
_TANGERINES = re.compile(r"^🍊(?P<n>\d+) ", re.M)
_SLEEP_IN = re.compile(r"^🛌 Через (?P<t>" + DURATION + r")$", re.M)
_SLEEPING = re.compile(r"^🛌Спишь (?P<where>под мостом|в отеле) \((?P<t>[^)]+)\)$", re.M)
_TARGET = re.compile(r"^⚔️Взлом (?P<target>[^\n]+)$", re.M)
_DOING = re.compile(r"^(?P<act>(?!🛌)[^\n/]+?) \((?P<t>" + DURATION + r")\)$", re.M)
_ACTS = {
    "⛏Барахолишь": "harvest",
    "💻Работаешь": "job",
    "📚Учишься": "learn",
    "🍴Ешь": "eat",
    "⚔Дерёшься": "fight",
    "⚙️Перерабатываешь детали": "dconv",
    "Ожидаешь соперника в 🎯 Дартс": "darts",
}


@dataclass(frozen=True, slots=True, kw_only=True)
class ProfileCompact(Event):
    kind: ClassVar[str] = "profile_compact"
    battle_in_s: int
    level: int
    exp: int
    exp_next: int
    money: int
    stamina: int
    knowledge: int
    raw: int
    details: int
    motivation: int
    motivation_max: int
    motivation_next_in_s: int | None
    bag: int
    bag_cap: int
    tangerines: int | None
    practice: int
    theory: int
    cunning: int
    wisdom: int
    battle_target: str | None
    sleep_in_s: int | None
    busy_kind: str | None
    busy_left_s: int | None
    # Своя компания — код по значку в начале строки имени (☣️ → bmesa).
    company: str | None = None
    # Тег команды; None — персонаж не в команде.
    team_tag: str | None = None


def _busy(tail: str) -> tuple[str | None, int | None]:
    if sleeping := _SLEEPING.search(tail):
        where = "bridge" if sleeping["where"] == "под мостом" else "hotel"
        return f"sleep_{where}", dur(sleeping["t"])
    if doing := _DOING.search(tail):
        return _ACTS.get(doing["act"], "other"), dur(doing["t"])
    return None, None


def recognize_compact(msg: IncomingMessage) -> list[Event]:
    text = msg.text or ""
    # Сообщение CEO игра ставит перед профилем, отделяя пустой строкой.
    if text.startswith("🎙CEO:") and _CEO_BREAK in text:
        text = text[text.index(_CEO_BREAK) + 2 :]
    m = next((m for rx in _COMPACT if (m := rx.match(text))), None)
    if m is None:
        return []
    tail = m["tail"]
    busy_kind, busy_left = _busy(tail)
    sleep_in = _SLEEP_IN.search(tail)
    target = _TARGET.search(tail)
    tangerines = _TANGERINES.search(m["extra"])
    return [
        ProfileCompact(
            battle_in_s=dur(m["battle_in"]),
            level=int(m["level"]),
            exp=num(m["exp"]),
            exp_next=num(m["exp_next"]),
            money=num(m["money"]),
            stamina=int(m["stamina"]),
            knowledge=num(m["knowledge"]),
            raw=num(m["raw"]),
            details=num(m["details"]),
            motivation=int(m["mot"]),
            motivation_max=int(m["mot_max"]),
            motivation_next_in_s=dur(m["mot_in"]) if m["mot_in"] else None,
            bag=int(m["bag"]),
            bag_cap=int(m["bag_cap"]),
            tangerines=int(tangerines["n"]) if tangerines else None,
            practice=int(m["practice"]),
            theory=int(m["theory"]),
            cunning=int(m["cunning"]),
            wisdom=int(m["wisdom"]),
            battle_target=target["target"] if target else None,
            sleep_in_s=dur(sleep_in["t"]) if sleep_in else None,
            busy_kind=busy_kind,
            busy_left_s=busy_left,
            company=company_of_mark(m["who"]),
            team_tag=m["tag"],
        )
    ]


RECOGNIZERS = (recognize_compact,)

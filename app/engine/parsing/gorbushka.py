from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

from app.engine.events import Event
from app.engine.parsing.common import DURATION, NUM, Rewards, dur, num, parse_rewards
from app.engine.parsing.refusals import Refused
from app.engine.types import IncomingMessage

_HEAD = "🏛Горбушка, сэр"
_PROGRESS = re.compile(
    r"Ты одолел (?P<won>\d+) из (?P<total>\d+) 👨Продаванов\.\n"
    r"У тебя осталось (?P<left>[^,]+), чтобы одолеть их всех\."
)
_ALL_DONE = re.compile(
    r"Ты одолел всех 👨Продаванов\. Приходи через (?P<t>" + DURATION + r") за новыми"
)
_NEED_TICKET = re.compile(
    r"Ты готов сражаться с 👨Продаванами\?.*?оплати входной сбор:\n"
    r"\$(?P<money>\d+)💵 и (?P<knowledge>\d+)📚\.\n\nТвои ресурсы:\n"
    r"\$(?P<have_money>" + NUM + r")💵 и (?P<have_knowledge>" + NUM + r")📚",
    re.S,
)
# Правило на каждом экране Горбушки; у экрана билета это единственный источник числа продаванов.
_DAILY_LIMIT = re.compile(r"определённым количеством продаванов: (?P<n>\d+)")
_SHORT = re.compile(r"❌Не хватает \$(?P<need>" + NUM + r")[\xa0 ]?💵 для входа")
_MIN_LEVEL = re.compile(r"❌Заходить на Горбушку можно только с (?P<level>\d+) уровня")
_NEXT = re.compile(r"Ты встретишь следующего 👨Продавана через (?P<t>" + DURATION + r")")
_MEETING = re.compile(
    r"Встретился с продаваном\..*?🔋Выносливость: (?P<st>\d+)%.*?Требования: (?P<mot>\d+)🔥",
    re.S,
)
_STAMINA = re.compile(r"🔋Твоя выносливость: (?P<st>\d+)%")
_FIGHT = "⚔Битва с продаваном"
_LOST = "❗️К сожалению, тебе не удалось одолеть продавана"
_NOTICES: tuple[tuple[str, str], ...] = (
    ("no_seller", "❌Ты ещё не встретил 👨Продавана."),
    ("skills_changed", "❌Выявлена проблема с твоими навыками для битвы с 👨Продаваном"),
)


@dataclass(frozen=True, slots=True, kw_only=True)
class GorbushkaScreen(Event):
    kind: ClassVar[str] = "gorbushka_screen"
    state: str
    won: int | None = None
    total: int | None = None
    ticket_left_s: int | None = None
    next_in_s: int | None = None
    comeback_in_s: int | None = None
    fight_cost_motivation: int | None = None
    ticket_money: int | None = None
    ticket_knowledge: int | None = None
    money: int | None = None
    knowledge: int | None = None
    short_of: int | None = None
    stamina: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class GorbushkaFight(Event):
    kind: ClassVar[str] = "gorbushka_fight"
    outcome: ClassVar[bool] = True
    won: bool
    rewards: Rewards


@dataclass(frozen=True, slots=True, kw_only=True)
class GorbushkaNotice(Event):
    kind: ClassVar[str] = "gorbushka_notice"
    notice: str


def _screen(text: str) -> list[Event]:
    if m := _MIN_LEVEL.search(text):
        return [Refused(reason="min_level", need=int(m["level"]))]
    if m := _ALL_DONE.search(text):
        return [GorbushkaScreen(state="done", comeback_in_s=dur(m["t"]))]
    if m := _NEED_TICKET.search(text):
        short = _SHORT.search(text)
        limit = _DAILY_LIMIT.search(text)
        return [
            GorbushkaScreen(
                state="need_ticket",
                total=int(limit["n"]) if limit else None,
                ticket_money=int(m["money"]),
                ticket_knowledge=int(m["knowledge"]),
                money=num(m["have_money"]),
                knowledge=num(m["have_knowledge"]),
                short_of=num(short["need"]) if short else None,
            )
        ]
    progress = _PROGRESS.search(text)
    if progress is None:
        return []
    won, total = int(progress["won"]), int(progress["total"])
    left = dur(progress["left"])
    if meeting := _MEETING.search(text):
        return [
            GorbushkaScreen(
                state="meeting",
                won=won,
                total=total,
                ticket_left_s=left,
                fight_cost_motivation=int(meeting["mot"]),
                stamina=int(meeting["st"]),
            )
        ]
    if nxt := _NEXT.search(text):
        stamina = _STAMINA.search(text)
        return [
            GorbushkaScreen(
                state="waiting",
                won=won,
                total=total,
                ticket_left_s=left,
                next_in_s=dur(nxt["t"]),
                stamina=int(stamina["st"]) if stamina else None,
            )
        ]
    return []


def recognize_gorbushka(msg: IncomingMessage) -> list[Event]:
    text = msg.text or ""
    if text.startswith(_HEAD):
        return _screen(text)
    if text.startswith(_FIGHT):
        return [GorbushkaFight(won=_LOST not in text, rewards=parse_rewards(text))]
    for notice, prefix in _NOTICES:
        if text.startswith(prefix):
            return [GorbushkaNotice(notice=notice)]
    return []


RECOGNIZERS = (recognize_gorbushka,)

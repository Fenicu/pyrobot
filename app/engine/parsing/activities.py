from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

from app.engine.events import Event
from app.engine.parsing.common import DURATION, NUM, Rewards, dur, num, parse_rewards
from app.engine.types import IncomingMessage

_STARTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "harvest",
        re.compile(
            r"\AТы отправился на Барахолку.*?Затраты - (?P<money>\d+) 💵\. "
            r"Закончишь через (?P<t>" + DURATION + r")",
            re.S,
        ),
    ),
    (
        "job",
        re.compile(r"\AТы отправился работать.*?Будешь работать (?P<t>" + DURATION + r")", re.S),
    ),
    ("learn", re.compile(r"\AНа учёбу.*?Закончишь через (?P<t>" + DURATION + r")", re.S)),
    (
        "eat",
        re.compile(r"\AТы ушёл поесть.*?На еду уйдёт (?P<money>\d+) 💵 и (?P<t>\d+ минут)", re.S),
    ),
    (
        "dconv",
        re.compile(
            r"\AТы перерабатываешь детали в сырьё\. Заплатил (?P<money>\d+)[\xa0 ]💵.*?"
            r"Выложил (?P<details>\d+)[\xa0 ]⚙️деталей\. Закончишь через (?P<t>" + DURATION + r")",
            re.S,
        ),
    ),
)
# Итог дела классифицируется по строке продолжения: первая строка — случайная шутка.
_FINISHES: tuple[tuple[str, str], ...] = (
    ("harvest", "Продолжить ⛏Добычу - /harvest"),
    ("job", "Продолжить 💻Работать - /job"),
    ("learn", "📚Учиться ещё - /learns"),
    ("eat", "Поедим ещё? - /eat"),
    ("dconv", "Перерабатывать ⚙️ → 🔩 ещё - /dconv"),
)
_FAILED = "Ничего не удалось обнаружить"
_LOGISTIC_REFUND = "Сработал 🗳Сет Логистик и ты восстановил 1 🔥"
_CANCEL_OK = "👍Задание отменено.\n\nТебе вернулось:\n"
_CANCEL_REFUND = re.compile(r"^(?:🔥Мотивация: (?P<mot>\d+)|💵Деньги: \$(?P<money>\d+))$", re.M)
_CANCEL_LATE = "❌Задание можно отменить только в первые"
_CANCEL_NONE = "❌Задания уже нет. Нечего отменять."
_MOT_FULL = "Поздравляю! Твоя 🔥Мотивация полностью восстановлена"
_MAGNET = "Сработал 🧲Магнит!"
_REQ = re.compile(r"(\d+)\s?(🔥|💵|⚙️|⚪️|🔵|⏰)")
_REQ_KEYS = {
    "🔥": "motivation",
    "💵": "money",
    "⚙️": "details",
    "⚪️": "white",
    "🔵": "blue",
    "⏰": "minutes",
}
_DEEDS_COMPACT = re.compile(r"^(?P<emo>💻|🚶|🔫)\w+ — (?P<mot>\d+)🔥, (?P<min>\d+) ⏰$", re.M)
_DEEDS_FULL = re.compile(r"^(?P<emo>💻|🚶|🔫)\w+ - .*?\nТребования: (?P<req>[^\n]+)$", re.M | re.S)
_DEEDS_STAMINA = re.compile(r"^🔋Выносливость: (?P<st>\d+)%$", re.M)
_DEEDS_SLEEP_IN = re.compile(r"Свалишься в сон через (?P<t>[^\n]+)")
_DEEDS_SLEEPING = re.compile(
    r"Спишь (?P<where>под мостом|в отеле)\. Закончишь через +(?P<t>[^\n]+)"
)
_STARTUP = re.compile(r"^📚(?P<act>Учиться|Конфа)\n.*?Требования: (?P<req>[^\n]+)$", re.M | re.S)
_WORKSHOP = re.compile(
    r"^(?P<act>⚙️ → 🔩|⚪️ → 🔵|🔵 → 🔴) - [^\n]+\nТребования: (?P<req>[^\n]+)$", re.M
)
_WS_RES = re.compile(
    r"💵Деньги: \$(?P<money>" + NUM + r")\n🔩Сырьё: (?P<raw>" + NUM + r")\n"
    r"⚙️Детали: (?P<det>" + NUM + r")"
)
_WS_UPG = re.compile(
    r"⚪️ простые: (?P<white>"
    + NUM
    + r")[\xa0 ]?шт\.\n🔵 редкие: (?P<blue>"
    + NUM
    + r")[\xa0 ]?шт\.\n🔴 уникальные: (?P<red>"
    + NUM
    + r")[\xa0 ]?шт\."
)
_PROFESSION = re.compile(r"⛏Добывать - [^\n]+\nТребования: (?P<req>[^\n]+)")
_PRICE_KEYS = {
    "💻": "job",
    "🚶": "walk",
    "🔫": "rob",
    "Учиться": "learn",
    "Конфа": "confa",
    "⚙️ → 🔩": "dconv",
    "⚪️ → 🔵": "white_to_blue",
    "🔵 → 🔴": "blue_to_red",
}


@dataclass(frozen=True, slots=True, kw_only=True)
class Price:
    motivation: int = 0
    money: int = 0
    minutes: int = 0
    details: int = 0
    white: int = 0
    blue: int = 0


def parse_requirements(text: str) -> Price:
    return Price(**{_REQ_KEYS[unit]: int(n) for n, unit in _REQ.findall(text)})


@dataclass(frozen=True, slots=True, kw_only=True)
class ActivityStarted(Event):
    kind: ClassVar[str] = "activity_started"
    outcome: ClassVar[bool] = True
    activity: str
    duration_s: int
    money: int = 0
    details: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class ActivityFinished(Event):
    kind: ClassVar[str] = "activity_finished"
    outcome: ClassVar[bool] = True
    activity: str
    failed: bool
    rewards: Rewards
    motivation_refund: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class ActivityCancelled(Event):
    kind: ClassVar[str] = "activity_cancelled"
    outcome: ClassVar[bool] = True
    result: str
    motivation: int = 0
    money: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class MotivationFull(Event):
    kind: ClassVar[str] = "motivation_full"


@dataclass(frozen=True, slots=True, kw_only=True)
class BonusRewards(Event):
    kind: ClassVar[str] = "bonus_rewards"
    outcome: ClassVar[bool] = True
    source: str
    rewards: Rewards


@dataclass(frozen=True, slots=True, kw_only=True)
class PricesScreen(Event):
    kind: ClassVar[str] = "prices_screen"
    screen: str
    prices: dict[str, Price]


@dataclass(frozen=True, slots=True, kw_only=True)
class DeedsMenu(Event):
    kind: ClassVar[str] = "deeds_menu"
    stamina: int
    sleep_in_s: int | None
    sleeping: str | None
    sleeping_left_s: int | None


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkshopScreen(Event):
    kind: ClassVar[str] = "workshop_screen"
    money: int
    raw: int
    details: int
    upgrades_white: int
    upgrades_blue: int
    upgrades_red: int


def recognize_start(msg: IncomingMessage) -> list[Event]:
    text = msg.text or ""
    for activity, pattern in _STARTS:
        if m := pattern.match(text):
            groups = m.groupdict()
            return [
                ActivityStarted(
                    activity=activity,
                    duration_s=dur(m["t"]),
                    money=int(groups.get("money") or 0),
                    details=int(groups.get("details") or 0),
                )
            ]
    return []


def recognize_finish(msg: IncomingMessage) -> list[Event]:
    text = msg.text or ""
    for activity, marker in _FINISHES:
        if marker in text:
            return [
                ActivityFinished(
                    activity=activity,
                    failed=_FAILED in text,
                    rewards=parse_rewards(text),
                    motivation_refund=1 if _LOGISTIC_REFUND in text else 0,
                )
            ]
    return []


def recognize_cancel(msg: IncomingMessage) -> list[Event]:
    text = msg.text or ""
    if text.startswith(_CANCEL_OK):
        refunds = list(_CANCEL_REFUND.finditer(text, len(_CANCEL_OK)))
        if not refunds:
            return []
        return [
            ActivityCancelled(
                result="ok",
                motivation=sum(int(m["mot"] or 0) for m in refunds),
                money=sum(int(m["money"] or 0) for m in refunds),
            )
        ]
    if text.startswith(_CANCEL_LATE):
        return [ActivityCancelled(result="too_late")]
    if text.startswith(_CANCEL_NONE):
        return [ActivityCancelled(result="nothing")]
    return []


def recognize_motivation_full(msg: IncomingMessage) -> list[Event]:
    return [MotivationFull()] if (msg.text or "").startswith(_MOT_FULL) else []


def recognize_bonus(msg: IncomingMessage) -> list[Event]:
    text = msg.text or ""
    if text.startswith(_MAGNET):
        return [BonusRewards(source="magnet", rewards=parse_rewards(text))]
    return []


# Снимок экрана выдаётся только целиком: частичный разбор не должен затирать
# известное состояние, а сообщение без событий попадёт в Unrecognized.
_REQUIRED = {
    "deeds": {"job", "walk", "rob"},
    "startup": {"learn", "confa"},
    "workshop": {"dconv", "white_to_blue", "blue_to_red"},
}


def _complete(screen: str, prices: dict[str, Price]) -> bool:
    return _REQUIRED[screen] <= prices.keys()


def _deeds(text: str) -> list[Event]:
    prices: dict[str, Price] = {}
    for m in _DEEDS_COMPACT.finditer(text):
        prices[_PRICE_KEYS[m["emo"]]] = Price(motivation=int(m["mot"]), minutes=int(m["min"]))
    for m in _DEEDS_FULL.finditer(text):
        prices[_PRICE_KEYS[m["emo"]]] = parse_requirements(m["req"])
    stamina = _DEEDS_STAMINA.search(text)
    if stamina is None or not _complete("deeds", prices):
        return []
    sleep_in = _DEEDS_SLEEP_IN.search(text)
    sleeping = _DEEDS_SLEEPING.search(text)
    return [
        PricesScreen(screen="deeds", prices=prices),
        DeedsMenu(
            stamina=int(stamina["st"]),
            sleep_in_s=dur(sleep_in["t"]) if sleep_in else None,
            sleeping=(
                ("bridge" if sleeping["where"] == "под мостом" else "hotel") if sleeping else None
            ),
            sleeping_left_s=dur(sleeping["t"]) if sleeping else None,
        ),
    ]


def _workshop(text: str) -> list[Event]:
    prices = {
        _PRICE_KEYS[m["act"]]: parse_requirements(m["req"]) for m in _WORKSHOP.finditer(text)
    }
    res, upg = _WS_RES.search(text), _WS_UPG.search(text)
    if res is None or upg is None or not _complete("workshop", prices):
        return []
    return [
        PricesScreen(screen="workshop", prices=prices),
        WorkshopScreen(
            money=num(res["money"]),
            raw=num(res["raw"]),
            details=num(res["det"]),
            upgrades_white=num(upg["white"]),
            upgrades_blue=num(upg["blue"]),
            upgrades_red=num(upg["red"]),
        ),
    ]


def recognize_screens(msg: IncomingMessage) -> list[Event]:
    text = msg.text or ""
    if text.startswith(("💻Работать —", "Ну, граждане алкоголики")):
        return _deeds(text)
    if text.startswith("🔮Стартапы"):
        prices = {
            _PRICE_KEYS[m["act"]]: parse_requirements(m["req"]) for m in _STARTUP.finditer(text)
        }
        return (
            [PricesScreen(screen="startup", prices=prices)] if _complete("startup", prices) else []
        )
    if text.startswith("🛠Мастерская"):
        return _workshop(text)
    if text.startswith("🧵") and (prof := _PROFESSION.search(text)):
        return [
            PricesScreen(screen="profession", prices={"harvest": parse_requirements(prof["req"])})
        ]
    return []


RECOGNIZERS = (
    recognize_start,
    recognize_finish,
    recognize_cancel,
    recognize_motivation_full,
    recognize_bonus,
    recognize_screens,
)

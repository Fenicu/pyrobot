from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

from app.engine.events import Event
from app.engine.gadget_catalog import KIND_BY_CALLBACK, KIND_BY_ICON, SHOP, SLOTS
from app.engine.parsing.common import NUM, num
from app.engine.parsing.items import Gadget, gadget_line, gadget_stats
from app.engine.types import IncomingMessage

_SHOP_NOTE = (
    "Если гаджет разработан какой-либо компанией, то 10% от цены покупки поступает на счёт "
    "этой компании."
)
_OFFER = re.compile(
    r"^(?P<name>[^\n]+?) \((?P<stats>[^()\n]*)\)\n"
    r"(?:Требования: (?P<level>\d+) уровень\n)?"
    r"💵Цена: \$(?P<price>" + NUM + r")\n/buy_(?P<slot>[a-z]+?)(?P<tier>\d+)$",
    re.M,
)
_MONEY = re.compile(r"^💵Твои деньги: \$(?P<money>" + NUM + r")$", re.M)
_BOUGHT = re.compile(
    r"\A👍Поздравляю! Ты стал счастливым обладателем гаджета (?P<name>.+?) "
    r"\((?P<stats>[^()]*)\) и (?:(?P<bag>положил его в 🎒Рюкзак \(/inv\))|сразу надел его)\."
)
_CHANGE = re.compile(
    r"^👍Ты (?P<verb>надел|снял) (?:(?P<grade>[^\w\s]+)(?P<level>\d+)[ \xa0])?(?P<name>.+?)"
    r" \((?P<stats>[^()]*)\)$",
    re.M,
)
# Заголовок гаджета: «⚪️2 📱Китайская мобила» (между уровнем и значком слота бывает \xa0),
# у неулучшенного — «📱Китайская мобила».
_HEADER = r"(?:(?P<grade>[^\w\s]+)(?P<level>\d+)[ \xa0])?(?P<icon>[^\w\s]️?)(?P<name>[^\n]+)"
_STOCK = re.compile(
    r"^(?P<icon>⚪️|🔵|🔴) (?:простые|редкие|уникальные): (?P<n>\d+)[\xa0 ]шт\. "
    r"\((?P<chance>\d+)%\)$",
    re.M,
)
_UPGRADEMAN = re.compile(r"^🗜Апгрейдмэн: .*\(\+(?P<pct>\d+)%\)$", re.M)
_UP_STOCK = re.compile(r"^(?P<icon>⚪️|🔵|🔴) (?P<n>\d+)[\xa0 ]шт\.$", re.M)
# Под шапкой — по строке на каждый навык гаджета (от одной до трёх).
_UP_BODY = re.compile(r"\A" + _HEADER + r"\n(?:[^\n]+\n)+\nУ тебя\n")
_BUTTON_CHANCE = re.compile(r"^(?P<icon>⚪️|🔵|🔴) \((?P<chance>\d+)%\)$")
_TRIES = r"Успех: (?P<ok>\d+)[^\w\s.]+\nПровал: (?P<fail>\d+)[^\w\s.]+\.?"
_ATTEMPT = re.compile(
    r"\A" + _HEADER + r"\n\nПрименил (?P<used>\S+)\n\n(?P<mid>.*?)\n\n" + _TRIES + r"\Z", re.S
)
_CONFIRM = re.compile(
    r"\A" + _HEADER + r"\n\n" + _TRIES + r"\n\nПрименить \S+ [^\n]*? с (?P<chance>\d+)% успеха\Z"
)
_DECLINED = re.compile(r"\A👎Отказался от улучшения\.\n\n" + _HEADER + r"\n\n" + _TRIES + r"\Z")
_CONFIRM_SET = re.compile(
    r"\AПодтверждение при наложении апгрейдов (?P<state>включено|отключено)\."
)
_UP_BUTTON = re.compile(
    r"^up_(?P<slot>[a-z]+)_(?P<kind>low|middle|high)(?:_(?P<n>\d+)_accept|_decline)?$"
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ShopOffer:
    tier: int
    name: str
    bonuses: dict[str, int]
    level: int | None
    price: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ShopScreen(Event):
    """Витрина слота: позиции по порядку тиров и деньги (`💵Твои деньги`)."""

    kind: ClassVar[str] = "gadget_shop"
    slot: str
    offers: tuple[ShopOffer, ...]
    money: int


@dataclass(frozen=True, slots=True, kw_only=True)
class GadgetBought(Event):
    """Покупка в магазине; `worn` — слот был пуст и игра сразу надела купленное."""

    kind: ClassVar[str] = "gadget_bought"
    outcome: ClassVar[bool] = True
    name: str
    bonuses: dict[str, int]
    worn: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class GadgetWorn(Event):
    """Строка «👍Ты надел …» ответа на `/wear_`; значка слота в ней нет."""

    kind: ClassVar[str] = "gadget_worn"
    grade: str | None
    level: int | None
    name: str
    bonuses: dict[str, int]
    mark: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class GadgetUnworn(Event):
    kind: ClassVar[str] = "gadget_unworn"
    grade: str | None
    level: int | None
    name: str
    bonuses: dict[str, int]
    mark: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class UpgradesScreen(Event):
    """Экран «Гаджеты: (на апгрейд)»: надетые с up-слотами, запасы улучшений, шансы по видам,
    бонус Апгрейдмэна и режим подтверждения (`/ucoff` в тексте — подтверждение включено)."""

    kind: ClassVar[str] = "upgrades_screen"
    items: tuple[tuple[str, Gadget], ...]
    upgrademan_pct: int | None
    stocks: dict[str, int]
    chances: dict[str, int]
    confirm: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class UpgradeScreen(Event):
    """Экран одного гаджета (`/up_<слот>`): запасы и шансы из текста кнопок."""

    kind: ClassVar[str] = "upgrade_screen"
    up_slot: str
    grade: str | None
    level: int | None
    name: str
    stocks: dict[str, int]
    chances: dict[str, int]


@dataclass(frozen=True, slots=True, kw_only=True)
class UpgradeAttempt(Event):
    """Итог попытки заточки — правка того же сообщения; уровень в шапке уже после попытки."""

    kind: ClassVar[str] = "upgrade_attempt"
    outcome: ClassVar[bool] = True
    per_revision: ClassVar[bool] = True
    up_slot: str
    name: str
    grade: str | None
    level: int
    used: str
    success: bool
    next_ok: int
    next_fail: int


@dataclass(frozen=True, slots=True, kw_only=True)
class UpgradeConfirm(Event):
    """Запрос подтверждения попытки: гаджет из шапки правки, вид улучшения из кнопки
    `…_1_accept`, шанс."""

    kind: ClassVar[str] = "upgrade_confirm"
    up_slot: str
    name: str
    grade: str | None = None
    level: int | None = None
    upgrade: str
    chance: int


@dataclass(frozen=True, slots=True, kw_only=True)
class UpgradeDeclined(Event):
    kind: ClassVar[str] = "upgrade_declined"
    up_slot: str
    name: str
    grade: str | None
    level: int


@dataclass(frozen=True, slots=True, kw_only=True)
class UpgradeConfirmSet(Event):
    kind: ClassVar[str] = "upgrade_confirm_set"
    on: bool


def _shop(text: str) -> list[Event]:
    money = _MONEY.search(text)
    offers: list[ShopOffer] = []
    slots: set[str] = set()
    for m in _OFFER.finditer(text):
        parsed = gadget_stats(m["stats"])
        if parsed is None:
            return []
        offers.append(
            ShopOffer(
                tier=int(m["tier"]),
                name=m["name"],
                bonuses=parsed[0],
                level=int(m["level"]) if m["level"] else None,
                price=num(m["price"]),
            )
        )
        slots.add(m["slot"])
    # Витрина целиком: один слот магазина и тиры подряд с первого.
    if money is None or len(slots) != 1 or (slot := slots.pop()) not in SHOP:
        return []
    if [o.tier for o in offers] != list(range(1, len(offers) + 1)):
        return []
    return [ShopScreen(slot=slot, offers=tuple(offers), money=num(money["money"]))]


def _bought(m: re.Match[str]) -> list[Event]:
    parsed = gadget_stats(m["stats"])
    if parsed is None:
        return []
    return [GadgetBought(name=m["name"], bonuses=parsed[0], worn=m["bag"] is None)]


def _changed(text: str) -> list[Event]:
    m = _CHANGE.search(text)
    if m is None:
        return []
    parsed = gadget_stats(m["stats"])
    if parsed is None:
        return []
    event = GadgetWorn if m["verb"] == "надел" else GadgetUnworn
    return [
        event(
            grade=m["grade"],
            level=int(m["level"]) if m["level"] else None,
            name=m["name"],
            bonuses=parsed[0],
            mark=parsed[1],
        )
    ]


def _upgrades(text: str) -> list[Event]:
    items: list[tuple[str, Gadget]] = []
    for line in text.split("\n"):
        parsed = gadget_line(line)
        if parsed is not None and parsed[1] is not None:
            items.append((parsed[1], parsed[0]))
    stocks: dict[str, int] = {}
    chances: dict[str, int] = {}
    for m in _STOCK.finditer(text):
        kind = KIND_BY_ICON[m["icon"]]
        stocks[kind] = int(m["n"])
        chances[kind] = int(m["chance"])
    # Ничего не надето — строк гаджетов нет; экран узнаётся по полному блоку запасов.
    if len(stocks) != 3:
        return []
    man = _UPGRADEMAN.search(text)
    return [
        UpgradesScreen(
            items=tuple(items),
            upgrademan_pct=int(man["pct"]) if man else None,
            stocks=stocks,
            chances=chances,
            confirm="/ucoff" in text,
        )
    ]


def _up_slot(msg: IncomingMessage) -> str | None:
    """Up-слот из кнопок `up_<слот>_<вид>…`; `up_auto_<слот>` сюда не попадает."""
    for button in msg.inline:
        m = _UP_BUTTON.match(button.data or "")
        if m is not None and m["slot"] in SLOTS:
            return m["slot"]
    return None


def _level(m: re.Match[str]) -> int | None:
    return int(m["level"]) if m["level"] else None


def _upgrade_screen(msg: IncomingMessage, text: str, slot: str) -> list[Event]:
    head = _UP_BODY.match(text)
    if head is None:
        return []
    stocks: dict[str, int] = {
        KIND_BY_ICON[m["icon"]]: int(m["n"]) for m in _UP_STOCK.finditer(text)
    }
    chances: dict[str, int] = {}
    for button in msg.inline:
        m = _BUTTON_CHANCE.match(button.text)
        if m is not None:
            chances[KIND_BY_ICON[m["icon"]]] = int(m["chance"])
    if len(stocks) != 3 or len(chances) != 3:
        return []
    return [
        UpgradeScreen(
            up_slot=slot,
            grade=head["grade"],
            level=_level(head),
            name=head["name"],
            stocks=stocks,
            chances=chances,
        )
    ]


def _attempt(text: str, slot: str) -> list[Event]:
    m = _ATTEMPT.match(text)
    if m is None:
        return []
    used = KIND_BY_ICON.get(m["used"])
    success = {"💪": True, "😞": False}.get(m["mid"][:1])
    if used is None or success is None:
        return []
    return [
        UpgradeAttempt(
            up_slot=slot,
            name=m["name"],
            grade=m["grade"],
            level=_level(m) or 0,
            used=used,
            success=success,
            next_ok=int(m["ok"]),
            next_fail=int(m["fail"]),
        )
    ]


def _confirm(msg: IncomingMessage, text: str) -> list[Event]:
    m = _CONFIRM.match(text)
    if m is None:
        return []
    for button in msg.inline:
        accept = _UP_BUTTON.match(button.data or "")
        if accept is not None and accept["n"] and accept["slot"] in SLOTS:
            kind = KIND_BY_CALLBACK[accept["kind"]]
            return [
                UpgradeConfirm(
                    up_slot=accept["slot"],
                    name=m["name"],
                    grade=m["grade"],
                    level=_level(m),
                    upgrade=kind,
                    chance=int(m["chance"]),
                )
            ]
    return []


def _declined(text: str, slot: str) -> list[Event]:
    m = _DECLINED.match(text)
    if m is None:
        return []
    return [UpgradeDeclined(up_slot=slot, name=m["name"], grade=m["grade"], level=_level(m) or 0)]


def recognize_gadgets(msg: IncomingMessage) -> list[Event]:
    text = msg.text or ""
    if _SHOP_NOTE in text:
        return _shop(text)
    if m := _BOUGHT.match(text):
        return _bought(m)
    if _CHANGE.search(text) is not None and text.startswith("Гаджеты при тебе:"):
        return _changed(text)
    if text.startswith("Гаджеты: (на апгрейд)"):
        return _upgrades(text)
    if m := _CONFIRM_SET.match(text):
        return [UpgradeConfirmSet(on=m["state"] == "включено")]
    slot = _up_slot(msg)
    if slot is None:
        return []
    if text.startswith("👎Отказался от улучшения."):
        return _declined(text, slot)
    if "\n\nПрименил " in text:
        return _attempt(text, slot)
    if "\n\nПрименить " in text:
        return _confirm(msg, text)
    if "\n\nУ тебя\n" in text:
        return _upgrade_screen(msg, text, slot)
    return []


RECOGNIZERS = (recognize_gadgets,)

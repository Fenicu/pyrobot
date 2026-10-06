"""Поездки (🏢Офис → 🚦Поездки): экран «Транспорт», старт поездки, отказ по кулдауну вида и
итог — сюжет и блок «Ты получил:» последним, без строки продолжения дела; что это итог поездки,
узнаёт редьюсер по идущей поездке."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import ClassVar

from app.engine.events import Event
from app.engine.parsing.common import DURATION, Rewards, dur, parse_rewards
from app.engine.types import IncomingMessage

# Поездка занимает персонажа как дело; итог — ровно через 10 минут после старта.
TRIP = "trip"
TRIP_SPAN = timedelta(minutes=10)


@dataclass(frozen=True, slots=True)
class Vehicle:
    """Вид транспорта: строка экрана «Транспорт», кнопка вида под ним и кулдаун от старта."""

    key: str
    screen: str
    button: str
    cooldown: timedelta


VEHICLES: dict[str, Vehicle] = {
    v.key: v
    for v in (
        Vehicle("car", "🚕Ааавтомобиль", "🚕Тачка", timedelta(hours=20)),
        Vehicle("tram", "🚃Трамвай", "🚃Трамвай", timedelta(hours=18)),
        Vehicle("sled", "🛷Санки", "🛷Санки", timedelta(hours=18)),
        Vehicle("bike", "🚲Велосипед", "🚲Велик", timedelta(hours=23)),
        Vehicle("scooter", "🛴Самокат", "🛴Самокат", timedelta(hours=5)),
        Vehicle("tractor", "🚜Трактор", "🚜Трактор", timedelta(hours=19)),
    )
}
_EMOJI = re.compile(r"\A\W+")
_BY_EMOJI = {m.group(): v.key for v in VEHICLES.values() if (m := _EMOJI.match(v.screen))}
# В старте авто значка нет: «потратить на это ведро с гайками».
_START_PHRASES = {"ведро с гайками": "car"}
_MONTH_NAMES = "января февраля марта апреля мая июня июля августа сентября октября ноября декабря"
_MONTHS = {name: n for n, name in enumerate(_MONTH_NAMES.split(), start=1)}
_HEADER = "Транспорт\nОтправляйся в полные приключений поездки.\n\n"
_UNIT = r"\d+ ?(?:🔩|💵|⏰)"
_PRICED = re.compile(r"\A(?P<name>\W+\w[^\n]*?) - (?P<cost>" + _UNIT + r"(?:, " + _UNIT + r")*)\Z")
# Заглушка без цены: «🚃Трамвай - ждёт рельса», «🛷Санки» и строка пояснения. Ни цифр, ни значков
# цены: незнакомый формат цены — не заглушка, а неразобранный экран.
_NO_PRICE = r"[^\d\n🔩💵⏰$,]+"
_PLACEHOLDER = re.compile(r"\A(?P<name>\W+\w+)(?: - " + _NO_PRICE + r")?\Z")
_PLACEHOLDER_NOTE = re.compile(r"\A[^\d\n🔩💵⏰$]+\Z")
_COST = re.compile(r"(\d+) ?(🔩|💵)")
_LEFT = re.compile(r"\AЧерез (?P<t>" + DURATION + r")\Z")
_EXPIRES = re.compile(r"\Aгод(?:ны|ен|на|но) до (?P<day>\d{1,2}) (?P<month>[а-я]+)\Z")
_START = re.compile(r"\A(?P<head>[^\n]*?)Вернёшься через (?P<t>" + DURATION + r")")
_REFUSAL = re.compile(
    r"\A(?P<head>[^\n]+?) ещё (?P<t>" + DURATION + r")\n(?:Подкопи силёнок|Приходи, когда)"
)
# Последний блок сообщения — «Ты получил:» с наградой; сюжет может содержать пустые строки.
_REWARDS_ONLY = re.compile(r"\A\S.*\n\nТы получил:\n[^\n]+(?:\n[^\n]+)*\Z", re.S)
# Итоги поездки без награды — только сюжет, целиком.
_NO_REWARD = frozenset(
    {"Долго катался по городу в поисках приключений. Увы, сегодня не твой день."}
)
# Последняя строка «повторить» с командой вида («Заводи по новой! /car», «…ещё раз - /tram»): с
# ней итог поездки — любой сюжет, с наградой или без.
_AGAIN = re.compile(r"\n\n[^\n]+ /(?:" + "|".join(VEHICLES) + r")\Z")
_STORY = re.compile(r"\A\S")


def vehicle_key(name: str) -> str:
    """Ключ вида по строке экрана (по значку); незнакомый вид — ключом служит сама строка: в
    настройках его нет, кулдаун у него — только с экрана."""
    m = _EMOJI.match(name)
    return _BY_EMOJI.get(m.group(), name) if m else name


def _known(head: str) -> str | None:
    for emoji, key in _BY_EMOJI.items():
        if emoji in head:
            return key
    return next((key for phrase, key in _START_PHRASES.items() if phrase in head), None)


def season_end(month_day: tuple[int, int], seen: date) -> date:
    """Сезонный срок «годны до 9 мая» без года — первая такая дата не раньше дня экрана (с
    запасом в сутки на часовой пояс): показанный на экране вид не мог кончиться раньше."""
    month, day = month_day
    earliest = seen - timedelta(days=1)
    for year in range(earliest.year, earliest.year + 9):
        try:
            end = date(year, month, day)
        except ValueError:
            continue
        if end >= earliest:
            return end
    raise ValueError(f"no date for {month}-{day} near {seen}")


@dataclass(frozen=True, slots=True, kw_only=True)
class TripVehicle:
    """Вид на экране: цена (заглушка «ждёт рельса» — без цены, недоступен), остаток кулдауна
    «Через …» и сезонный срок (месяц, день)."""

    key: str
    name: str
    available: bool
    raw: int | None = None
    money: int | None = None
    left_s: int | None = None
    expires: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TripsScreen(Event):
    kind: ClassVar[str] = "trips_screen"
    vehicles: tuple[TripVehicle, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class TripStarted(Event):
    """Старт поездки: вид (None — незнакомый текст с общим «Вернёшься через») и списанные 🔩
    и 💵."""

    kind: ClassVar[str] = "trip_started"
    outcome: ClassVar[bool] = True
    vehicle: str | None
    raw: int
    money: int
    duration_s: int


@dataclass(frozen=True, slots=True, kw_only=True)
class TripRefused(Event):
    kind: ClassVar[str] = "trip_refused"
    vehicle: str
    left_s: int


@dataclass(frozen=True, slots=True, kw_only=True)
class RewardsOnly(Event):
    """«<сюжет>», пустая строка, «Ты получил:» и награда (или известный сюжет без награды; или
    любой из них со строкой «повторить» с командой вида) — сообщение, которое не распознало ни
    одно семейство: итог поездки, если она идёт (решает редьюсер), иначе не применяется."""

    kind: ClassVar[str] = "rewards_only"
    claimed_by: ClassVar[str | None] = "trip"
    rewards: Rewards


def _vehicle(block: str) -> TripVehicle | None:
    first, *rest = block.split("\n")
    priced = _PRICED.match(first)
    head = priced or _PLACEHOLDER.match(first)
    if head is None:
        return None
    name = head["name"]
    left: int | None = None
    expires: tuple[int, int] | None = None
    for line in rest:
        if m := _LEFT.match(line):
            left = dur(m["t"])
        elif (m := _EXPIRES.match(line)) and m["month"] in _MONTHS:
            expires = (_MONTHS[m["month"]], int(m["day"]))
        elif priced is not None or not _PLACEHOLDER_NOTE.match(line):
            # Незнакомая строка у вида с ценой или строка с ценой у заглушки — экран не разобран
            # целиком.
            return None
    if priced is None:
        return TripVehicle(key=vehicle_key(name), name=name, available=False, left_s=left)
    costs = {unit: int(n) for n, unit in _COST.findall(priced["cost"])}
    return TripVehicle(
        key=vehicle_key(name),
        name=name,
        available=True,
        raw=costs.get("🔩", 0),
        money=costs.get("💵", 0),
        left_s=left,
        expires=expires,
    )


def _screen(text: str) -> list[Event]:
    body = text[len(_HEADER) :]
    vehicles: list[TripVehicle] = []
    for block in body.split("\n\n") if body else ():
        vehicle = _vehicle(block)
        if vehicle is None:
            return []
        vehicles.append(vehicle)
    return [TripsScreen(vehicles=tuple(vehicles))]


def recognize_trips(msg: IncomingMessage) -> list[Event]:
    text = msg.text or ""
    if text.startswith(_HEADER):
        return _screen(text)
    if (m := _START.match(text)) is not None:
        head = m["head"]
        vehicle = _known(head)
        if vehicle is None and "Перед поездкой" not in head:
            return []
        costs = _COST.findall(head)
        return [
            TripStarted(
                vehicle=vehicle,
                raw=sum(int(n) for n, unit in costs if unit == "🔩"),
                money=sum(int(n) for n, unit in costs if unit == "💵"),
                duration_s=dur(m["t"]),
            )
        ]
    if (m := _REFUSAL.match(text)) is not None and (vehicle := _known(m["head"])) is not None:
        return [TripRefused(vehicle=vehicle, left_s=dur(m["t"]))]
    return []


def recognize_rewards_only(msg: IncomingMessage) -> list[Event]:
    text = msg.text or ""
    if text in _NO_REWARD:
        return [RewardsOnly(rewards=Rewards())]
    if (again := _AGAIN.search(text)) is not None:
        story = text[: again.start()]
        if _REWARDS_ONLY.match(story):
            return [RewardsOnly(rewards=parse_rewards(story))]
        if _STORY.match(story) and "Ты получил:" not in story:
            return [RewardsOnly(rewards=Rewards())]
        return []
    return [RewardsOnly(rewards=parse_rewards(text))] if _REWARDS_ONLY.match(text) else []


RECOGNIZERS = (recognize_trips,)
# Только если сообщение не распознало ни одно семейство.
FALLBACKS = (recognize_rewards_only,)

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

log = logging.getLogger(__name__)
Src = Literal["screen", "derived", "doubtful"]
SCHEMA_VERSION = 1


class Obs[T](BaseModel):
    model_config = ConfigDict(frozen=True)
    value: T
    at: datetime
    src: Src = "screen"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Skills(_Frozen):
    practice: int
    theory: int
    cunning: int
    wisdom: int


class BusyState(_Frozen):
    activity: str
    until: datetime


class PriceState(_Frozen):
    motivation: int = 0
    money: int = 0
    minutes: int = 0
    details: int = 0
    white: int = 0
    blue: int = 0
    knowledge: int = 0


class FoodStockState(_Frozen):
    count: int
    low: int
    high: int


class Upgrades(_Frozen):
    white: int
    blue: int
    red: int


class GorbushkaState(_Frozen):
    state: str
    won: int | None = None
    total: int | None = None
    ticket_until: datetime | None = None
    next_fight_at: datetime | None = None
    comeback_at: datetime | None = None
    fight_cost: int | None = None


class RefusalState(_Frozen):
    reason: str
    need: int | None = None


class TeamTask(_Frozen):
    current: int
    goal: int
    resource: str
    # День заданий (00:00 MSK). Снимки прошлых версий дня не знают — такое значение планировщик
    # считает неизвестным.
    day: date | None = None
    status: Literal["none", "active", "done"] = "active"
    activities: tuple[str, ...] = ()


class TaskOfferState(_Frozen):
    type: str
    level: str
    goal: int
    trophies: int


class ChosenTaskState(_Frozen):
    type: str | None = None
    level: str | None = None
    goal: int = 0
    resource: str = ""
    activities: tuple[str, ...] = ()


class PersonalTask(_Frozen):
    """Личное задание за день `day`: варианты, выбранное (`current` — прогресс) или выполненное
    (у выполненного по сообщению о выполнении `chosen` может быть неизвестно)."""

    day: date
    status: Literal["offers", "active", "done"]
    offers: tuple[TaskOfferState, ...] = ()
    chosen: ChosenTaskState | None = None
    current: int = 0


class StockLimits(_Frozen):
    min_buy: int
    max_sell: int
    reserve: int
    open_hour: int
    close_hour: int


class TargetSet(_Frozen):
    target: str
    battle_at: datetime


class SmoothieRecipeState(_Frozen):
    recipe: str
    bonus: str


class MetroRunRef(_Frozen):
    """Идущий забег метро: его сообщение и наблюдение времени битвы на входе — игра выкинет
    персонажа за 15 минут до этой битвы, а не до той, что покажет профиль после неё."""

    message_id: int
    battle_at: Obs[datetime] | None = None


class LotteryState(_Frozen):
    """Снимок одного тиража: по валютам (`money`, `knowledge`, `raw`, `details`) куплено, лимит и
    цена; None — неизвестно (ответ покупки без экрана этого тиража). `until` — закрытие продажи.
    `short` — валюты, на которые при последней попытке не хватило, и сколько ресурса было после неё
    (None — неизвестно)."""

    draw: int
    until: datetime
    bought: dict[str, int] | None = None
    limits: dict[str, int] | None = None
    prices: dict[str, int] | None = None
    short: dict[str, int | None] = {}


class ArtifactCollect(_Frozen):
    """Идущий в игре сбор артефакта: что собирается и когда кончится (время экрана + остаток)."""

    artifact: str
    ends_at: datetime


class VehicleState(_Frozen):
    """Вид транспорта: строка экрана «Транспорт», цена в 🔩 и 💵 (None — неизвестна), доступность
    (False — заглушка без цены: «ждёт рельса», «Полозья точатся…»), с какого момента можно ехать
    (None — неизвестно) и последний день сезона («годны до 9 мая»)."""

    name: str
    available: bool = True
    raw: int | None = None
    money: int | None = None
    ready_at: datetime | None = None
    expires_on: date | None = None


class TripRef(_Frozen):
    """Последняя начатая поездка: вид (None — незнакомый текст старта), старт и пришёл ли итог."""

    vehicle: str | None
    started_at: datetime
    done: bool = False


class TripsState(_Frozen):
    """Транспорт по видам (ключ — `car`, `tram`…, у незнакомого вида — строка экрана) и последняя
    поездка — по ней узнаётся её итог."""

    vehicles: dict[str, VehicleState] = {}
    last: TripRef | None = None


class ActivityStat(_Frozen):
    """Скользящее среднее наград одного дела (без его цены)."""

    count: int = 0
    exp: float = 0
    money: float = 0
    knowledge: float = 0
    details: float = 0
    raw: float = 0


# Стартовые значения — средние по корпусу (mechanics_core §2.4, живой замер переработки; прогулка
# и конфа — средние всех итогов 2024–2025 из серверного поиска вместе с провалами).
DEED_PRIORS = {
    "harvest": ActivityStat(exp=203),
    "job": ActivityStat(exp=117, money=25.7, details=2.3, raw=0.8),
    "learn": ActivityStat(exp=235, knowledge=11),
    "dconv": ActivityStat(exp=253, raw=5),
    "walk": ActivityStat(exp=173, money=2.6, raw=0.7),
    "confa": ActivityStat(exp=112, knowledge=31),
    # Не дело, но победа над продаваном копится так же. ⚙️ — нижняя оценка без ⚫️VIP-сета
    # (не у всех он есть), дальше — среднее по боям персонажа: с сетом оно выше.
    "gorbushka": ActivityStat(exp=240, money=37, knowledge=7, details=12),
}
DEFAULT_PRICES = {
    "harvest": PriceState(motivation=1, money=30, minutes=5),
    "job": PriceState(motivation=1, minutes=2),
    "learn": PriceState(motivation=2, minutes=4),
    "dconv": PriceState(motivation=1, money=5, details=10, minutes=6),
    "eat": PriceState(money=5, minutes=5),
    "walk": PriceState(motivation=1, minutes=5),
    "confa": PriceState(motivation=3, money=7, minutes=10),
    "rob": PriceState(motivation=1, minutes=8),
}


class CharacterState(_Frozen):
    schema_version: int = SCHEMA_VERSION
    level: Obs[int] | None = None
    exp: Obs[int] | None = None
    exp_next: Obs[int] | None = None
    money: Obs[int] | None = None
    stamina: Obs[int] | None = None
    knowledge: Obs[int] | None = None
    raw: Obs[int] | None = None
    details: Obs[int] | None = None
    motivation: Obs[int] | None = None
    motivation_max: Obs[int] | None = None
    motivation_next_at: Obs[datetime | None] | None = None
    bag: Obs[int] | None = None
    bag_cap: Obs[int] | None = None
    tangerines: Obs[int] | None = None
    skills: Obs[Skills] | None = None
    battle_at: Obs[datetime] | None = None
    battle_target: Obs[str | None] | None = None
    battle_target_set: Obs[TargetSet] | None = None
    busy: Obs[BusyState | None] | None = None
    sleep_deadline: Obs[datetime | None] | None = None
    woke_at: Obs[datetime] | None = None
    sleep_allowed_at: Obs[datetime | None] | None = None
    levelup_pending: Obs[bool] | None = None
    prices: dict[str, Obs[PriceState]] = {}
    food_stock: Obs[dict[str, FoodStockState]] | None = None
    fastfood_ready_at: Obs[datetime] | None = None
    books: Obs[int] | None = None
    book_ready_at: Obs[datetime] | None = None
    cards: Obs[int] | None = None
    card_ready_at: Obs[datetime] | None = None
    prizebox: Obs[bool] | None = None
    prizebox_ready_at: Obs[datetime | None] | None = None
    containers_small: Obs[int] | None = None
    containers_medium: Obs[int] | None = None
    upgrades: Obs[Upgrades] | None = None
    gorbushka: Obs[GorbushkaState] | None = None
    last_refusal: Obs[RefusalState] | None = None
    team_task: Obs[TeamTask] | None = None
    daily_personal: Obs[PersonalTask] | None = None
    # Своя компания — код, как в /buys_<код>_N, по значку перед именем в профиле (☣️ → bmesa);
    # значение None — значок в последнем профиле не распознан.
    company: Obs[str | None] | None = None
    # Тег команды из профиля и экрана команды; значение None — в профиле тега нет, не в команде.
    team_tag: Obs[str | None] | None = None
    factory_wins: Obs[int] | None = None
    # Личная слава 🏆 с экрана команды.
    glory: Obs[int] | None = None
    factory_won_at: Obs[datetime] | None = None
    factory_signed: Obs[bool] | None = None
    factory_skip: Obs[bool] | None = None
    factory_call_at: Obs[datetime] | None = None
    # День битвы в последнем полученном личном отчёте о фабрике (/fb).
    factory_report_day: Obs[date] | None = None
    bulls_won_at: Obs[datetime] | None = None
    bulls_invite: Obs[str] | None = None
    stock_quotes: Obs[dict[str, int]] | None = None
    stock_holdings: Obs[dict[str, int]] | None = None
    stock_limits: Obs[StockLimits] | None = None
    smoothie_ingredients: Obs[dict[str, int]] | None = None
    smoothie_bonus: Obs[str | None] | None = None
    smoothie_recipe: Obs[SmoothieRecipeState] | None = None
    tangerine_ready_at: Obs[datetime] | None = None
    tangerine_not_player: Obs[str] | None = None
    metro_ready_at: Obs[datetime] | None = None
    # Идущий забег метро (None — вышел); момент — последний экран забега.
    metro_message: Obs[MetroRunRef | None] | None = None
    lottery: Obs[LotteryState] | None = None
    # Уровни своих артефактов (экран 👾Артефакты, строки частей в итогах дел).
    artifacts: Obs[dict[str, int]] | None = None
    # Идущий сбор по экрану артефактов; значение None — на экране сбора нет.
    artifact_collect: Obs[ArtifactCollect | None] | None = None
    # Транспорт с экрана «Транспорт», стартов и отказов поездок.
    trips: Obs[TripsState] | None = None
    activity_stats: dict[str, ActivityStat] = {}
    # Ключи «чат:сообщение:вид» применённых итогов → время создания сообщения:
    # правка итога не начисляет повторно (горизонт хранения — в редьюсере).
    applied: dict[str, datetime] = {}


def load_state(data: dict[str, Any]) -> CharacterState:
    if not data:
        return CharacterState()
    if data.get("schema_version") != SCHEMA_VERSION:
        log.warning("state snapshot schema %r ignored", data.get("schema_version"))
        return CharacterState()
    try:
        return CharacterState.model_validate(data)
    except ValidationError:
        log.exception("state snapshot invalid, starting empty")
        return CharacterState()


def company_of(data: dict[str, Any]) -> str | None:
    """Своя компания из снимка состояния; None — ещё не видели в профиле или значок не
    распознан. Без разбора всего снимка: шлюз спрашивает её перед каждой отправкой."""
    if data.get("schema_version") != SCHEMA_VERSION:
        return None
    seen = data.get("company")
    value = seen.get("value") if isinstance(seen, dict) else None
    return value if isinstance(value, str) else None


def dump_state(state: CharacterState) -> dict[str, Any]:
    return state.model_dump(mode="json")


def is_fresh(obs: Obs[Any] | None, now: datetime, max_age: timedelta) -> bool:
    return obs is not None and obs.src != "doubtful" and now - obs.at <= max_age


VOLATILE = frozenset(
    {"money", "stamina", "motivation", "busy", "exp", "knowledge", "raw", "details"}
)
TIMERS = frozenset(
    {
        "motivation_next_at",
        "battle_at",
        "battle_target",
        "sleep_deadline",
        "woke_at",
        "sleep_allowed_at",
        "levelup_pending",
        "fastfood_ready_at",
        "book_ready_at",
        "card_ready_at",
        "prizebox_ready_at",
        "last_refusal",
        "factory_won_at",
        "factory_signed",
        "factory_skip",
        "factory_call_at",
        "factory_report_day",
        "bulls_won_at",
        "bulls_invite",
        "battle_target_set",
        "smoothie_bonus",
        "smoothie_recipe",
        "tangerine_ready_at",
        "tangerine_not_player",
        "metro_ready_at",
        "metro_message",
        "lottery",
        "artifact_collect",
        "trips",
    }
)
# Значения за день заданий: устаревают сменой дня (её проверяет планировщик), а не возрастом.
DAY_SCOPED = frozenset({"team_task", "daily_personal"})
SLOW_MAX_AGE = timedelta(hours=6)
PRICE_MAX_AGE = timedelta(days=7)


def stale_fields(state: CharacterState, now: datetime, volatile_max_age: timedelta) -> list[str]:
    names: list[str] = []
    for name in CharacterState.model_fields:
        obs = getattr(state, name)
        if not isinstance(obs, Obs):
            continue
        if name in TIMERS or name in DAY_SCOPED:
            if obs.src == "doubtful":
                names.append(name)
            continue
        max_age = volatile_max_age if name in VOLATILE else SLOW_MAX_AGE
        if not is_fresh(obs, now, max_age):
            names.append(name)
    names.extend(
        f"prices.{key}"
        for key, obs in state.prices.items()
        if not is_fresh(obs, now, PRICE_MAX_AGE)
    )
    return names

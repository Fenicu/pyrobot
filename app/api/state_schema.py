"""Публичная схема снимка состояния для OpenAPI и TS-типов админки.

Форма — как у `dump_state`: наблюдаемое поле — `{value, at, src}` или null (ещё не наблюдалось).
Поля не обязательны: до первого сообщения снимок пуст, а в снимке прошлой сборки нет полей,
появившихся позже. Ответ `/state` отдаёт снимок как есть, модель его не пересобирает."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.engine.state.model import (
    SCHEMA_VERSION,
    ActivityStat,
    ArtifactCollect,
    BusyState,
    FoodStockState,
    GadgetsState,
    GorbushkaState,
    LotteryState,
    PersonalTask,
    PriceState,
    RefusalState,
    Skills,
    SmoothieRecipeState,
    Src,
    StockLimits,
    TargetSet,
    TeamTask,
    TripsState,
    UpgradeInfo,
    Upgrades,
)


class Observed[T](BaseModel):
    """Наблюдение поля: значение, момент и источник."""

    model_config = ConfigDict(extra="forbid")
    value: T
    at: datetime
    src: Src


class MetroRunRefOut(BaseModel):
    message_id: int
    battle_at: Observed[datetime] | None = None


class PublicState(BaseModel):
    """Снимок персонажа (`CharacterState` без служебного `applied`)."""

    model_config = ConfigDict(extra="forbid")
    # Необязательные поля без значений по умолчанию в схеме (default_factory): у ключа с default
    # openapi-typescript снимает `?`, а в пустом снимке ключей нет.
    schema_version: int = Field(default_factory=lambda: SCHEMA_VERSION)
    level: Observed[int] | None = None
    exp: Observed[int] | None = None
    exp_next: Observed[int] | None = None
    money: Observed[int] | None = None
    stamina: Observed[int] | None = None
    knowledge: Observed[int] | None = None
    raw: Observed[int] | None = None
    details: Observed[int] | None = None
    motivation: Observed[int] | None = None
    motivation_max: Observed[int] | None = None
    motivation_next_at: Observed[datetime | None] | None = None
    bag: Observed[int] | None = None
    bag_cap: Observed[int] | None = None
    tangerines: Observed[int] | None = None
    skills: Observed[Skills] | None = None
    battle_at: Observed[datetime] | None = None
    battle_target: Observed[str | None] | None = None
    battle_target_set: Observed[TargetSet] | None = None
    busy: Observed[BusyState | None] | None = None
    sleep_deadline: Observed[datetime | None] | None = None
    woke_at: Observed[datetime] | None = None
    sleep_allowed_at: Observed[datetime | None] | None = None
    levelup_pending: Observed[bool] | None = None
    prices: dict[str, Observed[PriceState]] = Field(default_factory=dict)
    food_stock: Observed[dict[str, FoodStockState]] | None = None
    fastfood_ready_at: Observed[datetime] | None = None
    books: Observed[int] | None = None
    book_ready_at: Observed[datetime] | None = None
    cards: Observed[int] | None = None
    card_ready_at: Observed[datetime] | None = None
    prizebox: Observed[bool] | None = None
    prizebox_ready_at: Observed[datetime | None] | None = None
    containers_small: Observed[int] | None = None
    containers_medium: Observed[int] | None = None
    upgrades: Observed[Upgrades] | None = None
    upgrade_info: Observed[UpgradeInfo] | None = None
    gorbushka: Observed[GorbushkaState] | None = None
    last_refusal: Observed[RefusalState] | None = None
    team_task: Observed[TeamTask] | None = None
    daily_personal: Observed[PersonalTask] | None = None
    company: Observed[str | None] | None = None
    team_tag: Observed[str | None] | None = None
    factory_wins: Observed[int] | None = None
    glory: Observed[int] | None = None
    factory_won_at: Observed[datetime] | None = None
    factory_signed: Observed[bool] | None = None
    factory_skip: Observed[bool] | None = None
    factory_call_at: Observed[datetime] | None = None
    factory_report_day: Observed[date] | None = None
    bulls_won_at: Observed[datetime] | None = None
    bulls_invite: Observed[str] | None = None
    stock_quotes: Observed[dict[str, int]] | None = None
    stock_holdings: Observed[dict[str, int]] | None = None
    stock_limits: Observed[StockLimits] | None = None
    smoothie_ingredients: Observed[dict[str, int]] | None = None
    smoothie_bonus: Observed[str | None] | None = None
    smoothie_recipe: Observed[SmoothieRecipeState] | None = None
    tangerine_ready_at: Observed[datetime] | None = None
    tangerine_not_player: Observed[str] | None = None
    metro_ready_at: Observed[datetime] | None = None
    metro_message: Observed[MetroRunRefOut | None] | None = None
    lottery: Observed[LotteryState] | None = None
    artifacts: Observed[dict[str, int]] | None = None
    artifact_collect: Observed[ArtifactCollect | None] | None = None
    trips: Observed[TripsState] | None = None
    gadgets: Observed[GadgetsState] | None = None
    activity_stats: dict[str, ActivityStat] = Field(default_factory=dict)

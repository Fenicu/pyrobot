from __future__ import annotations

from collections.abc import Callable
from typing import Literal, Protocol

from pydantic import BaseModel, Field


class EngineSection(BaseModel):
    mode: Literal["dry_run", "live"] = "dry_run"
    killed: bool = False
    kill_reason: str | None = None
    min_request_interval_s: float = Field(default=1.6, ge=0)
    antiflood_retry_max: int = Field(default=2, ge=0)
    antiflood_pause_s: float = Field(default=10.0, ge=0)
    action_ttl_s: float = Field(default=60.0, gt=0)
    default_expect_timeout_s: float = Field(default=20.0, gt=0)
    click_answer_timeout_s: float = Field(default=4.0, gt=0, le=30)
    recovered_react_max_age_min: int = Field(default=10, ge=0)
    refresh_min_interval_s: float = Field(default=120.0, gt=0)
    state_stale_after_min: int = Field(default=15, ge=1)
    paused: bool = False
    urgent_while_paused: bool = True
    manual_while_paused: bool = True


class TelegramSection(BaseModel):
    expected_user_id: int = 267519921


class ChatsSection(BaseModel):
    game_chat_id: int = 227859379
    swinfo_chat_id: int = -1001109615116
    swinfo_user_id: int = 376592453
    smoothie_channel_id: int = -1001356300612
    tangerine_chat_id: int = -1001377961602
    tangerine_reply_to: int = 927136
    bulls_invite_chat_id: int | None = None


class FeaturesSection(BaseModel):
    """Включённые механики: планировщик их выбирает, шлюз пропускает их команды не вручную."""

    deeds: bool = True
    books: bool = True
    fastfood: bool = True
    cards_containers: bool = True
    gorbushka: bool = True
    sleep: bool = True
    levelup: bool = True
    metro: bool = True
    factory: bool = True
    bulls: bool = True
    stocks_dump: bool = True
    smoothie: bool = True
    tangerine: bool = True
    lottery: bool = False
    casino: bool = False
    arena: bool = False
    pet_feast: bool = False
    daily_tasks: bool = False
    paid_info: bool = False
    seasonal: bool = False


Deed = Literal["harvest", "job", "learn", "dconv", "walk", "confa", "rob"]


class StrategySection(BaseModel):
    weight_xp: float = Field(default=1.0, ge=0)
    weight_money: float = Field(default=1.0, ge=0)
    weight_resources: float = Field(default=0.5, ge=0)
    weight_team: float = Field(default=0.5, ge=0)
    # Масштабы «типичного дохода на 1🔥» — приводят разные единицы к сравнимому виду.
    exp_scale: float = Field(default=200.0, gt=0)
    money_scale: float = Field(default=30.0, gt=0)
    resource_scale: float = Field(default=10.0, gt=0)
    deeds: tuple[Deed, ...] = ("harvest", "job", "learn", "dconv")


Food = Literal["hotdog", "pizza", "burger", "banana"]


class FoodSection(BaseModel):
    order: tuple[Food, ...] = ("hotdog", "pizza", "burger")
    banana_reserve: int = Field(default=50, ge=0)


class SleepSection(BaseModel):
    duration_h: int = Field(default=7, ge=7, le=12)
    lead_min: int = Field(default=120, ge=10)
    hotel_if_cash_after_reserve_ge: int | None = None


class LevelupSection(BaseModel):
    policy: Literal["balanced"] = "balanced"


class Settings(BaseModel):
    engine: EngineSection = Field(default_factory=EngineSection)
    telegram: TelegramSection = Field(default_factory=TelegramSection)
    chats: ChatsSection = Field(default_factory=ChatsSection)
    features: FeaturesSection = Field(default_factory=FeaturesSection)
    strategy: StrategySection = Field(default_factory=StrategySection)
    food: FoodSection = Field(default_factory=FoodSection)
    sleep: SleepSection = Field(default_factory=SleepSection)
    levelup: LevelupSection = Field(default_factory=LevelupSection)


class SettingsConflict(Exception):
    pass


SettingsChange = Callable[[Settings], Settings]


class SettingsProvider(Protocol):
    @property
    def current(self) -> Settings: ...

    @property
    def version(self) -> int: ...

    async def update(
        self, change: SettingsChange, *, changed_by: str, expected_version: int | None = None
    ) -> Settings: ...


class StaticSettings:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or Settings()
        self._version = 0

    @property
    def current(self) -> Settings:
        return self._settings

    @property
    def version(self) -> int:
        return self._version

    async def update(
        self, change: SettingsChange, *, changed_by: str, expected_version: int | None = None
    ) -> Settings:
        if expected_version is not None and expected_version != self._version:
            raise SettingsConflict(f"version {self._version} != {expected_version}")
        self._settings = Settings.model_validate(change(self._settings).model_dump())
        self._version += 1
        return self._settings

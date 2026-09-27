from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, Field

# Меняется только своими эндпоинтами движка (kill/unkill, pause/resume): у них latch шлюза,
# проверка блокировки экземпляра и аудит, а PATCH настроек обошёл бы их.
READ_ONLY: dict[str, Any] = {"readOnly": True}


class EngineSection(BaseModel):
    mode: Literal["dry_run", "live"] = "dry_run"
    killed: bool = Field(default=False, json_schema_extra=READ_ONLY)
    kill_reason: str | None = Field(default=None, json_schema_extra=READ_ONLY)
    min_request_interval_s: float = Field(default=1.6, ge=0)
    antiflood_retry_max: int = Field(default=2, ge=0)
    antiflood_pause_s: float = Field(default=10.0, ge=0)
    action_ttl_s: float = Field(default=60.0, gt=0)
    default_expect_timeout_s: float = Field(default=20.0, gt=0)
    click_answer_timeout_s: float = Field(default=4.0, gt=0, le=30)
    recovered_react_max_age_min: int = Field(default=10, ge=0)
    refresh_min_interval_s: float = Field(default=120.0, gt=0)
    state_stale_after_min: int = Field(default=15, ge=1)
    paused: bool = Field(default=False, json_schema_extra=READ_ONLY)
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
    battle: bool = True
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


Target = Literal["📯Pied Piper", "🤖Hooli", "⚡️Stark Ind.", "☂️Umbrella", "🎩Wayne Ent.", "🛡Защита"]


class BattleSection(BaseModel):
    target: Target = "📯Pied Piper"
    # Цель на конкретную битву: час битвы по Москве → цель.
    overrides: dict[Annotated[int, Field(ge=0, le=23)], Target] = Field(default_factory=dict)


class StocksSection(BaseModel):
    cash_floor: int = Field(default=150, ge=0)
    min_dump: int = Field(default=200, ge=1)
    sell_cap_margin: int = Field(default=5, ge=0)
    dump_lead_min: int = Field(default=5, ge=1)


class TangerineSection(BaseModel):
    interval_h: int = Field(default=20, ge=20)


MetroBuff = Literal["fastMove", "strong", "firstAid"]


class MetroSection(BaseModel):
    # До битвы нужно не меньше max(min_budget_min, p90 прошлых забегов × 1.5) + запасы.
    min_budget_min: int = Field(default=60, ge=1)
    # Игра выкидывает из метро за 15 минут до битвы с половиной найденного.
    battle_margin_min: int = Field(default=15, ge=15)
    extra_margin_min: int = Field(default=10, ge=0)
    # Бафы за 🕳; за 🌐 — никогда (донат).
    buffs: tuple[MetroBuff, ...] = ("fastMove", "strong", "firstAid")
    heal_at: int = Field(default=50, ge=0, le=100)
    heal_before_exit: bool = True
    chest_min_packs: int = Field(default=2, ge=0)
    npc_low_enabled: bool = True
    npc_high_enabled: bool = False
    # Без аптечек с NPC не драться при 🔋 ниже этого: на экране NPC лечиться нельзя.
    npc_min_stamina: int = Field(default=30, ge=0)

    @property
    def margin_min(self) -> int:
        """Запас до битвы на забег: выброс игрой и свой."""
        return self.battle_margin_min + self.extra_margin_min


class RetentionSection(BaseModel):
    # Журнал: сообщения (с нераспознанными), действия, запуски сценариев, уведомления.
    messages_days: int = Field(default=90, ge=1)
    decisions_days: int = Field(default=30, ge=1)
    # Долгая статистика: ряды метрик и забеги метро.
    metrics_days: int = Field(default=365, ge=1)


class Settings(BaseModel):
    engine: EngineSection = Field(default_factory=EngineSection)
    telegram: TelegramSection = Field(default_factory=TelegramSection)
    chats: ChatsSection = Field(default_factory=ChatsSection)
    features: FeaturesSection = Field(default_factory=FeaturesSection)
    strategy: StrategySection = Field(default_factory=StrategySection)
    food: FoodSection = Field(default_factory=FoodSection)
    sleep: SleepSection = Field(default_factory=SleepSection)
    levelup: LevelupSection = Field(default_factory=LevelupSection)
    battle: BattleSection = Field(default_factory=BattleSection)
    stocks: StocksSection = Field(default_factory=StocksSection)
    tangerine: TangerineSection = Field(default_factory=TangerineSection)
    metro: MetroSection = Field(default_factory=MetroSection)
    retention: RetentionSection = Field(default_factory=RetentionSection)


class SettingsConflict(Exception):
    pass


class SettingsPatchError(ValueError):
    def __init__(self, code: str, path: str) -> None:
        super().__init__(f"{code}: {path}")
        self.code = code
        self.path = path


# Читаются только при старте процесса (парсер, фильтр чатов, вход в Telegram, конвейер).
_RESTART_REQUIRED = ("chats.", "telegram.", "engine.recovered_react_max_age_min")


def apply_patch(settings: Settings, changes: Mapping[str, Any]) -> Settings:
    """Частичное изменение: секции сливаются, листья (в том числе словари и списки)
    заменяются целиком; незнакомый или read-only путь — `SettingsPatchError`."""
    data = settings.model_dump(mode="json")
    _merge(Settings, data, changes, "")
    return Settings.model_validate(data)


def _merge(
    model: type[BaseModel], data: dict[str, Any], changes: Mapping[str, Any], prefix: str
) -> None:
    for key, value in changes.items():
        path = f"{prefix}{key}"
        field = model.model_fields.get(key)
        if field is None:
            raise SettingsPatchError("unknown_field", path)
        extra = field.json_schema_extra
        if isinstance(extra, dict) and extra.get("readOnly"):
            raise SettingsPatchError("read_only", path)
        section = field.annotation
        if isinstance(section, type) and issubclass(section, BaseModel):
            if not isinstance(value, Mapping):
                raise SettingsPatchError("section_expected", path)
            _merge(section, data[key], value, f"{path}.")
        else:
            data[key] = value


def settings_diff(old: Mapping[str, Any], new: Mapping[str, Any]) -> dict[str, list[Any]]:
    """Изменённые листья JSON-дампа настроек: путь через точку → [было, стало]."""
    out: dict[str, list[Any]] = {}
    _diff(old, new, "", out)
    return out


def _diff(old: Any, new: Any, prefix: str, out: dict[str, list[Any]]) -> None:
    if isinstance(old, Mapping) and isinstance(new, Mapping):
        for key in sorted(old.keys() | new.keys(), key=str):
            _diff(old.get(key), new.get(key), f"{prefix}{key}.", out)
    elif old != new:
        out[prefix.rstrip(".")] = [old, new]


def restart_required(paths: Iterable[str]) -> list[str]:
    return [p for p in paths if p.startswith(_RESTART_REQUIRED)]


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

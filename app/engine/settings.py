from __future__ import annotations

import copy
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, Field, ValidationInfo, field_validator

# Меняется только своими эндпоинтами движка (kill/unkill, pause/resume, сбор артефакта): у них
# latch шлюза, проверка блокировки экземпляра и аудит, а PATCH настроек обошёл бы их.
READ_ONLY: dict[str, Any] = {"readOnly": True}
# Код настройку не читает: менять можно, но ни на что не влияет — в админке она приглушена.
UNUSED: dict[str, Any] = {"x-unused": True}


# Супергруппа в Telegram — id с префиксом -100: -100XXXXXXXXXX ≤ −10¹².
SUPERGROUP_MAX_ID = -1_000_000_000_000


# Длительности, сроки и таймауты ограничены сверху по смыслу поля: огромное значение переполнило бы
# timedelta в планировщике (цикл встал бы) или надолго остановило бы шлюз.
class EngineSection(BaseModel):
    mode: Literal["dry_run", "live"] = "dry_run"
    killed: bool = Field(default=False, json_schema_extra=READ_ONLY)
    kill_reason: str | None = Field(default=None, json_schema_extra=READ_ONLY)
    min_request_interval_s: float = Field(default=1.6, ge=0, le=60)
    antiflood_retry_max: int = Field(default=2, ge=0)
    antiflood_pause_s: float = Field(default=10.0, ge=0, le=600)
    action_ttl_s: float = Field(default=60.0, gt=0, le=3600)
    default_expect_timeout_s: float = Field(default=20.0, gt=0, le=300)
    click_answer_timeout_s: float = Field(default=4.0, gt=0, le=30)
    recovered_react_max_age_min: int = Field(default=10, ge=0, le=1440)
    refresh_min_interval_s: float = Field(default=120.0, gt=0, le=3600)
    state_stale_after_min: int = Field(default=15, ge=1, le=1440)
    paused: bool = Field(default=False, json_schema_extra=READ_ONLY)
    urgent_while_paused: bool = True
    manual_while_paused: bool = True


class ChatsSection(BaseModel):
    game_chat_id: int = 227859379
    swinfo_chat_id: int = -1001109615116
    swinfo_user_id: int = 376592453
    # Канал рецептов смузи своей компании; None — смузи не варится.
    smoothie_channel_id: int | None = None
    tangerine_chat_id: int = -1001377961602
    # Сообщение того, кому дарить мандарины (/gt уходит ответом на него); None — не дарить.
    tangerine_reply_to: int | None = None
    bulls_invite_chat_id: int | None = None
    # Чат команды для пересылки итогов задания и отчёта о фабрике; None — не пересылать. Только
    # супергруппа (-100…) и не чат игры, SWINFO, канал смузи (если задан) или мандарины: личный
    # отчёт не должен уйти не туда. С чатом приглашений к биржевикам совпадать можно: там
    # читаются только они.
    team_chat_id: int | None = Field(default=None, le=SUPERGROUP_MAX_ID)

    @field_validator("team_chat_id")
    @classmethod
    def _team_chat_is_own(cls, value: int | None, info: ValidationInfo) -> int | None:
        others = {
            info.data.get(name)
            for name in (
                "game_chat_id",
                "swinfo_chat_id",
                "smoothie_channel_id",
                "tangerine_chat_id",
            )
        }
        if value is not None and value in others:
            raise ValueError("team chat must differ from game and other known chats")
        return value


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
    lottery: bool = True
    casino: bool = Field(default=False, json_schema_extra=UNUSED)
    arena: bool = Field(default=False, json_schema_extra=UNUSED)
    pet_feast: bool = False
    daily_tasks: bool = True
    robbery_defense: bool = True
    paid_info: bool = False
    seasonal: bool = False


Deed = Literal["harvest", "job", "learn", "dconv", "walk", "confa", "rob"]


class ReserveAhead(BaseModel):
    """За сколько минут до боя Горбушки и открытия метро дела держат под них 🔥; 0 — не держат."""

    gorbushka: int = Field(default=60, ge=0, le=1440)
    metro: int = Field(default=60, ge=0, le=1440)


class StrategySection(BaseModel):
    weight_xp: float = Field(default=1.0, ge=0)
    weight_money: float = Field(default=1.0, ge=0)
    weight_resources: float = Field(default=0.5, ge=0)
    # Масштабы «типичного дохода на 1🔥» — приводят разные единицы к сравнимому виду.
    exp_scale: float = Field(default=200.0, gt=0)
    money_scale: float = Field(default=30.0, gt=0)
    resource_scale: float = Field(default=10.0, gt=0)
    # Основные дела: делят 🔥 поровну, остальные разрешённые (`deeds`) — запасные по оценке.
    focus: tuple[Deed, ...] = ("harvest", "dconv")
    deeds: tuple[Deed, ...] = ("harvest", "job", "learn", "dconv", "walk")
    reserve_ahead_min: ReserveAhead = Field(default_factory=ReserveAhead)


Food = Literal["hotdog", "pizza", "burger", "banana"]


class FoodSection(BaseModel):
    order: tuple[Food, ...] = ("hotdog", "pizza", "burger")
    banana_reserve: int = Field(default=50, ge=0)


class SleepSection(BaseModel):
    duration_h: int = Field(default=7, ge=7, le=12)
    lead_min: int = Field(default=120, ge=10, le=1440)
    hotel_if_cash_after_reserve_ge: int | None = None


class LevelupSection(BaseModel):
    policy: Literal["balanced"] = Field(default="balanced", json_schema_extra=UNUSED)


Target = Literal[
    "📯Pied Piper",
    "🤖Hooli",
    "⚡️Stark Ind.",
    "☂️Umbrella",
    "🎩Wayne Ent.",
    "☣️Black Mesa",
    "🛡Защита",
]


class BattleSection(BaseModel):
    target: Target = "📯Pied Piper"
    # Цель на конкретную битву: час битвы по Москве → цель.
    overrides: dict[Annotated[int, Field(ge=0, le=23)], Target] = Field(default_factory=dict)


class StocksSection(BaseModel):
    cash_floor: int = Field(default=150, ge=0)
    min_dump: int = Field(default=200, ge=1)
    sell_cap_margin: int = Field(default=5, ge=0)
    dump_lead_min: int = Field(default=5, ge=1, le=1440)


class TangerineSection(BaseModel):
    interval_h: int = Field(default=20, ge=20, le=168)


MetroBuff = Literal["fastMove", "strong", "firstAid"]


class MetroSection(BaseModel):
    # До битвы нужно не меньше max(min_budget_min, p90 прошлых забегов × 1.5) + запасы.
    min_budget_min: int = Field(default=60, ge=1, le=1440)
    # Игра выкидывает из метро за 15 минут до битвы с половиной найденного.
    battle_margin_min: int = Field(default=15, ge=15, le=1440)
    extra_margin_min: int = Field(default=10, ge=0, le=1440)
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


PersonalTaskType = Literal[
    "convDets", "robPro", "jobMoney", "materials", "learnKnows", "walkMoney", "confKnows"
]


class DailySection(BaseModel):
    # Среди hard-вариантов личного задания берётся первый по этому порядку — от самого дешёвого
    # для бота: переработка — основное дело, Горбушка идёт и так, дальше по росту лишнего 🔥.
    personal_order: tuple[PersonalTaskType, ...] = (
        "convDets",
        "robPro",
        "jobMoney",
        "materials",
        "learnKnows",
        "walkMoney",
        "confKnows",
    )


# Сколько билетов брать за тираж: число или `max` — до лимита тиража (лимит даёт экран).
TicketCount = Annotated[int, Field(ge=0, strict=True)] | Literal["max"]


class LotteryTickets(BaseModel):
    money: TicketCount = "max"
    knowledge: TicketCount = "max"
    raw: TicketCount = "max"
    details: TicketCount = "max"


class LotteryKeep(BaseModel):
    """Сколько ресурса не тратить на билеты (к 💵 ещё резервы билета Горбушки и отеля)."""

    money: int = Field(default=0, ge=0)
    knowledge: int = Field(default=0, ge=0)
    raw: int = Field(default=0, ge=0)
    details: int = Field(default=0, ge=0)


class LotterySection(BaseModel):
    tickets: LotteryTickets = Field(default_factory=LotteryTickets)
    keep: LotteryKeep = Field(default_factory=LotteryKeep)


ArtifactKey = Literal["book", "fax", "light"]
ArtifactDeed = Literal["walk", "job", "learn"]
RunStatus = Literal["idle", "starting", "active", "paused", "cancelled", "finished"]


class ArtifactsSection(BaseModel):
    """Тактика сбора артефакта: в какие дела бот тратит всю 🔥, порядок — приоритет."""

    # Страницы Букваря до 17 ур. персонажа падают на Работе и Прогулке, с 18 — на Учёбе.
    book_low: tuple[Literal["walk", "job"], ...] = ("walk", "job")
    book_high: tuple[Literal["learn"], ...] = ("learn",)
    fax: tuple[Literal["job"], ...] = ("job",)
    light: tuple[Literal["walk"], ...] = ("walk",)
    lottery_on_start: bool = True

    @field_validator("book_low", "book_high", "fax", "light")
    @classmethod
    def _deeds_order(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("at least one deed")
        if len(set(value)) != len(value):
            raise ValueError("deeds must not repeat")
        return value


class ArtifactLottery(BaseModel):
    """Лотерея до старта сбора или включённая ботом на сбор: флаг механики и её секция."""

    enabled: bool
    lottery: LotterySection


class ArtifactRunSection(BaseModel):
    """Текущий сбор артефакта: меняют эндпоинты `/artifact/*` и движок, не PATCH."""

    artifact: ArtifactKey | None = Field(default=None, json_schema_extra=READ_ONLY)
    status: RunStatus = Field(default="idle", json_schema_extra=READ_ONLY)
    requested_at: datetime | None = Field(default=None, json_schema_extra=READ_ONLY)
    started_at: datetime | None = Field(default=None, json_schema_extra=READ_ONLY)
    ends_at: datetime | None = Field(default=None, json_schema_extra=READ_ONLY)
    deed_hint: ArtifactDeed | None = Field(default=None, json_schema_extra=READ_ONLY)
    lottery_before: ArtifactLottery | None = Field(default=None, json_schema_extra=READ_ONLY)
    lottery_applied: ArtifactLottery | None = Field(default=None, json_schema_extra=READ_ONLY)
    result_level: int | None = Field(default=None, ge=0, le=100, json_schema_extra=READ_ONLY)


class RetentionSection(BaseModel):
    # Журнал: сообщения (с нераспознанными), действия, запуски сценариев, уведомления.
    messages_days: int = Field(default=90, ge=1, le=3650)
    decisions_days: int = Field(default=30, ge=1, le=3650)
    # Долгая статистика: ряды метрик и забеги метро.
    metrics_days: int = Field(default=365, ge=1, le=3650)
    # Журнал прихода — целыми сутками MSK: сегодня и `ledger_days - 1` суток до него; не меньше 31,
    # чтобы все 30 дней «Итогов» были полными.
    ledger_days: int = Field(default=31, ge=31, le=3650)


class Settings(BaseModel):
    engine: EngineSection = Field(default_factory=EngineSection)
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
    daily: DailySection = Field(default_factory=DailySection)
    lottery: LotterySection = Field(default_factory=LotterySection)
    artifacts: ArtifactsSection = Field(default_factory=ArtifactsSection)
    artifact_run: ArtifactRunSection = Field(
        default_factory=ArtifactRunSection, json_schema_extra=READ_ONLY
    )
    retention: RetentionSection = Field(default_factory=RetentionSection)


class SettingsConflict(Exception):
    pass


class SettingsPatchError(ValueError):
    def __init__(self, code: str, path: str) -> None:
        super().__init__(f"{code}: {path}")
        self.code = code
        self.path = path


class ChatIsSelf(Exception):
    """Поля `chats.*` указывают на пользователя Telegram, к которому привязан аккаунт."""

    def __init__(self, fields: list[str]) -> None:
        super().__init__(f"chat_is_self: {', '.join(fields)}")
        self.fields = fields


# Поля `chats.*`, которые не id чата или пользователя: id сообщения не сравнивается с id
# пользователя Telegram.
_NOT_PEERS = frozenset({"tangerine_reply_to"})


def self_chat_fields(settings: Settings, tg_user_id: int) -> list[str]:
    """Пути полей `chats.*` с id чата, равных `tg_user_id`: «Избранное» аккаунта никогда не
    попадает в журнал (раздел 4.3 спеки)."""
    return [
        f"chats.{name}"
        for name, value in settings.chats
        if name not in _NOT_PEERS and value == tg_user_id
    ]


def check_self_chat(settings: Settings, tg_user_id: int | None) -> None:
    """Аккаунт привязан, а поле `chats.*` равно его пользователю Telegram — `ChatIsSelf`."""
    if tg_user_id is None:
        return
    fields = self_chat_fields(settings, tg_user_id)
    if fields:
        raise ChatIsSelf(fields)


# Читаются только при старте движка аккаунта (парсер, фильтр чатов, конвейер).
_RESTART_REQUIRED = ("chats.", "engine.recovered_react_max_age_min")
# Чаты мандаринов и команды не входят ни в фильтр, ни в разбор: планировщик, шлюз и реакция
# пересылки читают их на лету.
_LIVE = frozenset({"chats.tangerine_chat_id", "chats.tangerine_reply_to", "chats.team_chat_id"})


def apply_patch(settings: Settings, changes: Mapping[str, Any]) -> Settings:
    """Частичное изменение: секции сливаются, листья (в том числе словари и списки)
    заменяются целиком; незнакомый или read-only путь — `SettingsPatchError`."""
    return _patched(settings.model_dump(mode="json"), changes)


def _patched(values: Mapping[str, Any], changes: Mapping[str, Any]) -> Settings:
    # Проверяется только итог: так изменение ложится и на значения, которые текущая сборка не
    # принимает (`stored_values`). `values` не меняются.
    data = copy.deepcopy(dict(values))
    _merge(Settings, data, changes, "")
    return Settings.model_validate(data)


def stored_values(data: Mapping[str, Any]) -> dict[str, Any]:
    """Сохранённые настройки поверх умолчаний (JSON) без проверки значений: секции сливаются,
    листья заменяются целиком, незнакомые поля отбрасываются (как при `model_validate`), секция
    не объектом — по умолчанию. Так настройку, которую текущая сборка не принимает, видно в
    форме и её можно исправить прямой записью (раздел 4.4 спеки)."""
    values = Settings().model_dump(mode="json")
    _overlay(Settings, values, data)
    return values


def _overlay(model: type[BaseModel], values: dict[str, Any], data: Mapping[str, Any]) -> None:
    for key, value in data.items():
        field = model.model_fields.get(key)
        if field is None:
            continue
        section = field.annotation
        if isinstance(section, type) and issubclass(section, BaseModel):
            if isinstance(value, Mapping):
                _overlay(section, values[key], value)
        else:
            values[key] = value


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


class SettingsPatch:
    """Частичное изменение настроек (`apply_patch`) как `SettingsChange`. Переход в `live` —
    только с `confirm_live`: из dry_run начинаются реальные траты. `self_id` — пользователь
    Telegram привязанного аккаунта: свой чат в `chats.*` — `ChatIsSelf`. `before` — JSON
    настроек, к которым изменение применено последним."""

    def __init__(
        self,
        changes: Mapping[str, Any],
        *,
        confirm_live: bool = False,
        self_id: int | None = None,
    ) -> None:
        self.changes = changes
        self.confirm_live = confirm_live
        self.self_id = self_id
        self.before: dict[str, Any] | None = None

    def __call__(self, settings: Settings) -> Settings:
        return self.apply(settings.model_dump(mode="json"))

    def apply(self, values: dict[str, Any]) -> Settings:
        """Изменение поверх JSON настроек — и тех, что текущая сборка не принимает
        (`stored_values`): проверяется только итог."""
        new = _patched(values, self.changes)
        was_live = values["engine"]["mode"] == "live"
        if new.engine.mode == "live" and not was_live and not self.confirm_live:
            raise SettingsPatchError("live_requires_confirm", "engine.mode")
        check_self_chat(new, self.self_id)
        self.before = values
        return new


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
    return [p for p in paths if p.startswith(_RESTART_REQUIRED) and p not in _LIVE]


SettingsChange = Callable[[Settings], Settings]


class SettingsProvider(Protocol):
    @property
    def current(self) -> Settings: ...

    @property
    def version(self) -> int: ...

    async def update(
        self, change: SettingsChange, *, changed_by: str, expected_version: int | None = None
    ) -> tuple[Settings, int]:
        """Новые настройки и версия, под которой они сохранены."""
        ...


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
    ) -> tuple[Settings, int]:
        if expected_version is not None and expected_version != self._version:
            raise SettingsConflict(f"version {self._version} != {expected_version}")
        self._settings = Settings.model_validate(change(self._settings).model_dump())
        self._version += 1
        return self._settings, self._version

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from app.engine.settings import Settings


@dataclass(frozen=True)
class Bound:
    path: str
    min: float | None = None
    max: float | None = None


class RetentionPolicy(BaseModel):
    """Сроки хранения данных сервера и аккаунтов (раздел 5.6 спеки)."""

    # Журнал: сообщения (с нераспознанными), действия, запуски сценариев, уведомления.
    messages_days: int = Field(default=90, ge=1, le=3650)
    decisions_days: int = Field(default=30, ge=1, le=3650)
    # Долгая статистика: ряды метрик и забеги метро.
    metrics_days: int = Field(default=365, ge=1, le=3650)
    # Журнал прихода — целыми сутками MSK: сегодня и `ledger_days - 1` суток до него; не меньше 31,
    # чтобы все 30 дней «Итогов» были полными.
    ledger_days: int = Field(default=31, ge=31, le=3650)
    # Журнал действий администраторов и владельцев сервера.
    audit_days: int = Field(default=365, ge=1, le=3650)


class InvitesSection(BaseModel):
    """Параметры приглашений по умолчанию."""

    default_ttl_h: int = Field(default=72, ge=1, le=720)
    default_max_accounts: int = Field(default=1, ge=1, le=1000)


class LimitsSection(BaseModel):
    """Ограничения сервера и лимиты запросов кодов Telegram."""

    max_accounts_total: int = Field(default=50, ge=1, le=10000)
    sse_per_user: int = Field(default=5, ge=1, le=100)
    tg_codes_per_hour: int = Field(default=10, ge=1)
    tg_codes_per_account_hour: int = Field(default=3, ge=1)


class EngineBounds(BaseModel):
    """Границы допустимых настроек движков аккаунтов (раздел 5.6 спеки)."""

    min_request_interval_s_min: float = Field(default=1.6, ge=0, le=60)
    antiflood_pause_s_min: float = Field(default=10.0, ge=0, le=600)
    antiflood_retry_max_max: int = Field(default=2, ge=0)
    action_ttl_s_max: float = Field(default=600.0, gt=0, le=3600)

    def bounds(self) -> list[Bound]:
        return [
            Bound("engine.min_request_interval_s", min=self.min_request_interval_s_min, max=None),
            Bound("engine.antiflood_pause_s", min=self.antiflood_pause_s_min, max=None),
            Bound("engine.antiflood_retry_max", min=None, max=float(self.antiflood_retry_max_max)),
            Bound("engine.action_ttl_s", min=None, max=self.action_ttl_s_max),
        ]

    def violation(
        self, settings: Settings, paths: Iterable[str]
    ) -> tuple[Bound, Literal["min", "max"], float] | None:
        """Первая нарушенная граница среди `paths`."""
        path_set = set(paths)
        for b in self.bounds():
            if b.path not in path_set:
                continue
            if b.path == "engine.min_request_interval_s":
                val = settings.engine.min_request_interval_s
            elif b.path == "engine.antiflood_pause_s":
                val = settings.engine.antiflood_pause_s
            elif b.path == "engine.antiflood_retry_max":
                val = float(settings.engine.antiflood_retry_max)
            elif b.path == "engine.action_ttl_s":
                val = settings.engine.action_ttl_s
            else:
                continue

            if b.min is not None and val < b.min:
                return (b, "min", b.min)
            if b.max is not None and val > b.max:
                return (b, "max", b.max)
        return None

    def clamp(self, settings: Settings) -> Settings:
        """Значения за границами приводятся к ним."""
        eng = settings.engine
        new_interval = max(eng.min_request_interval_s, self.min_request_interval_s_min)
        new_pause = max(eng.antiflood_pause_s, self.antiflood_pause_s_min)
        new_retry = min(eng.antiflood_retry_max, self.antiflood_retry_max_max)
        new_ttl = min(eng.action_ttl_s, self.action_ttl_s_max)

        if (
            new_interval == eng.min_request_interval_s
            and new_pause == eng.antiflood_pause_s
            and new_retry == eng.antiflood_retry_max
            and new_ttl == eng.action_ttl_s
        ):
            return settings

        new_eng = eng.model_copy(
            update={
                "min_request_interval_s": new_interval,
                "antiflood_pause_s": new_pause,
                "antiflood_retry_max": new_retry,
                "action_ttl_s": new_ttl,
            }
        )
        return settings.model_copy(update={"engine": new_eng})


class ServerSettings(BaseModel):
    """Настройки сервера (раздел 5.6 спеки)."""

    retention: RetentionPolicy = Field(default_factory=RetentionPolicy)
    invites: InvitesSection = Field(default_factory=InvitesSection)
    limits: LimitsSection = Field(default_factory=LimitsSection)
    engine_bounds: EngineBounds = Field(default_factory=EngineBounds)

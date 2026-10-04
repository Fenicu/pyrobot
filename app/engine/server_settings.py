from __future__ import annotations

from pydantic import BaseModel, Field


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


class ServerSettings(BaseModel):
    """Настройки сервера (раздел 5.6 спеки)."""

    retention: RetentionPolicy = Field(default_factory=RetentionPolicy)
    invites: InvitesSection = Field(default_factory=InvitesSection)
    limits: LimitsSection = Field(default_factory=LimitsSection)
    engine_bounds: EngineBounds = Field(default_factory=EngineBounds)

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _now_col() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Account(Base):
    """Реестр аккаунтов: владелец, имя, желаемое состояние движка и аренда."""

    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_accounts_owner_name"),
        CheckConstraint(
            "status IN ('enabled', 'disabled', 'error', 'deleting')", name="ck_accounts_status"
        ),
        # Пользователь Telegram привязан не больше чем к одному аккаунту.
        Index(
            "uq_accounts_tg_user_id",
            "tg_user_id",
            unique=True,
            postgresql_where=text("tg_user_id IS NOT NULL"),
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # NULL — аккаунт без владельца: никому не виден, при старте его получает первая учётка.
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(64))
    # Движок крутится только у `enabled`; у `disabled` и `error` причина — в `status_reason`.
    status: Mapped[str] = mapped_column(String(16), server_default="enabled")
    status_reason: Mapped[str | None] = mapped_column(Text)
    # Привязка к пользователю Telegram — на всю жизнь аккаунта.
    tg_user_id: Mapped[int | None] = mapped_column(BigInteger)
    # Желаемое поколение движка: перезапуск его увеличивает, хост сверяет с запущенным.
    engine_generation: Mapped[int] = mapped_column(BigInteger, server_default="0")
    # Аренда: кто держит (NULL — свободна), номер (растёт с каждым захватом), срок по часам базы.
    lease_holder: Mapped[str | None] = mapped_column(Text)
    lease_epoch: Mapped[int] = mapped_column(BigInteger, server_default="0")
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = _now_col()


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('owner', 'user')", name="ck_users_role"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    login: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(8), server_default="user")
    max_accounts: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = _now_col()
    password_changed_at: Mapped[datetime] = _now_col()
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_reason: Mapped[str | None] = mapped_column(Text)
    invited_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    deleting_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    admin_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = _now_col()
    last_seen_at: Mapped[datetime] = _now_col()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(256))


class SettingsRow(Base):
    __tablename__ = "settings"
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    version: Mapped[int] = mapped_column(BigInteger)
    data: Mapped[dict[str, Any]]
    updated_at: Mapped[datetime] = _now_col()


class SettingsHistory(Base):
    __tablename__ = "settings_history"
    __table_args__ = (Index("ix_settings_history_account_id_id", "account_id", "id"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    version: Mapped[int] = mapped_column(BigInteger)
    data: Mapped[dict[str, Any]]
    changed_by: Mapped[str] = mapped_column(String(64))
    changed_at: Mapped[datetime] = _now_col()


class StateSnapshot(Base):
    __tablename__ = "state_snapshot"
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    version: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[dict[str, Any]]
    updated_at: Mapped[datetime] = _now_col()


class MessageRow(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("account_id", "chat_id", "msg_id", "revision", "content_hash"),
        Index("ix_messages_account_received", "account_id", "received_at"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    msg_id: Mapped[int] = mapped_column(BigInteger)
    revision: Mapped[int] = mapped_column(BigInteger)
    content_hash: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(8))
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recovered: Mapped[bool] = mapped_column(Boolean, default=False)
    outgoing: Mapped[bool] = mapped_column(Boolean, default=False)
    from_id: Mapped[int | None] = mapped_column(BigInteger)
    text: Mapped[str | None] = mapped_column(Text)
    markup: Mapped[dict[str, Any] | None]
    events: Mapped[list[Any]]


class ActionRow(Base):
    __tablename__ = "actions"
    __table_args__ = (
        UniqueConstraint("account_id", "idempotency_key"),
        Index("ix_actions_account_created", "account_id", "created_at"),
        Index("ix_actions_scenario_run_id", "scenario_run_id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    created_at: Mapped[datetime] = _now_col()
    source: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(8))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict[str, Any]]
    command_class: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(String(200), default="")
    idempotency_key: Mapped[str | None] = mapped_column(String(100))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    answer: Mapped[str | None] = mapped_column(Text)
    match_detail: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Запуск сценария, шагом которого было действие; ручные команды и реакции — NULL.
    scenario_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("scenario_runs.id", ondelete="SET NULL")
    )


class MetricRow(Base):
    __tablename__ = "metrics"
    __table_args__ = (Index("ix_metrics_account_key_ts", "account_id", "key", "ts"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    key: Mapped[str] = mapped_column(String(32))
    value: Mapped[float] = mapped_column(Float)


class LedgerRow(Base):
    """Журнал прихода: эффект применённого итога сообщения (что редьюсер изменил в ресурсах)."""

    __tablename__ = "ledger"
    __table_args__ = (
        # Ключ ряда сообщения (как у `messages`) и номер эффекта: правки одной секунды с разным
        # содержимым — разные ряды, и эффект каждой пишется.
        UniqueConstraint(
            "account_id",
            "chat_id",
            "msg_id",
            "revision",
            "content_hash",
            "kind",
            "seq",
            name="uq_ledger_effect",
        ),
        Index("ix_ledger_account_day", "account_id", "day"),
        # Итог, который приходит разными сообщениями (отчёты фабрики и битвы), — один ряд.
        Index(
            "uq_ledger_outcome_key",
            "account_id",
            "outcome_key",
            unique=True,
            postgresql_where=text("outcome_key IS NOT NULL"),
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    # Время исхода (ревизии, в которой итог применён; у отчётов битвы и фабрики — самой битвы) и
    # его дата по Москве.
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    day: Mapped[date] = mapped_column(Date)
    # Когда журнал узнал об эффекте (получение сообщения): с него считается запуск журнала — отчёт
    # о прошлой битве датирован битвой, но дни до записи журнал не видел.
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(String(32))
    amounts: Mapped[dict[str, Any]]
    items: Mapped[dict[str, Any]]
    chat_id: Mapped[int] = mapped_column(BigInteger)
    msg_id: Mapped[int] = mapped_column(BigInteger)
    revision: Mapped[int] = mapped_column(BigInteger)
    content_hash: Mapped[str] = mapped_column(String(40))
    seq: Mapped[int] = mapped_column(Integer)
    # Постоянный ключ итога (`factory:<день битвы>`, `battle:<отпечаток отчёта>`), у прочих NULL.
    outcome_key: Mapped[str | None] = mapped_column(String(64))


class UnrecognizedRow(Base):
    __tablename__ = "unrecognized"
    __table_args__ = (
        Index("ix_unrecognized_account_acked_created", "account_id", "acked", "created_at"),
        Index("ix_unrecognized_message_id", "message_id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    msg_id: Mapped[int] = mapped_column(BigInteger)
    first_line: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _now_col()
    acked: Mapped[bool] = mapped_column(Boolean, default=False)


class NotificationRow(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_account_id_id", "account_id", "id"),
        Index("ix_notifications_server", "id", postgresql_where=text("account_id IS NULL")),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    created_at: Mapped[datetime] = _now_col()
    level: Mapped[str] = mapped_column(String(8))
    code: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    read: Mapped[bool] = mapped_column(Boolean, default=False)


class AuditRow(Base):
    """Журнал действий администраторов и владельцев сервера (раздел 5.6 спеки)."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_at", "at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[datetime] = _now_col()
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_login: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    target_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))


class DecisionRow(Base):
    __tablename__ = "decisions"
    __table_args__ = (Index("ix_decisions_account_at", "account_id", "at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(String(8))
    scenario: Mapped[str | None] = mapped_column(String(32))
    params: Mapped[dict[str, Any]]
    reason: Mapped[str] = mapped_column(String(200))
    until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    candidates: Mapped[list[Any]]


class ScenarioRunRow(Base):
    __tablename__ = "scenario_runs"
    __table_args__ = (
        Index("ix_scenario_runs_account_started", "account_id", "started_at"),
        Index("ix_scenario_runs_decision_id", "decision_id"),
        UniqueConstraint("account_id", "idempotency_key", name="uq_scenario_runs_account_key"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    decision_id: Mapped[int | None] = mapped_column(
        ForeignKey("decisions.id", ondelete="SET NULL")
    )
    scenario: Mapped[str] = mapped_column(String(32))
    params: Mapped[dict[str, Any]]
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(12))
    reason: Mapped[str] = mapped_column(String(200), default="")
    # Ручной запуск из админки: кто запросил и ключ идемпотентности; у плановых — NULL.
    requested_by: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str | None] = mapped_column(String(100))
    # Параметры, присланные клиентом (до слияния с реестром): по ним сверяется повтор ключа.
    requested_params: Mapped[dict[str, Any] | None]


class MetroRunRow(Base):
    __tablename__ = "metro_runs"
    __table_args__ = (
        Index("ix_metro_runs_account_started", "account_id", "started_at"),
        Index("ix_metro_runs_scenario_run_id", "scenario_run_id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    scenario_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("scenario_runs.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(12))
    outcome: Mapped[str] = mapped_column(String(64))
    steps: Mapped[int] = mapped_column(Integer)
    duration_s: Mapped[float] = mapped_column(Float)
    step_s: Mapped[float | None] = mapped_column(Float)
    buffs: Mapped[list[Any]]
    result: Mapped[dict[str, Any] | None]
    grid: Mapped[dict[str, Any]]
    path: Mapped[list[Any]]
    events: Mapped[list[Any]]
    vitals: Mapped[list[Any]]
    summary: Mapped[dict[str, Any]]


class TgSession(Base):
    """Сессия Telegram аккаунта (хранилище kurigram); `auth_key` — шифротекст `SecretBox`."""

    __tablename__ = "tg_sessions"
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    # Поля как в `sessions` SQLite-хранилища kurigram: часть пуста, пока клиент не вошёл.
    dc_id: Mapped[int] = mapped_column(Integer)
    api_id: Mapped[int | None] = mapped_column(Integer)
    test_mode: Mapped[bool | None] = mapped_column(Boolean)
    auth_key: Mapped[bytes | None] = mapped_column(LargeBinary)
    date: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    is_bot: Mapped[bool | None] = mapped_column(Boolean)
    server_address: Mapped[str | None] = mapped_column(Text)
    port: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = _now_col()


class TgPeer(Base):
    """Пиры чатов из настроек аккаунта и пользователя `swinfo_user_id`: доступны вне первых 200
    диалогов, когда kurigram ещё не прогрел свой кэш."""

    __tablename__ = "tg_peers"
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    access_hash: Mapped[int | None] = mapped_column(BigInteger)
    type: Mapped[str] = mapped_column(String(16))
    username: Mapped[str | None] = mapped_column(Text)
    phone_number: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = _now_col()


class TgChatMark(Base):
    """Отметка сверки истории: до какого `msg_id` включительно чтение (чат, отправитель) уже в
    журнале. `from_id` = 0 — чтение всего чата. Принадлежит журналу: выход из Telegram и сброс
    ключа её не трогают."""

    __tablename__ = "tg_chat_marks"
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    from_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    msg_id: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = _now_col()


class ServerMeta(Base):
    """Служебные записи сервера; сейчас одна — `key_check`, проверка ключа шифрования."""

    __tablename__ = "server_meta"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[bytes] = mapped_column(LargeBinary)

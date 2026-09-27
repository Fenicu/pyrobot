from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _now_col() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_user_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = _now_col()


class AdminUser(Base):
    __tablename__ = "admin_users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    login: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = _now_col()
    password_changed_at: Mapped[datetime] = _now_col()


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    admin_user_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"))
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
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    created_at: Mapped[datetime] = _now_col()
    level: Mapped[str] = mapped_column(String(8))
    code: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    read: Mapped[bool] = mapped_column(Boolean, default=False)


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

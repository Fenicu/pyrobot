"""accounts registry: владелец, имя, статус, поколение движка, аренда; привязка Telegram

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-30 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("owner_id", sa.Integer(), nullable=True))
    # Имя обязательно; пока строки не заполнены, колонка допускает NULL.
    op.add_column("accounts", sa.Column("name", sa.String(length=64), nullable=True))
    op.add_column(
        "accounts",
        sa.Column("status", sa.String(length=16), server_default="enabled", nullable=False),
    )
    op.add_column("accounts", sa.Column("status_reason", sa.Text(), nullable=True))
    op.add_column(
        "accounts",
        sa.Column("engine_generation", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.add_column("accounts", sa.Column("lease_holder", sa.Text(), nullable=True))
    op.add_column(
        "accounts", sa.Column("lease_epoch", sa.BigInteger(), server_default="0", nullable=False)
    )
    op.add_column(
        "accounts", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "accounts",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    # Нынешние аккаунты достаются первой учётке (NULL, если учёток нет: их получит первая при
    # старте), аккаунт с наименьшим id — «Основной».
    op.execute("UPDATE accounts SET owner_id = (SELECT min(id) FROM admin_users)")
    op.execute(
        "UPDATE accounts SET name = CASE WHEN id = (SELECT min(id) FROM accounts)"
        " THEN 'Основной' ELSE 'Аккаунт ' || id END"
    )
    op.alter_column("accounts", "name", nullable=False)
    # Привязка к пользователю Telegram переезжает из настроек в аккаунт.
    op.execute(
        "UPDATE accounts SET tg_user_id = (s.data->'telegram'->>'expected_user_id')::bigint"
        " FROM settings s WHERE s.account_id = accounts.id"
        " AND s.data->'telegram'->>'expected_user_id' IS NOT NULL"
    )
    op.execute("UPDATE settings SET data = data - 'telegram'")
    op.create_foreign_key(
        "accounts_owner_id_fkey",
        "accounts",
        "admin_users",
        ["owner_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint("uq_accounts_owner_name", "accounts", ["owner_id", "name"])
    op.create_check_constraint(
        "ck_accounts_status", "accounts", "status IN ('enabled', 'disabled', 'error', 'deleting')"
    )
    op.create_index(
        "uq_accounts_tg_user_id",
        "accounts",
        ["tg_user_id"],
        unique=True,
        postgresql_where=sa.text("tg_user_id IS NOT NULL"),
    )
    op.create_index(
        "ix_notifications_account_id_id", "notifications", ["account_id", "id"], unique=False
    )
    op.create_index(
        "ix_settings_history_account_id_id", "settings_history", ["account_id", "id"], unique=False
    )
    # Миграция 0001 вставила аккаунт с явным id, счётчик не сдвинут: первый `POST /accounts`
    # упал бы на дубле ключа.
    op.execute("SELECT setval('accounts_id_seq', (SELECT max(id) FROM accounts))")


def downgrade() -> None:
    op.execute(
        "UPDATE settings SET data = jsonb_set("
        "data, '{telegram}', jsonb_build_object('expected_user_id', a.tg_user_id))"
        " FROM accounts a WHERE a.id = settings.account_id AND a.tg_user_id IS NOT NULL"
    )
    op.drop_index("ix_settings_history_account_id_id", table_name="settings_history")
    op.drop_index("ix_notifications_account_id_id", table_name="notifications")
    op.drop_index("uq_accounts_tg_user_id", table_name="accounts")
    # Внешний ключ и ограничения `accounts` уходят вместе со своими колонками; `tg_user_id`
    # была и до 0011 — остаётся.
    for column in (
        "updated_at",
        "lease_expires_at",
        "lease_epoch",
        "lease_holder",
        "engine_generation",
        "status_reason",
        "status",
        "name",
        "owner_id",
    ):
        op.drop_column("accounts", column)

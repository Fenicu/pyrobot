"""сессии Telegram в базе: tg_sessions, tg_peers, tg_chat_marks и server_meta (проверка ключа)

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-30 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _updated_at() -> sa.Column[sa.DateTime]:
    return sa.Column(
        "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    # Внешние ключи без каскада: аккаунт удаляет фоновая чистка, а ключ страхует от удаления
    # строки `accounts` с оставшимися данными.
    op.create_table(
        "tg_sessions",
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("dc_id", sa.Integer(), nullable=False),
        sa.Column("api_id", sa.Integer(), nullable=True),
        sa.Column("test_mode", sa.Boolean(), nullable=True),
        sa.Column("auth_key", sa.LargeBinary(), nullable=True),
        sa.Column("date", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("is_bot", sa.Boolean(), nullable=True),
        sa.Column("server_address", sa.Text(), nullable=True),
        sa.Column("port", sa.Integer(), nullable=True),
        _updated_at(),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("account_id"),
    )
    op.create_table(
        "tg_peers",
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("access_hash", sa.BigInteger(), nullable=True),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("username", sa.Text(), nullable=True),
        sa.Column("phone_number", sa.Text(), nullable=True),
        _updated_at(),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("account_id", "id"),
    )
    op.create_table(
        "tg_chat_marks",
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("from_id", sa.BigInteger(), nullable=False),
        sa.Column("msg_id", sa.BigInteger(), nullable=False),
        _updated_at(),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("account_id", "chat_id", "from_id"),
    )
    op.create_table(
        "server_meta",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.LargeBinary(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("server_meta")
    op.drop_table("tg_chat_marks")
    op.drop_table("tg_peers")
    op.drop_table("tg_sessions")

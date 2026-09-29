"""ledger: журнал прихода

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-28 20:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ledger",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("amounts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("items", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("msg_id", sa.BigInteger(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(length=40), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id",
            "chat_id",
            "msg_id",
            "revision",
            "content_hash",
            "kind",
            "seq",
            name="uq_ledger_effect",
        ),
    )
    op.create_index("ix_ledger_account_day", "ledger", ["account_id", "day"], unique=False)


def downgrade() -> None:
    # Индексы и ограничения уходят вместе с таблицей.
    op.drop_table("ledger")

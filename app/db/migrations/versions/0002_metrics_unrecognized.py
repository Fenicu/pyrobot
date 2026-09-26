"""metrics and unrecognized

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-26 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "metrics",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_metrics_account_key_ts", "metrics", ["account_id", "key", "ts"], unique=False
    )
    op.create_table(
        "unrecognized",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("msg_id", sa.BigInteger(), nullable=False),
        sa.Column("first_line", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("acked", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_unrecognized_account_acked_created",
        "unrecognized",
        ["account_id", "acked", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_unrecognized_account_acked_created", table_name="unrecognized")
    op.drop_table("unrecognized")
    op.drop_index("ix_metrics_account_key_ts", table_name="metrics")
    op.drop_table("metrics")

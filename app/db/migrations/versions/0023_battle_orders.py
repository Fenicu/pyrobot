"""Приказы штаба битвы (battle_orders) и индексы журнала под ленту обзора.

Revision ID: 0023
Revises: 0022
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "battle_orders",
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("battle_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scope", sa.String(length=8), nullable=False),
        sa.Column("target", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "scope IN ('all', 'piper', 'hooli', 'stark', 'umbrl', 'wayne', 'bmesa')",
            name="ck_battle_orders_scope",
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("owner_id", "battle_at", "scope"),
    )
    op.create_index(
        "ix_ledger_account_at", "ledger", ["account_id", sa.text("at DESC"), sa.text("id DESC")]
    )
    op.create_index("ix_ledger_account_recorded", "ledger", ["account_id", "recorded_at"])


def downgrade() -> None:
    op.drop_index("ix_ledger_account_recorded", table_name="ledger")
    op.drop_index("ix_ledger_account_at", table_name="ledger")
    op.drop_table("battle_orders")

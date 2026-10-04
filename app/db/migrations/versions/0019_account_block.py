"""Блокировка аккаунта владельцем (blocked, blocked_reason)

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-04 17:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column(
            "blocked",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "accounts",
        sa.Column("blocked_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("accounts", "blocked_reason")
    op.drop_column("accounts", "blocked")

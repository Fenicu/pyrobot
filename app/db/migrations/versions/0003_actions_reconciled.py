"""actions reconciled_at

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-26 13:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("actions", sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("actions", "reconciled_at")

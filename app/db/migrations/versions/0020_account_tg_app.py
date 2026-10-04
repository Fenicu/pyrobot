"""Своё приложение Telegram у аккаунта.

Revision ID: 0020
Revises: 0019
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("tg_api_id", sa.Integer(), nullable=True))
    op.add_column("accounts", sa.Column("tg_api_hash", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    op.drop_column("accounts", "tg_api_hash")
    op.drop_column("accounts", "tg_api_id")

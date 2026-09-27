"""manual scenario runs

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-27 04:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("scenario_runs", sa.Column("requested_by", sa.String(length=64), nullable=True))
    op.add_column(
        "scenario_runs", sa.Column("idempotency_key", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "scenario_runs",
        sa.Column("requested_params", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_unique_constraint(
        "uq_scenario_runs_account_key", "scenario_runs", ["account_id", "idempotency_key"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_scenario_runs_account_key", "scenario_runs", type_="unique")
    op.drop_column("scenario_runs", "requested_params")
    op.drop_column("scenario_runs", "idempotency_key")
    op.drop_column("scenario_runs", "requested_by")

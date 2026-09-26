"""metro runs

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-26 21:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, postgresql.JSONB(astext_type=sa.Text()), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "metro_runs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("scenario_run_id", sa.BigInteger(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("outcome", sa.String(length=64), nullable=False),
        sa.Column("steps", sa.Integer(), nullable=False),
        sa.Column("duration_s", sa.Float(), nullable=False),
        sa.Column("step_s", sa.Float(), nullable=True),
        _json("buffs"),
        _json("result", nullable=True),
        _json("grid"),
        _json("path"),
        _json("events"),
        _json("vitals"),
        _json("summary"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["scenario_run_id"], ["scenario_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_metro_runs_account_started", "metro_runs", ["account_id", "started_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_metro_runs_account_started", table_name="metro_runs")
    op.drop_table("metro_runs")

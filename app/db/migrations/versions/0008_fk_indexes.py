"""foreign key indexes

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-27 08:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_unrecognized_message_id", "unrecognized", ["message_id"], unique=False)
    op.create_index("ix_scenario_runs_decision_id", "scenario_runs", ["decision_id"], unique=False)
    op.create_index(
        "ix_metro_runs_scenario_run_id", "metro_runs", ["scenario_run_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_metro_runs_scenario_run_id", table_name="metro_runs")
    op.drop_index("ix_scenario_runs_decision_id", table_name="scenario_runs")
    op.drop_index("ix_unrecognized_message_id", table_name="unrecognized")

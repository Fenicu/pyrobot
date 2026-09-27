"""actions.scenario_run_id

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-27 23:40:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Прежние шаги сценариев: запуски исполняет один PlannerLoop строго по очереди, поэтому действие,
# созданное внутри окна [started_at, finished_at] ровно одного завершённого запуска, — его шаг.
# Плановый запуск шлёт от имени scenario, ручной — от manual без ключа идемпотентности; ручные
# команды (ключ manual:…, запреты forbidden/donate без ключа) и реакции (urgent) не трогаются.
BACKFILL = """
UPDATE actions AS a SET scenario_run_id = m.run_id
FROM (
    SELECT s.id AS action_id, min(r.id) AS run_id
    FROM actions AS s
    JOIN scenario_runs AS r
      ON r.account_id = s.account_id
     AND r.finished_at IS NOT NULL
     AND s.created_at BETWEEN r.started_at AND r.finished_at
     AND (
        (s.source = 'scenario' AND r.requested_by IS NULL)
        OR (
            s.source = 'manual'
            AND r.requested_by IS NOT NULL
            AND s.idempotency_key IS NULL
            AND s.command_class NOT IN ('forbidden', 'donate')
        )
     )
    WHERE s.scenario_run_id IS NULL
    GROUP BY s.id
    HAVING count(*) = 1
) AS m
WHERE a.id = m.action_id
"""


def upgrade() -> None:
    op.add_column("actions", sa.Column("scenario_run_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "actions_scenario_run_id_fkey",
        "actions",
        "scenario_runs",
        ["scenario_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_actions_scenario_run_id", "actions", ["scenario_run_id"], unique=False)
    op.execute(BACKFILL)


def downgrade() -> None:
    op.drop_index("ix_actions_scenario_run_id", table_name="actions")
    op.drop_constraint("actions_scenario_run_id_fkey", "actions", type_="foreignkey")
    op.drop_column("actions", "scenario_run_id")

"""audit_log и уведомления сервера (account_id NULL)

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-04 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column(
            "at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("actor_login", sa.String(64), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(16), nullable=True),
        sa.Column("target_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_index("ix_audit_log_at", "audit_log", ["at"])

    op.alter_column("notifications", "account_id", existing_type=sa.Integer(), nullable=True)
    op.create_index(
        "ix_notifications_server",
        "notifications",
        ["id"],
        postgresql_where=sa.text("account_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_server", table_name="notifications")
    op.execute("DELETE FROM notifications WHERE account_id IS NULL")
    op.alter_column("notifications", "account_id", existing_type=sa.Integer(), nullable=False)
    op.drop_index("ix_audit_log_at", table_name="audit_log")
    op.drop_table("audit_log")

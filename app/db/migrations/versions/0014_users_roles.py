"""учётки с ролями: admin_users -> users, owner и user, лимит аккаунтов, отключение

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-04 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.rename_table("admin_users", "users")
    op.add_column(
        "users",
        sa.Column(
            "role",
            sa.String(8),
            sa.CheckConstraint("role IN ('owner', 'user')", name="ck_users_role"),
            nullable=False,
            server_default="user",
        ),
    )
    op.add_column(
        "users",
        sa.Column("max_accounts", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "users",
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("disabled_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "invited_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("deleting_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE users SET role = 'owner', "
        "max_accounts = GREATEST("
        "10, (SELECT count(*) FROM accounts a WHERE a.owner_id = users.id))"
    )
    op.execute("ALTER SEQUENCE admin_users_id_seq RENAME TO users_id_seq")
    op.execute("ALTER TABLE users RENAME CONSTRAINT admin_users_pkey TO users_pkey")
    op.execute("ALTER TABLE users RENAME CONSTRAINT admin_users_login_key TO users_login_key")


def downgrade() -> None:
    op.execute("ALTER TABLE users RENAME CONSTRAINT users_login_key TO admin_users_login_key")
    op.execute("ALTER TABLE users RENAME CONSTRAINT users_pkey TO admin_users_pkey")
    op.execute("ALTER SEQUENCE users_id_seq RENAME TO admin_users_id_seq")
    op.drop_column("users", "deleting_at")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "invited_by")
    op.drop_column("users", "disabled_reason")
    op.drop_column("users", "disabled_at")
    op.drop_column("users", "max_accounts")
    op.drop_column("users", "role")
    op.rename_table("users", "admin_users")

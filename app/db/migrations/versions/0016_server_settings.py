"""server_settings и перенос retention из настроек аккаунтов

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-04 14:00:00
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_SERVER_SETTINGS = {
    "retention": {
        "messages_days": 90,
        "decisions_days": 30,
        "metrics_days": 365,
        "ledger_days": 31,
        "audit_days": 365,
    },
    "invites": {
        "default_ttl_h": 72,
        "default_max_accounts": 1,
    },
    "limits": {
        "max_accounts_total": 50,
        "sse_per_user": 5,
        "tg_codes_per_hour": 10,
        "tg_codes_per_account_hour": 3,
    },
    "engine_bounds": {
        "min_request_interval_s_min": 1.6,
        "antiflood_pause_s_min": 10.0,
        "antiflood_retry_max_max": 2,
        "action_ttl_s_max": 600.0,
    },
}


def upgrade() -> None:
    table = op.create_table(
        "server_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name="ck_server_settings_single_row"),
    )

    bind = op.get_bind()
    row = bind.execute(
        sa.text("SELECT data->'retention' FROM settings WHERE account_id = 1")
    ).scalar()

    server_data = {
        "retention": dict(_DEFAULT_SERVER_SETTINGS["retention"]),
        "invites": dict(_DEFAULT_SERVER_SETTINGS["invites"]),
        "limits": dict(_DEFAULT_SERVER_SETTINGS["limits"]),
        "engine_bounds": dict(_DEFAULT_SERVER_SETTINGS["engine_bounds"]),
    }
    if row is not None:
        retention_dict = row if isinstance(row, dict) else json.loads(row)
        if isinstance(retention_dict, dict):
            server_data["retention"].update(retention_dict)
            server_data["retention"]["audit_days"] = 365

    op.bulk_insert(
        table,
        [
            {
                "id": 1,
                "version": 1,
                "data": server_data,
            }
        ],
    )
    op.execute(sa.text("UPDATE settings SET data = data - 'retention'"))


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE settings SET data = jsonb_set("
            "data, '{retention}', "
            "(SELECT (data->'retention') - 'audit_days' FROM server_settings WHERE id = 1)"
            ")"
        )
    )
    op.drop_table("server_settings")

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


def _max_retention(bind: sa.Connection) -> dict[str, int]:
    """По каждому полю — максимум по всем аккаунтам: обновление не сокращает ничей срок хранения.

    У аккаунта без строки settings или без поля действует умолчание.
    """
    defaults = _DEFAULT_SERVER_SETTINGS["retention"]
    found: dict[str, list[int]] = {key: [] for key in defaults if key != "audit_days"}
    rows = bind.execute(
        sa.text(
            "SELECT s.data->'retention' FROM accounts a "
            "LEFT JOIN settings s ON s.account_id = a.id"
        )
    ).scalars()
    for raw in rows:
        retention = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(retention, dict):
            retention = {}
        for key, values in found.items():
            value = retention.get(key)
            ok = isinstance(value, int) and not isinstance(value, bool)
            values.append(value if ok else defaults[key])
    result = dict(defaults)
    for key, values in found.items():
        if values:
            result[key] = max(values)
    return result


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

    server_data = {
        "retention": _max_retention(op.get_bind()),
        "invites": dict(_DEFAULT_SERVER_SETTINGS["invites"]),
        "limits": dict(_DEFAULT_SERVER_SETTINGS["limits"]),
        "engine_bounds": dict(_DEFAULT_SERVER_SETTINGS["engine_bounds"]),
    }

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

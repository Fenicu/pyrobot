"""Id отправленного сообщения у действия (actions.sent_msg_id).

Revision ID: 0022
Revises: 0021
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("actions", sa.Column("sent_msg_id", sa.BigInteger(), nullable=True))
    op.create_index(
        "ix_actions_account_chat_sent", "actions", ["account_id", "chat_id", "sent_msg_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_actions_account_chat_sent", table_name="actions")
    op.drop_column("actions", "sent_msg_id")

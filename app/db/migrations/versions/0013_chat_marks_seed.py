"""отметки сверки истории аккаунта 1 по журналу: сверка вернёт сообщения, пришедшие за время
обновления

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-01 12:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Значения `ChatsSection` по умолчанию на момент миграции: чат игры, канал смузи, чат swinfo и
# пользователь swinfo — для настроек, в которых их нет.
GAME_CHAT_ID = 227859379
SMOOTHIE_CHANNEL_ID = -1001356300612
SWINFO_CHAT_ID = -1001109615116
SWINFO_USER_ID = 376592453


def upgrade() -> None:
    # Отметка — наибольший `msg_id` чтения в журнале: чаты игры и смузи журнал берёт целиком,
    # из чата swinfo — все сообщения swinfo, поэтому отметка по журналу точна. Чату приглашений
    # к биржевикам отметки нет (журнал берёт из него не всё): её поставит первый проход.
    op.execute(
        f"""
        WITH chats AS (
            SELECT coalesce(
                (SELECT data->'chats' FROM settings WHERE account_id = 1), '{{}}'::jsonb
            ) AS c
        ),
        readers AS (
            SELECT coalesce((c->>'game_chat_id')::bigint, {GAME_CHAT_ID}) AS chat_id,
                   0::bigint AS from_id
            FROM chats
            UNION
            SELECT coalesce((c->>'smoothie_channel_id')::bigint, {SMOOTHIE_CHANNEL_ID}), 0
            FROM chats
            UNION
            SELECT coalesce((c->>'swinfo_chat_id')::bigint, {SWINFO_CHAT_ID}),
                   coalesce((c->>'swinfo_user_id')::bigint, {SWINFO_USER_ID})
            FROM chats
        )
        INSERT INTO tg_chat_marks (account_id, chat_id, from_id, msg_id)
        SELECT 1, r.chat_id, r.from_id, max(m.msg_id)
        FROM readers r
        JOIN messages m ON m.account_id = 1 AND m.chat_id = r.chat_id
            AND (r.from_id = 0 OR m.from_id = r.from_id)
        GROUP BY r.chat_id, r.from_id
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    # Отметки — данные журнала: откат их не трогает, таблицу удаляет откат 0012.
    pass

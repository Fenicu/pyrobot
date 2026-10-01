import time

import pytest
from sqlalchemy import insert, select

from app.db.base import Database
from app.db.chat_marks import ChatMarks
from app.db.models import Account, TgChatMark
from app.engine.fence import Fence, LeaseLost
from app.engine.settings import ChatsSection
from app.engine.transport.history import readers_for

pytestmark = pytest.mark.db
GAME = 227859379
SMOOTHIE = -1001356300612
SWINFO = -1001109615116
SW_USER = 376592453


def _fence(epoch: int, *, ttl: float = 60.0) -> Fence:
    return Fence(1, epoch, time.monotonic() + ttl)


async def _rows(db: Database) -> set[tuple[int, int, int, int]]:
    async with db.sessions() as session:
        rows = await session.execute(
            select(
                TgChatMark.account_id, TgChatMark.chat_id, TgChatMark.from_id, TgChatMark.msg_id
            )
        )
        return {(a, c, f, m) for a, c, f, m in rows}


async def test_marks_only_grow(clean_db: Database) -> None:
    marks = ChatMarks(clean_db, 1, _fence(0))
    assert await marks.get((GAME, 0)) is None
    await marks.advance((GAME, 0), 10)
    await marks.advance((GAME, 0), 12)
    assert await marks.get((GAME, 0)) == 12
    # Опоздавшая запись прежнего держателя аренды (его эпоха в базе уже не своя): запись отметки
    # не ограждена строкой аккаунта, но отметку не откатывает.
    stale = ChatMarks(clean_db, 1, _fence(7))
    await stale.advance((GAME, 0), 11)
    assert await marks.get((GAME, 0)) == 12
    # После местного срока аренды отметка не пишется вовсе.
    expired = ChatMarks(clean_db, 1, _fence(0, ttl=-1.0))
    with pytest.raises(LeaseLost):
        await expired.advance((GAME, 0), 20)
    assert await marks.get((GAME, 0)) == 12
    # Отправитель — часть чтения: отметка swinfo своя.
    await marks.advance((SWINFO, SW_USER), 5)
    assert await marks.get((SWINFO, SW_USER)) == 5 and await marks.get((SWINFO, 0)) is None


async def test_prune_removes_readers_not_in_settings(clean_db: Database) -> None:
    async with clean_db.sessions() as session, session.begin():
        other = await session.scalar(insert(Account).values(name="Второй").returning(Account.id))
    assert other is not None
    marks = ChatMarks(clean_db, 1, _fence(0))
    for reader, msg_id in (
        ((GAME, 0), 10),
        ((SMOOTHIE, 0), 20),
        ((SWINFO, SW_USER), 30),
        ((SWINFO, 0), 40),
    ):
        await marks.advance(reader, msg_id)
    await ChatMarks(clean_db, other).advance((SWINFO, 0), 50)
    # swinfo — другой отправитель, чата приглашений больше нет.
    chats = ChatsSection(swinfo_user_id=555)
    assert readers_for(chats) == {(GAME, 0), (SMOOTHIE, 0), (SWINFO, 555)}
    assert await marks.prune(readers_for(chats)) == 2
    assert await _rows(clean_db) == {
        (1, GAME, 0, 10),
        (1, SMOOTHIE, 0, 20),
        (other, SWINFO, 0, 50),
    }
    # Удаление — пишущая транзакция аккаунта: ограждена его строкой.
    with pytest.raises(LeaseLost):
        await ChatMarks(clean_db, 1, _fence(7)).prune(set())
    assert len(await _rows(clean_db)) == 3

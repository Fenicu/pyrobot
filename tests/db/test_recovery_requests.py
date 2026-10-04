import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, update

from app.db.base import Database
from app.db.models import RecoveryRequestRow, User
from app.db.recovery import RecoveryRequests

pytestmark = pytest.mark.db


async def _create_user(db: Database, login: str = "testuser") -> int:
    async with db.sessions() as session, session.begin():
        u = User(login=login, password_hash="dummy_hash", role="user", max_accounts=1)
        session.add(u)
        await session.flush()
        return u.id


async def test_new_start_replaces_code(clean_db: Database) -> None:
    user_id = await _create_user(clean_db, "user1")
    reqs = RecoveryRequests(clean_db, os.urandom(32))

    code1 = await reqs.start(user_id)
    assert len(code1) == 8 and code1.isdigit()

    code2 = await reqs.start(user_id)
    assert len(code2) == 8 and code2.isdigit()

    # Старый код больше не действует
    assert not await reqs.check(user_id, code1)
    # Новый код действует
    assert await reqs.check(user_id, code2)
    # Использованный код удалён
    assert not await reqs.check(user_id, code2)


async def test_five_wrong_attempts_delete_request(clean_db: Database) -> None:
    user_id = await _create_user(clean_db, "user2")
    reqs = RecoveryRequests(clean_db, os.urandom(32))

    code = await reqs.start(user_id)
    wrong = "00000000" if code != "00000000" else "11111111"

    # 4 неверных попытки
    for _ in range(4):
        assert not await reqs.check(user_id, wrong)

    async with clean_db.sessions() as session:
        row = await session.scalar(
            select(RecoveryRequestRow).where(RecoveryRequestRow.user_id == user_id)
        )
        assert row is not None and row.attempts == 4

    # 5-я попытка удаляет запрос
    assert not await reqs.check(user_id, wrong)

    async with clean_db.sessions() as session:
        row = await session.scalar(
            select(RecoveryRequestRow).where(RecoveryRequestRow.user_id == user_id)
        )
        assert row is None

    # Правильный код теперь тоже не действует, так как запрос удалён
    assert not await reqs.check(user_id, code)


async def test_expired_request_deleted(clean_db: Database) -> None:
    user_id = await _create_user(clean_db, "user3")
    reqs = RecoveryRequests(clean_db, os.urandom(32))

    code = await reqs.start(user_id)

    # Искусственно делаем запрос просроченным
    async with clean_db.sessions() as session, session.begin():
        await session.execute(
            update(RecoveryRequestRow)
            .where(RecoveryRequestRow.user_id == user_id)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )

    # check удаляет просроченный запрос и возвращает False
    assert not await reqs.check(user_id, code)

    async with clean_db.sessions() as session:
        row = await session.scalar(
            select(RecoveryRequestRow).where(RecoveryRequestRow.user_id == user_id)
        )
        assert row is None


async def test_code_not_valid_for_other_user(clean_db: Database) -> None:
    user1_id = await _create_user(clean_db, "user4")
    user2_id = await _create_user(clean_db, "user5")
    reqs = RecoveryRequests(clean_db, os.urandom(32))

    code1 = await reqs.start(user1_id)

    # Код пользователя 1 не подходит пользователю 2
    assert not await reqs.check(user2_id, code1)
    # И код пользователя 1 остался активным для пользователя 1
    assert await reqs.check(user1_id, code1)


async def test_user_delete_cascades_request(clean_db: Database) -> None:
    user_id = await _create_user(clean_db, "user6")
    reqs = RecoveryRequests(clean_db, os.urandom(32))

    code = await reqs.start(user_id)

    # Удаляем пользователя
    async with clean_db.sessions() as session, session.begin():
        await session.execute(delete(User).where(User.id == user_id))

    # Запрос удалился каскадно
    async with clean_db.sessions() as session:
        row = await session.scalar(
            select(RecoveryRequestRow).where(RecoveryRequestRow.user_id == user_id)
        )
        assert row is None

    assert not await reqs.check(user_id, code)

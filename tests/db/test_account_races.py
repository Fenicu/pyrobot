"""Гонки создания, включения и чистки аккаунтов с отключением и удалением учётки — на настоящих
соединениях. У каждого соединения `lock_timeout`, и каждый тест ограничен по времени: взаимная
блокировка или вечное ожидание падают за секунды, а не вешают прогон."""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable
from typing import Any

import pytest
from sqlalchemy import event, func, select, text

from app.db.accounts import _ENABLED_LOCK_KEY, AccountRepo, LimitReached, UserInactive
from app.db.base import Database
from app.db.models import Account, User
from app.db.users import UserRepo
from tests.conftest import TEST_DB_URL

pytestmark = pytest.mark.db

_TIMEOUT_S = 15.0


@pytest.fixture
async def rdb(clean_db: Database) -> AsyncIterator[Database]:
    database = Database(TEST_DB_URL)

    @event.listens_for(database.engine.sync_engine, "connect")
    def _lock_timeout(dbapi_conn: Any, _record: Any) -> None:
        dbapi_conn.run_async(lambda conn: conn.execute("SET lock_timeout = '3s'"))

    yield database
    await database.dispose()


async def _bounded[T](aw: Awaitable[T]) -> T:
    return await asyncio.wait_for(aw, _TIMEOUT_S)


async def _user(db: Database, login: str, *, max_accounts: int = 10) -> int:
    async with db.sessions() as session, session.begin():
        user = User(login=login, password_hash="x", role="user", max_accounts=max_accounts)
        session.add(user)
        await session.flush()
        return user.id


async def _lock_waiters(db: Database, n: int, *, timeout_s: float = 5.0) -> None:
    """Ждёт, пока на блокировках базы ждут не меньше `n` соединений."""
    deadline = time.monotonic() + timeout_s
    while True:
        async with db.sessions() as session:
            waiting = await session.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            )
        if (waiting or 0) >= n:
            return
        if time.monotonic() > deadline:
            raise AssertionError(f"expected {n} lock waiters, got {waiting}")
        await asyncio.sleep(0.01)


async def _statuses(db: Database, owner_id: int) -> list[tuple[str, str | None]]:
    async with db.sessions() as session:
        rows = await session.execute(
            select(Account.status, Account.status_reason)
            .where(Account.owner_id == owner_id)
            .order_by(Account.id)
        )
        return [(s, r) for s, r in rows]


async def _hold_enabled_lock(session: Any) -> None:
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _ENABLED_LOCK_KEY})


async def test_create_rejected_for_disabled_user(rdb: Database) -> None:
    uid = await _user(rdb, "bob")
    await UserRepo(rdb).disable(uid, None)
    with pytest.raises(UserInactive):
        await AccountRepo(rdb).create(uid, "Новый", capacity=20)
    assert await _statuses(rdb, uid) == []


async def test_create_rejected_for_deleting_user(rdb: Database) -> None:
    uid = await _user(rdb, "bob")
    repo = AccountRepo(rdb)
    await repo.create(uid, "Первый", capacity=20)
    await UserRepo(rdb).mark_deleting(uid)
    with pytest.raises(UserInactive):
        await repo.create(uid, "Второй", capacity=20)
    assert await _statuses(rdb, uid) == [("deleting", None)]


async def test_create_rejected_for_missing_user(rdb: Database) -> None:
    with pytest.raises(UserInactive):
        await AccountRepo(rdb).create(9999, "Новый", capacity=20)


async def test_create_reads_limit_from_user_row(rdb: Database) -> None:
    uid = await _user(rdb, "bob", max_accounts=2)
    repo = AccountRepo(rdb)
    await repo.create(uid, "Первый", capacity=20)
    await UserRepo(rdb).set_limit(uid, 1)
    with pytest.raises(LimitReached):
        await repo.create(uid, "Второй", capacity=20)


async def test_limit_lowered_while_create_waits_is_observed(rdb: Database) -> None:
    uid = await _user(rdb, "bob", max_accounts=2)
    repo = AccountRepo(rdb)
    await repo.create(uid, "Первый", capacity=20)
    async with rdb.sessions() as side, side.begin():
        await _hold_enabled_lock(side)
        task = asyncio.create_task(repo.create(uid, "Второй", capacity=20))
        await _lock_waiters(rdb, 1)
        await _bounded(UserRepo(rdb).set_limit(uid, 1))
    with pytest.raises(LimitReached):
        await _bounded(task)


async def test_disable_while_create_waits_rejects_create(rdb: Database) -> None:
    # Запрос прошёл проверку сессии и ждёт в транзакции создания, а учётку тем временем отключили.
    uid = await _user(rdb, "bob")
    repo = AccountRepo(rdb)
    async with rdb.sessions() as side, side.begin():
        await _hold_enabled_lock(side)
        task = asyncio.create_task(repo.create(uid, "Новый", capacity=20))
        await _lock_waiters(rdb, 1)
        await _bounded(UserRepo(rdb).disable(uid, None))
    with pytest.raises(UserInactive):
        await _bounded(task)
    assert await _statuses(rdb, uid) == []


async def test_delete_while_create_waits_keeps_user_deletable(rdb: Database) -> None:
    uid = await _user(rdb, "bob")
    repo = AccountRepo(rdb)
    first = await repo.create(uid, "Первый", capacity=20)
    async with rdb.sessions() as side, side.begin():
        await _hold_enabled_lock(side)
        task = asyncio.create_task(repo.create(uid, "Второй", capacity=20))
        await _lock_waiters(rdb, 1)
        assert await _bounded(UserRepo(rdb).mark_deleting(uid)) == [first.id]
    with pytest.raises(UserInactive):
        await _bounded(task)
    await _bounded(repo.purge(first.id))
    assert await UserRepo(rdb).get(uid) is None


async def test_disable_while_enable_waits_rejects_enable(rdb: Database) -> None:
    uid = await _user(rdb, "bob")
    repo = AccountRepo(rdb)
    acc = await repo.create(uid, "Первый", capacity=20)
    await repo.update(acc.id, enabled=False, capacity=20)
    async with rdb.sessions() as side, side.begin():
        await _hold_enabled_lock(side)
        task = asyncio.create_task(repo.update(acc.id, enabled=True, capacity=20))
        await _lock_waiters(rdb, 1)
        await _bounded(UserRepo(rdb).disable(uid, None))
    with pytest.raises(UserInactive):
        await _bounded(task)
    assert await _statuses(rdb, uid) == [("disabled", "user_disabled")]


async def test_enable_holding_user_lock_then_disable_no_deadlock(rdb: Database) -> None:
    # Включение уже держит строку учётки и ждёт строку аккаунта; отключение ждёт строку учётки.
    # Обратный порядок (аккаунт, затем учётка) здесь дал бы взаимную блокировку.
    uid = await _user(rdb, "bob")
    repo = AccountRepo(rdb)
    acc = await repo.create(uid, "Первый", capacity=20)
    await repo.update(acc.id, enabled=False, capacity=20)
    async with rdb.sessions() as side, side.begin():
        await side.execute(select(Account.id).where(Account.id == acc.id).with_for_update())
        enable = asyncio.create_task(repo.update(acc.id, enabled=True, capacity=20))
        await _lock_waiters(rdb, 1)
        disable = asyncio.create_task(UserRepo(rdb).disable(uid, None))
        await _lock_waiters(rdb, 2)
    enabled = await _bounded(enable)
    assert enabled.status == "enabled"
    assert await _bounded(disable) == [acc.id]
    assert await _statuses(rdb, uid) == [("disabled", "user_disabled")]


@pytest.mark.parametrize("op", ["create", "enable"])
async def test_concurrent_disable_never_leaves_enabled_account(rdb: Database, op: str) -> None:
    repo = AccountRepo(rdb)
    users = UserRepo(rdb)
    for i in range(10):
        uid = await _user(rdb, f"race{i}")
        if op == "create":
            action: Awaitable[Any] = repo.create(uid, "Новый", capacity=99)
        else:
            acc = await repo.create(uid, "Первый", capacity=99)
            await repo.update(acc.id, enabled=False, capacity=99)
            action = repo.update(acc.id, enabled=True, capacity=99)
        results = await _bounded(
            asyncio.gather(action, users.disable(uid, None), return_exceptions=True)
        )
        assert not isinstance(results[1], BaseException), results
        assert results[0] is not None
        if isinstance(results[0], BaseException):
            assert isinstance(results[0], UserInactive), results
        assert all(status != "enabled" for status, _ in await _statuses(rdb, uid))


async def test_concurrent_purge_of_last_two_accounts_deletes_user(rdb: Database) -> None:
    uid = await _user(rdb, "bob")
    repo = AccountRepo(rdb)
    first = await repo.create(uid, "Первый", capacity=20)
    second = await repo.create(uid, "Второй", capacity=20)
    await UserRepo(rdb).mark_deleting(uid)
    async with rdb.sessions() as side, side.begin():
        # Обе чистки доходят до своей последней транзакции и ждут вместе.
        await side.execute(text("LOCK TABLE users IN ACCESS EXCLUSIVE MODE"))
        tasks = [asyncio.create_task(repo.purge(a.id)) for a in (first, second)]
        await _lock_waiters(rdb, 2)
    await _bounded(asyncio.gather(*tasks))
    async with rdb.sessions() as session:
        left = await session.scalar(
            select(func.count()).select_from(Account).where(Account.owner_id == uid)
        )
        assert left == 0
    assert await UserRepo(rdb).get(uid) is None

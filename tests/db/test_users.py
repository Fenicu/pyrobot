import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.db.accounts import AccountRepo
from app.db.base import Database
from app.db.models import Account, AuthSession, User
from app.db.users import LastOwner, UserInfo, UserRepo

pytestmark = pytest.mark.db


async def test_get_by_login_and_all(clean_db: Database) -> None:
    users = UserRepo(clean_db)
    async with clean_db.sessions() as s, s.begin():
        s.add(User(login="owner1", password_hash="h1", role="owner", max_accounts=10))
        s.add(User(login="user1", password_hash="h2", role="user", max_accounts=2))

    all_users = await users.all()
    assert [u.login for u in all_users] == ["owner1", "user1"]

    o1 = await users.by_login("owner1")
    assert o1 is not None and o1.role == "owner" and o1.max_accounts == 10
    assert o1.active is True

    by_id = await users.get(o1.id)
    assert by_id == o1

    assert await users.by_login("nonexistent") is None
    assert await users.get(999999) is None


async def test_user_info_active_property() -> None:
    now = datetime.now(UTC)
    u = UserInfo(
        id=1,
        login="u",
        role="user",
        max_accounts=1,
        created_at=now,
        last_login_at=None,
        disabled_at=None,
        disabled_reason=None,
        invited_by=None,
        deleting_at=None,
    )
    assert u.active is True
    u_disabled = UserInfo(
        id=1,
        login="u",
        role="user",
        max_accounts=1,
        created_at=now,
        last_login_at=None,
        disabled_at=now,
        disabled_reason="spam",
        invited_by=None,
        deleting_at=None,
    )
    assert u_disabled.active is False
    u_deleting = UserInfo(
        id=1,
        login="u",
        role="user",
        max_accounts=1,
        created_at=now,
        last_login_at=None,
        disabled_at=None,
        disabled_reason=None,
        invited_by=None,
        deleting_at=now,
    )
    assert u_deleting.active is False


async def test_promote_and_unknown_login(clean_db: Database) -> None:
    users = UserRepo(clean_db)
    async with clean_db.sessions() as s, s.begin():
        s.add(User(login="user1", password_hash="h", role="user"))

    promoted = await users.promote("user1")
    assert promoted.role == "owner"

    fetched = await users.by_login("user1")
    assert fetched is not None and fetched.role == "owner"

    with pytest.raises(KeyError):
        await users.promote("unknown")


async def test_set_limit(clean_db: Database) -> None:
    users = UserRepo(clean_db)
    async with clean_db.sessions() as s, s.begin():
        u = User(login="u1", password_hash="h", role="user", max_accounts=1)
        s.add(u)
        await s.flush()
        uid = u.id

    updated = await users.set_limit(uid, 5)
    assert updated.max_accounts == 5
    check = await users.get(uid)
    assert check is not None and check.max_accounts == 5

    with pytest.raises(KeyError):
        await users.set_limit(999999, 10)


async def test_touch_login(clean_db: Database) -> None:
    users = UserRepo(clean_db)
    async with clean_db.sessions() as s, s.begin():
        u = User(login="u1", password_hash="h", role="user")
        s.add(u)
        await s.flush()
        uid = u.id

    assert (await users.get(uid)).last_login_at is None  # type: ignore[union-attr]
    await users.touch_login(uid)
    assert (await users.get(uid)).last_login_at is not None  # type: ignore[union-attr]


async def test_owners_active(clean_db: Database) -> None:
    users = UserRepo(clean_db)
    assert await users.owners_active() == 0

    async with clean_db.sessions() as s, s.begin():
        s.add(User(login="o1", password_hash="h", role="owner"))
        s.add(User(login="o2", password_hash="h", role="owner", disabled_at=datetime.now(UTC)))
        s.add(User(login="u1", password_hash="h", role="user"))

    assert await users.owners_active() == 1


async def test_disable_closes_sessions_and_disables_accounts(clean_db: Database) -> None:
    users = UserRepo(clean_db)
    accounts = AccountRepo(clean_db)

    async with clean_db.sessions() as s, s.begin():
        owner = User(login="owner", password_hash="h", role="owner")
        user = User(login="bob", password_hash="h", role="user")
        s.add_all([owner, user])
        await s.flush()
        uid = user.id
        exp = datetime.now(UTC)
        s.add(AuthSession(token_hash="th1", csrf_token="c1", admin_user_id=uid, expires_at=exp))
        s.add(AuthSession(token_hash="th2", csrf_token="c2", admin_user_id=uid, expires_at=exp))
        acc = Account(owner_id=uid, name="BobAcc", status="enabled")
        s.add(acc)
        await s.flush()
        acc_id = acc.id

    ids = await users.disable(uid, "spam")
    assert ids == [acc_id]

    acc_row = await accounts.get(acc_id)
    assert acc_row is not None
    assert acc_row.status == "disabled"
    assert acc_row.status_reason == "user_disabled"

    async with clean_db.sessions() as s:
        stmt = select(AuthSession).where(AuthSession.admin_user_id == uid)
        sessions = list(await s.scalars(stmt))
    assert len(sessions) == 0

    u_row = await users.get(uid)
    assert u_row is not None
    assert u_row.disabled_at is not None
    assert u_row.disabled_reason == "spam"
    assert not u_row.active


async def test_disable_skips_deleting_accounts(clean_db: Database) -> None:
    users = UserRepo(clean_db)
    accounts = AccountRepo(clean_db)

    async with clean_db.sessions() as s, s.begin():
        owner = User(login="owner", password_hash="h", role="owner")
        user = User(login="bob", password_hash="h", role="user")
        s.add_all([owner, user])
        await s.flush()
        uid = user.id
        acc = Account(owner_id=uid, name="DeletingAcc", status="deleting")
        s.add(acc)
        await s.flush()
        acc_id = acc.id

    ids = await users.disable(uid, "spam")
    assert ids == []

    acc_row = await accounts.get(acc_id)
    assert acc_row is not None
    assert acc_row.status == "deleting"


async def test_enable_keeps_accounts_disabled(clean_db: Database) -> None:
    users = UserRepo(clean_db)
    accounts = AccountRepo(clean_db)

    async with clean_db.sessions() as s, s.begin():
        owner = User(login="owner", password_hash="h", role="owner")
        user = User(login="bob", password_hash="h", role="user")
        s.add_all([owner, user])
        await s.flush()
        uid = user.id
        acc = Account(owner_id=uid, name="BobAcc", status="enabled")
        s.add(acc)
        await s.flush()
        acc_id = acc.id

    await users.disable(uid, "spam")
    enabled_user = await users.enable(uid)
    assert enabled_user.active is True
    assert enabled_user.disabled_at is None
    assert enabled_user.disabled_reason is None

    acc_row = await accounts.get(acc_id)
    assert acc_row is not None
    assert acc_row.status == "disabled"
    assert acc_row.status_reason == "user_disabled"


async def test_last_owner_cannot_be_disabled(clean_db: Database) -> None:
    users = UserRepo(clean_db)
    async with clean_db.sessions() as s, s.begin():
        owner = User(login="sole_owner", password_hash="h", role="owner")
        s.add(owner)
        await s.flush()
        oid = owner.id

    with pytest.raises(LastOwner):
        await users.disable(oid, "reason")


async def test_second_owner_allows_disabling_first(clean_db: Database) -> None:
    users = UserRepo(clean_db)
    async with clean_db.sessions() as s, s.begin():
        o1 = User(login="o1", password_hash="h", role="owner")
        o2 = User(login="o2", password_hash="h", role="owner")
        s.add_all([o1, o2])
        await s.flush()
        id1, id2 = o1.id, o2.id

    await users.disable(id1, "retired")
    assert (await users.get(id1)).active is False  # type: ignore[union-attr]

    # Now o2 is the last active owner and cannot be disabled
    with pytest.raises(LastOwner):
        await users.disable(id2, "retired")


async def test_concurrent_disable_of_two_owners_leaves_one(clean_db: Database) -> None:
    users = UserRepo(clean_db)
    async with clean_db.sessions() as s, s.begin():
        o1 = User(login="o1", password_hash="h", role="owner")
        o2 = User(login="o2", password_hash="h", role="owner")
        s.add_all([o1, o2])
        await s.flush()
        id1, id2 = o1.id, o2.id

    res = await asyncio.gather(
        users.disable(id1, "r1"),
        users.disable(id2, "r2"),
        return_exceptions=True,
    )
    # One succeeds (returns list of int ids), one fails with LastOwner
    successes = [r for r in res if isinstance(r, list)]
    errors = [r for r in res if isinstance(r, LastOwner)]
    assert len(successes) == 1
    assert len(errors) == 1
    assert await users.owners_active() == 1


async def test_mark_deleting_without_accounts_removes_user_immediately(clean_db: Database) -> None:
    users = UserRepo(clean_db)
    async with clean_db.sessions() as s, s.begin():
        owner = User(login="owner", password_hash="h", role="owner")
        user = User(login="user_no_acc", password_hash="h", role="user")
        s.add_all([owner, user])
        await s.flush()
        uid = user.id

    ids = await users.mark_deleting(uid)
    assert ids == []
    assert await users.get(uid) is None


async def test_mark_deleting_with_accounts_marks_accounts_deleting(clean_db: Database) -> None:
    users = UserRepo(clean_db)
    accounts = AccountRepo(clean_db)
    async with clean_db.sessions() as s, s.begin():
        owner = User(login="owner", password_hash="h", role="owner")
        user = User(login="user_with_acc", password_hash="h", role="user")
        s.add_all([owner, user])
        await s.flush()
        uid = user.id
        exp = datetime.now(UTC)
        s.add(AuthSession(token_hash="th1", csrf_token="c1", admin_user_id=uid, expires_at=exp))
        acc1 = Account(owner_id=uid, name="Acc1", status="enabled")
        acc2 = Account(owner_id=uid, name="Acc2", status="disabled")
        acc3 = Account(owner_id=uid, name="Acc3", status="deleting")
        s.add_all([acc1, acc2, acc3])
        await s.flush()
        id1, id2, id3 = acc1.id, acc2.id, acc3.id

    ids = await users.mark_deleting(uid)
    assert sorted(ids) == sorted([id1, id2, id3])

    u = await users.get(uid)
    assert u is not None
    assert u.disabled_at is not None
    assert u.deleting_at is not None
    assert not u.active

    # Sessions cleared
    async with clean_db.sessions() as s:
        stmt = select(AuthSession).where(AuthSession.admin_user_id == uid)
        assert len(list(await s.scalars(stmt))) == 0

    # All accounts are now deleting
    for aid in (id1, id2, id3):
        acc = await accounts.get(aid)
        assert acc is not None and acc.status == "deleting"


async def test_mark_deleting_last_owner_raises(clean_db: Database) -> None:
    users = UserRepo(clean_db)
    async with clean_db.sessions() as s, s.begin():
        owner = User(login="sole_owner", password_hash="h", role="owner")
        s.add(owner)
        await s.flush()
        oid = owner.id

    with pytest.raises(LastOwner):
        await users.mark_deleting(oid)


async def test_finish_deleting(clean_db: Database) -> None:
    users = UserRepo(clean_db)
    async with clean_db.sessions() as s, s.begin():
        owner = User(login="owner", password_hash="h", role="owner")
        user = User(
            login="deleting_user",
            password_hash="h",
            role="user",
            deleting_at=datetime.now(UTC),
        )
        s.add_all([owner, user])
        await s.flush()
        uid = user.id
        acc = Account(owner_id=uid, name="Acc", status="deleting")
        s.add(acc)
        await s.flush()
        acc_id = acc.id

    # Has account -> finish_deleting returns False
    assert await users.finish_deleting(uid) is False
    assert await users.get(uid) is not None

    # Remove account
    async with clean_db.sessions() as s, s.begin():
        row = await s.get(Account, acc_id)
        if row:
            await s.delete(row)

    # Now finish_deleting removes user
    assert await users.finish_deleting(uid) is True
    assert await users.get(uid) is None

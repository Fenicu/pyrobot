import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.db.audit import CLI_ACTOR, Actor, AuditLog
from app.db.base import Database
from app.db.invites import InviteGone, InviteNotFound, InviteRepo
from app.db.models import InviteRow, RecoveryCodeRow, User
from app.db.users import LoginTaken

pytestmark = pytest.mark.db


async def _create_user(db: Database, login: str, role: str = "owner") -> int:
    async with db.sessions() as s, s.begin():
        u = User(login=login, password_hash="hash", role=role, max_accounts=5)
        s.add(u)
        await s.flush()
        return u.id


async def test_peek_states(clean_db: Database) -> None:
    audit = AuditLog(clean_db)
    invites = InviteRepo(clean_db, audit)
    owner_id = await _create_user(clean_db, "owner1")
    actor = Actor(owner_id, "owner1")

    # 1. Unknown token -> InviteNotFound
    with pytest.raises(InviteNotFound):
        await invites.peek("nonexistent-token-12345678901234567890")

    # Create active invite
    token, info = await invites.create(actor, ttl_h=24, max_accounts=3, note="test invite")
    peeked = await invites.peek(token)
    assert peeked.id == info.id
    assert peeked.max_accounts == 3
    assert peeked.note == "test invite"
    assert peeked.created_by == owner_id

    # 2. Revoked -> InviteGone
    await invites.revoke(info.id, actor)
    with pytest.raises(InviteGone):
        await invites.peek(token)

    # 3. Expired -> InviteGone
    token_exp, info_exp = await invites.create(actor, ttl_h=1, max_accounts=1, note=None)
    # Move expires_at to the past
    past = datetime.now(UTC) - timedelta(hours=2)
    async with clean_db.sessions() as s, s.begin():
        await s.execute(
            update(InviteRow).where(InviteRow.id == info_exp.id).values(expires_at=past)
        )
    with pytest.raises(InviteGone):
        await invites.peek(token_exp)

    # 4. Used -> InviteGone
    token_used, _ = await invites.create(actor, ttl_h=24, max_accounts=2, note=None)
    await invites.accept(token_used, "newbie", "phash", ["h1", "h2"])
    with pytest.raises(InviteGone):
        await invites.peek(token_used)


async def test_accept_creates_user_with_limit_and_inviter(clean_db: Database) -> None:
    audit = AuditLog(clean_db)
    invites = InviteRepo(clean_db, audit)
    owner_id = await _create_user(clean_db, "owner1")
    actor = Actor(owner_id, "owner1")

    token, info = await invites.create(actor, ttl_h=48, max_accounts=7, note="invite 7")
    code_hashes = [f"ch_{i}" for i in range(10)]

    user, code_ids = await invites.accept(token, "reg_user", "some_hash", code_hashes)
    assert user.login == "reg_user"
    assert user.role == "user"
    assert user.max_accounts == 7
    assert user.invited_by == owner_id
    assert len(code_ids) == 10

    # Verify recovery codes inserted
    async with clean_db.sessions() as s:
        rows = list(
            await s.scalars(
                select(RecoveryCodeRow)
                .where(RecoveryCodeRow.user_id == user.id)
                .order_by(RecoveryCodeRow.id)
            )
        )
        assert len(rows) == 10
        assert [r.id for r in rows] == code_ids
        assert [r.code_hash for r in rows] == code_hashes

    # Verify invite row state
    async with clean_db.sessions() as s:
        inv_row = await s.get(InviteRow, info.id)
        assert inv_row is not None
        assert inv_row.used_at is not None
        assert inv_row.used_by == user.id

    # Verify audit
    entries, _ = await audit.page(limit=10)
    user_reg = next((e for e in entries if e.action == "user_registered"), None)
    assert user_reg is not None
    assert user_reg.actor_user_id == user.id
    assert user_reg.actor_login == "reg_user"
    assert user_reg.target_type == "user"
    assert user_reg.target_id == user.id


async def test_accept_twice_is_gone(clean_db: Database) -> None:
    audit = AuditLog(clean_db)
    invites = InviteRepo(clean_db, audit)
    owner_id = await _create_user(clean_db, "owner1")
    actor = Actor(owner_id, "owner1")

    token, _ = await invites.create(actor, ttl_h=12, max_accounts=1, note=None)
    await invites.accept(token, "user_first", "hash", ["h1"])

    with pytest.raises(InviteGone):
        await invites.accept(token, "user_second", "hash", ["h2"])


async def test_concurrent_accept_one_wins(clean_db: Database) -> None:
    audit = AuditLog(clean_db)
    invites = InviteRepo(clean_db, audit)
    owner_id = await _create_user(clean_db, "owner1")
    actor = Actor(owner_id, "owner1")

    token, _ = await invites.create(actor, ttl_h=12, max_accounts=1, note=None)

    async def try_accept(login: str) -> str:
        try:
            await invites.accept(token, login, "hash", ["h1"])
            return "ok"
        except InviteGone:
            return "gone"

    results = await asyncio.gather(try_accept("user_a"), try_accept("user_b"))
    assert sorted(results) == ["gone", "ok"]


async def test_ttl_over_30_days_rejected(clean_db: Database) -> None:
    audit = AuditLog(clean_db)
    invites = InviteRepo(clean_db, audit)
    actor = CLI_ACTOR

    with pytest.raises(ValueError, match="ttl_h"):
        await invites.create(actor, ttl_h=721, max_accounts=1, note=None)

    with pytest.raises(ValueError, match="ttl_h"):
        await invites.create(actor, ttl_h=0, max_accounts=1, note=None)

    with pytest.raises(ValueError, match="ttl_h"):
        await invites.create(actor, ttl_h=-5, max_accounts=1, note=None)


async def test_accept_login_taken(clean_db: Database) -> None:
    audit = AuditLog(clean_db)
    invites = InviteRepo(clean_db, audit)
    await _create_user(clean_db, "existing_user")
    token, _ = await invites.create(CLI_ACTOR, ttl_h=24, max_accounts=1, note=None)

    with pytest.raises(LoginTaken):
        await invites.accept(token, "existing_user", "hash", ["h1"])


async def test_revoke_and_unused(clean_db: Database) -> None:
    audit = AuditLog(clean_db)
    invites = InviteRepo(clean_db, audit)
    owner_id = await _create_user(clean_db, "owner1")
    actor = Actor(owner_id, "owner1")

    # Unknown invite revoke -> InviteNotFound
    with pytest.raises(InviteNotFound):
        await invites.revoke(999999, actor)

    _tok1, inf1 = await invites.create(actor, ttl_h=24, max_accounts=1, note="first")
    tok2, inf2 = await invites.create(actor, ttl_h=24, max_accounts=2, note="second")
    _tok3, inf3 = await invites.create(actor, ttl_h=1, max_accounts=3, note="third")

    # Expire inf3
    past = datetime.now(UTC) - timedelta(hours=2)
    async with clean_db.sessions() as s, s.begin():
        await s.execute(update(InviteRow).where(InviteRow.id == inf3.id).values(expires_at=past))

    # All 3 are unused (including expired)
    unused_list = await invites.unused()
    assert [u.id for u in unused_list] == [inf1.id, inf2.id, inf3.id]

    # Revoke inf1
    await invites.revoke(inf1.id, actor)

    # Revoke again -> InviteGone
    with pytest.raises(InviteGone):
        await invites.revoke(inf1.id, actor)

    # Use inf2
    await invites.accept(tok2, "u_inf2", "hash", ["h"])

    # Revoke used -> InviteGone
    with pytest.raises(InviteGone):
        await invites.revoke(inf2.id, actor)

    # Expired but unused is still listed and can be revoked
    unused_after = await invites.unused()
    assert [u.id for u in unused_after] == [inf3.id]
    await invites.revoke(inf3.id, actor)
    assert await invites.unused() == []
    with pytest.raises(InviteGone):
        await invites.revoke(inf3.id, actor)

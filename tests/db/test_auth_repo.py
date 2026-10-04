from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from app.db.auth_repo import AuthRepo
from app.db.base import Database
from app.db.models import AuthSession, User

pytestmark = pytest.mark.db


async def test_purge_expired_sessions(clean_db: Database) -> None:
    repo = AuthRepo(clean_db)
    await repo.ensure_owner("admin", "correct horse battery")
    user = await repo.get_user("admin")
    assert user is not None
    _, old = await repo.create_session(user.id, None, None)
    _, fresh = await repo.create_session(user.id, None, None)
    async with clean_db.sessions() as s, s.begin():
        await s.execute(
            update(AuthSession)
            .where(AuthSession.id == old.id)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert await repo.purge_expired() == 1
    async with clean_db.sessions() as s:
        ids = list(await s.scalars(select(AuthSession.id)))
        assert ids == [fresh.id]
        assert await s.scalar(select(func.count()).select_from(AuthSession)) == 1


async def test_ensure_owner_creates_owner_on_empty_db(clean_db: Database) -> None:
    repo = AuthRepo(clean_db)
    await repo.ensure_owner("admin", "correct horse battery")
    user = await repo.get_user("admin")
    assert user is not None
    assert user.role == "owner"
    assert user.max_accounts == 10


async def test_ensure_owner_noop_when_owner_exists(clean_db: Database) -> None:
    repo = AuthRepo(clean_db)
    await repo.ensure_owner("admin", "correct horse battery")
    await repo.ensure_owner("second", "password12345")
    assert await repo.get_user("second") is None


async def test_resolve_rejects_disabled_and_deleting_user(clean_db: Database) -> None:
    repo = AuthRepo(clean_db)
    await repo.ensure_owner("admin", "password12345")
    user = await repo.get_user("admin")
    assert user is not None
    token, _ = await repo.create_session(user.id, None, None)
    assert await repo.resolve(token) is not None

    # Disable user
    async with clean_db.sessions() as s, s.begin():
        await s.execute(
            update(User).where(User.id == user.id).values(disabled_at=datetime.now(UTC))
        )
    assert await repo.resolve(token) is None

    # Reset disabled, set deleting
    async with clean_db.sessions() as s, s.begin():
        await s.execute(
            update(User)
            .where(User.id == user.id)
            .values(disabled_at=None, deleting_at=datetime.now(UTC))
        )
    assert await repo.resolve(token) is None

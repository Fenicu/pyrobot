from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from app.db.auth_repo import AuthRepo
from app.db.base import Database
from app.db.models import AuthSession

pytestmark = pytest.mark.db


async def test_purge_expired_sessions(clean_db: Database) -> None:
    repo = AuthRepo(clean_db)
    await repo.ensure_admin("admin", "correct horse battery")
    admin = await repo.get_admin("admin")
    assert admin is not None
    _, old = await repo.create_session(admin.id, None, None)
    _, fresh = await repo.create_session(admin.id, None, None)
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

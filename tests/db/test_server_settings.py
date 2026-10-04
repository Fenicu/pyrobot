import pytest

from app.db.audit import Actor, AuditLog
from app.db.base import Database
from app.db.models import User
from app.db.server_settings import ServerSettingsRepo
from app.engine.settings import SettingsConflict

pytestmark = pytest.mark.db


async def _seed_user(db: Database) -> Actor:
    async with db.sessions() as session, session.begin():
        user = User(
            id=1,
            login="owner1",
            password_hash="h",
            role="owner",
            max_accounts=10,
        )
        session.add(user)
    return Actor(user_id=1, login="owner1")


async def test_update_bumps_version_and_audits_diff(clean_db: Database) -> None:
    actor = await _seed_user(clean_db)
    audit = AuditLog(clean_db)
    repo = ServerSettingsRepo(clean_db, audit)
    settings, version = await repo.load()
    assert version == 1
    assert settings.retention.messages_days == 90

    new_settings, new_version, diff = await repo.update(
        {"retention": {"messages_days": 120}},
        version=1,
        actor=actor,
    )
    assert new_version == 2
    assert new_settings.retention.messages_days == 120
    assert diff == {"retention.messages_days": [90, 120]}

    # Repo properties updated
    assert repo.version == 2
    assert repo.current.retention.messages_days == 120

    # Audit entry written
    entries, _ = await audit.page(limit=1)
    assert len(entries) == 1
    assert entries[0].action == "server_settings_changed"
    assert entries[0].target_type == "server"
    assert entries[0].target_id == 1
    assert entries[0].actor_login == "owner1"
    assert entries[0].details == {"changes": {"retention.messages_days": [90, 120]}}


async def test_update_version_conflict(clean_db: Database) -> None:
    actor = await _seed_user(clean_db)
    audit = AuditLog(clean_db)
    repo = ServerSettingsRepo(clean_db, audit)
    await repo.load()

    with pytest.raises(SettingsConflict) as exc_info:
        await repo.update(
            {"retention": {"messages_days": 120}},
            version=99,
            actor=actor,
        )
    assert "99" in str(exc_info.value)


async def test_empty_diff_keeps_version_and_writes_nothing(clean_db: Database) -> None:
    actor = await _seed_user(clean_db)
    audit = AuditLog(clean_db)
    repo = ServerSettingsRepo(clean_db, audit)
    await repo.load()

    # Apply same values as defaults
    _new_settings, new_version, diff = await repo.update(
        {"retention": {"messages_days": 90}},
        version=1,
        actor=actor,
    )
    assert new_version == 1
    assert diff == {}
    assert repo.version == 1

    entries, _ = await audit.page()
    assert entries == []


async def test_cache_follows_update(clean_db: Database) -> None:
    actor = await _seed_user(clean_db)
    audit = AuditLog(clean_db)
    repo = ServerSettingsRepo(clean_db, audit)
    await repo.load()
    assert repo.current.limits.sse_per_user == 5

    await repo.update({"limits": {"sse_per_user": 10}}, version=1, actor=actor)
    assert repo.current.limits.sse_per_user == 10

    # Second instance loads new version from db
    repo2 = ServerSettingsRepo(clean_db, audit)
    s2, v2 = await repo2.load()
    assert v2 == 2
    assert s2.limits.sse_per_user == 10

import pytest

from app.db.audit import CLI_ACTOR, Actor, AuditLog
from app.db.base import Database
from app.db.models import User

pytestmark = pytest.mark.db


async def test_write_and_page_newest_first(clean_db: Database) -> None:
    async with clean_db.sessions() as session, session.begin():
        session.add(
            User(
                id=1,
                login="owner_user",
                password_hash="h",
                role="owner",
                max_accounts=10,
            )
        )
        session.add(
            User(
                id=2,
                login="other_user",
                password_hash="h",
                role="user",
                max_accounts=1,
            )
        )
    audit = AuditLog(clean_db)
    actor = Actor(user_id=1, login="owner_user")

    await audit.write(
        actor,
        "invite_created",
        target_type="invite",
        target_id=10,
        details={"note": "for friend"},
    )
    await audit.write(
        CLI_ACTOR,
        "password_set_by_cli",
        target_type="user",
        target_id=1,
    )
    await audit.write(
        actor,
        "user_limit_changed",
        target_type="user",
        target_id=2,
        details={"from": 1, "to": 5},
    )

    items, next_before = await audit.page(limit=2)
    assert len(items) == 2
    assert items[0].action == "user_limit_changed"
    assert items[0].actor_login == "owner_user"
    assert items[0].details == {"from": 1, "to": 5}
    assert items[1].action == "password_set_by_cli"
    assert items[1].actor_login == "cli"
    assert items[1].actor_user_id is None
    assert next_before == items[1].id

    # Second page
    items2, next_before2 = await audit.page(limit=2, before=next_before)
    assert len(items2) == 1
    assert items2[0].action == "invite_created"
    assert items2[0].target_type == "invite"
    assert items2[0].target_id == 10
    assert items2[0].details == {"note": "for friend"}
    assert next_before2 is None


async def test_write_inside_callers_transaction_rolls_back_with_it(clean_db: Database) -> None:
    audit = AuditLog(clean_db)
    try:
        async with clean_db.sessions() as session, session.begin():
            await audit.write(
                CLI_ACTOR,
                "owner_promoted",
                target_type="user",
                target_id=1,
                session=session,
            )
            raise RuntimeError("something went wrong")
    except RuntimeError:
        pass

    items, _ = await audit.page()
    assert items == []


async def test_actor_deleted_keeps_login_copy(clean_db: Database) -> None:
    async with clean_db.sessions() as session, session.begin():
        user = User(
            login="temp_admin",
            password_hash="hash",
            role="owner",
            max_accounts=10,
        )
        session.add(user)
        await session.flush()
        user_id = user.id

    audit = AuditLog(clean_db)
    actor = Actor(user_id=user_id, login="temp_admin")
    await audit.write(actor, "user_disabled", target_type="user", target_id=99)

    items, _ = await audit.page(limit=1)
    assert len(items) == 1
    assert items[0].actor_user_id == user_id
    assert items[0].actor_login == "temp_admin"

    # Delete the user: foreign key has ON DELETE SET NULL
    async with clean_db.sessions() as session, session.begin():
        u = await session.get(User, user_id)
        assert u is not None
        await session.delete(u)

    items_after, _ = await audit.page(limit=1)
    assert len(items_after) == 1
    assert items_after[0].actor_user_id is None
    assert items_after[0].actor_login == "temp_admin"

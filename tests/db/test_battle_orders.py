import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.db.base import Database
from app.db.battle_orders import BattleOrderRepo, lock_key
from app.db.models import BattleOrderRow, User
from app.engine.battle_orders import BattleOrder
from tests.db.helpers import backend_pid, wait_blocked

pytestmark = pytest.mark.db

BATTLE = datetime(2026, 10, 18, 10, 0, tzinfo=UTC)
LATER = BATTLE + timedelta(hours=9)
HOOLI, UMBRELLA = "🤖Hooli", "☂️Umbrella"


async def _user(db: Database, login: str) -> int:
    async with db.sessions() as session, session.begin():
        user = User(login=login, password_hash="x", role="user")
        session.add(user)
        await session.flush()
        return user.id


async def _rows(db: Database, owner_id: int | None = None) -> set[tuple[int, datetime, str, str]]:
    query = select(
        BattleOrderRow.owner_id,
        BattleOrderRow.battle_at,
        BattleOrderRow.scope,
        BattleOrderRow.target,
    )
    if owner_id is not None:
        query = query.where(BattleOrderRow.owner_id == owner_id)
    async with db.sessions() as session:
        return {(o, b, s, t) for o, b, s, t in await session.execute(query)}


async def test_replace_then_active(clean_db: Database) -> None:
    uid = await _user(clean_db, "bob")
    repo = BattleOrderRepo(clean_db)
    await repo.replace(uid, BATTLE, {"all": HOOLI, "bmesa": UMBRELLA}, "bob")
    got = await repo.active(uid, BATTLE - timedelta(hours=2))
    assert set(got) == {BattleOrder(BATTLE, "all", HOOLI), BattleOrder(BATTLE, "bmesa", UMBRELLA)}


async def test_replace_stores_author(clean_db: Database) -> None:
    uid = await _user(clean_db, "bob")
    await BattleOrderRepo(clean_db).replace(uid, BATTLE, {"all": HOOLI}, "alice")
    async with clean_db.sessions() as session:
        assert await session.scalar(select(BattleOrderRow.created_by)) == "alice"


async def test_replace_overwrites_whole_battle(clean_db: Database) -> None:
    uid = await _user(clean_db, "bob")
    repo = BattleOrderRepo(clean_db)
    await repo.replace(uid, BATTLE, {"all": HOOLI, "bmesa": UMBRELLA}, "bob")
    await repo.replace(uid, BATTLE, {"piper": HOOLI}, "bob")
    assert await _rows(clean_db) == {(uid, BATTLE, "piper", HOOLI)}


async def test_replace_keeps_other_battles(clean_db: Database) -> None:
    uid = await _user(clean_db, "bob")
    repo = BattleOrderRepo(clean_db)
    await repo.replace(uid, BATTLE, {"all": HOOLI}, "bob")
    await repo.replace(uid, LATER, {"all": UMBRELLA}, "bob")
    assert await _rows(clean_db) == {(uid, BATTLE, "all", HOOLI), (uid, LATER, "all", UMBRELLA)}


async def test_replace_empty_cancels(clean_db: Database) -> None:
    uid = await _user(clean_db, "bob")
    repo = BattleOrderRepo(clean_db)
    await repo.replace(uid, BATTLE, {"all": HOOLI}, "bob")
    await repo.replace(uid, BATTLE, {}, "bob")
    assert await _rows(clean_db) == set()


async def test_active_filters_by_since(clean_db: Database) -> None:
    uid = await _user(clean_db, "bob")
    repo = BattleOrderRepo(clean_db)
    await repo.replace(uid, BATTLE, {"all": HOOLI}, "bob")
    await repo.replace(uid, LATER, {"all": UMBRELLA}, "bob")
    assert await repo.active(uid, BATTLE) == (
        BattleOrder(BATTLE, "all", HOOLI),
        BattleOrder(LATER, "all", UMBRELLA),
    )
    assert await repo.active(uid, BATTLE + timedelta(seconds=1)) == (
        BattleOrder(LATER, "all", UMBRELLA),
    )


async def test_isolated_by_owner(clean_db: Database) -> None:
    bob, eve = await _user(clean_db, "bob"), await _user(clean_db, "eve")
    repo = BattleOrderRepo(clean_db)
    await repo.replace(bob, BATTLE, {"all": HOOLI}, "bob")
    await repo.replace(eve, BATTLE, {"all": UMBRELLA}, "eve")
    await repo.replace(eve, BATTLE, {}, "eve")
    assert await repo.active(bob, BATTLE) == (BattleOrder(BATTLE, "all", HOOLI),)
    assert await repo.active(eve, BATTLE) == ()


async def test_old_rows_pruned_on_replace(clean_db: Database) -> None:
    uid = await _user(clean_db, "bob")
    old = datetime.now(UTC) - timedelta(days=2)
    async with clean_db.sessions() as session, session.begin():
        session.add(
            BattleOrderRow(owner_id=uid, battle_at=old, scope="all", target=HOOLI, created_by="x")
        )
    await BattleOrderRepo(clean_db).replace(uid, BATTLE, {"all": HOOLI}, "bob")
    assert await _rows(clean_db) == {(uid, BATTLE, "all", HOOLI)}


async def test_prune_leaves_other_owners_rows(clean_db: Database) -> None:
    bob, eve = await _user(clean_db, "bob"), await _user(clean_db, "eve")
    old = datetime.now(UTC) - timedelta(days=2)
    async with clean_db.sessions() as session, session.begin():
        session.add(
            BattleOrderRow(owner_id=eve, battle_at=old, scope="all", target=HOOLI, created_by="x")
        )
    await BattleOrderRepo(clean_db).replace(bob, BATTLE, {"all": HOOLI}, "bob")
    assert (eve, old, "all", HOOLI) in await _rows(clean_db)


async def test_cascade_on_user_delete(clean_db: Database) -> None:
    uid = await _user(clean_db, "bob")
    await BattleOrderRepo(clean_db).replace(uid, BATTLE, {"all": HOOLI}, "bob")
    async with clean_db.sessions() as session, session.begin():
        await session.execute(text("DELETE FROM users WHERE id = :u"), {"u": uid})
    assert await _rows(clean_db) == set()


async def test_scope_check_constraint(clean_db: Database) -> None:
    uid = await _user(clean_db, "bob")
    with pytest.raises(IntegrityError):
        await BattleOrderRepo(clean_db).replace(uid, BATTLE, {"zzz": HOOLI}, "bob")
    assert await _rows(clean_db) == set()


async def _hold_then_run(
    db: Database, uid: int, *runs: dict[str, str], battle: datetime = BATTLE
) -> None:
    """Держит блокировку приказов битвы, запускает замены и отпускает их разом: все стоят в
    очереди на блокировке (по состоянию Postgres), а не стартуют по порядку."""
    repo = BattleOrderRepo(db)
    async with db.sessions() as holder, holder.begin():
        await holder.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": lock_key(uid, battle)},
        )
        pid = await backend_pid(holder)
        tasks = [asyncio.create_task(repo.replace(uid, battle, orders, "bob")) for orders in runs]
        waiting: list[int] = []
        while len(waiting) < len(runs):
            waiting = await wait_blocked(db, pid)
            await asyncio.sleep(0.01)
    await asyncio.wait_for(asyncio.gather(*tasks), 5)


async def test_concurrent_replace_on_empty_battle_one_set_wins(clean_db: Database) -> None:
    uid = await _user(clean_db, "bob")
    first, second = {"all": HOOLI, "bmesa": UMBRELLA}, {"piper": HOOLI}
    await _hold_then_run(clean_db, uid, first, second)
    rows = {(s, t) for _, _, s, t in await _rows(clean_db)}
    assert rows in ({*first.items()}, {*second.items()})


async def test_concurrent_cancel_and_replace(clean_db: Database) -> None:
    uid = await _user(clean_db, "bob")
    await BattleOrderRepo(clean_db).replace(uid, BATTLE, {"all": HOOLI}, "bob")
    await _hold_then_run(clean_db, uid, {}, {"bmesa": UMBRELLA})
    rows = {(s, t) for _, _, s, t in await _rows(clean_db)}
    assert rows in (set(), {("bmesa", UMBRELLA)})

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import update

from app.db.accounts import AccountRepo
from app.db.base import Database
from app.db.models import Account, SettingsRow, StateSnapshot, User
from app.db.overview import OverviewReads
from app.engine.settings import Settings
from app.engine.state.model import SCHEMA_VERSION, CharacterState, Obs, dump_state

pytestmark = pytest.mark.db

AT = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)


async def _user(db: Database, login: str) -> int:
    async with db.sessions() as session, session.begin():
        user = User(login=login, password_hash="x", role="user", max_accounts=10)
        session.add(user)
        await session.flush()
        return user.id


async def _snapshot(db: Database, account_id: int, state: dict[str, Any]) -> None:
    async with db.sessions() as session, session.begin():
        await session.merge(StateSnapshot(account_id=account_id, version=1, state=state))


async def test_only_owner_accounts_in_id_order(clean_db: Database) -> None:
    repo = AccountRepo(clean_db)
    alice, bob = await _user(clean_db, "alice"), await _user(clean_db, "bob")
    b = await repo.create(bob, "b1")
    a2 = await repo.create(alice, "a2")
    a1 = await repo.create(alice, "a1")
    got = await OverviewReads(clean_db).account_inputs(alice)
    assert [i.info.id for i in got] == sorted([a2.id, a1.id])
    assert b.id not in {i.info.id for i in got}


async def test_user_without_accounts_gets_empty_list(clean_db: Database) -> None:
    uid = await _user(clean_db, "alice")
    assert await OverviewReads(clean_db).account_inputs(uid) == []


async def test_deleting_accounts_excluded(clean_db: Database) -> None:
    repo = AccountRepo(clean_db)
    uid = await _user(clean_db, "alice")
    keep = await repo.create(uid, "keep")
    gone = await repo.create(uid, "gone")
    await repo.mark_deleting(gone.id)
    got = await OverviewReads(clean_db).account_inputs(uid)
    assert [i.info.id for i in got] == [keep.id]


async def test_no_snapshot_gives_no_state(clean_db: Database) -> None:
    uid = await _user(clean_db, "alice")
    await AccountRepo(clean_db).create(uid, "a")
    [inp] = await OverviewReads(clean_db).account_inputs(uid)
    assert inp.state is None


async def test_other_schema_version_gives_no_state(clean_db: Database) -> None:
    uid = await _user(clean_db, "alice")
    acc = await AccountRepo(clean_db).create(uid, "a")
    stale = dump_state(CharacterState(level=Obs(value=7, at=AT)))
    stale["schema_version"] = SCHEMA_VERSION + 1
    await _snapshot(clean_db, acc.id, stale)
    [inp] = await OverviewReads(clean_db).account_inputs(uid)
    assert inp.state is None


async def test_empty_snapshot_gives_no_state(clean_db: Database) -> None:
    uid = await _user(clean_db, "alice")
    acc = await AccountRepo(clean_db).create(uid, "a")
    await _snapshot(clean_db, acc.id, {})
    [inp] = await OverviewReads(clean_db).account_inputs(uid)
    assert inp.state is None


async def test_state_is_parsed(clean_db: Database) -> None:
    uid = await _user(clean_db, "alice")
    acc = await AccountRepo(clean_db).create(uid, "a")
    await _snapshot(clean_db, acc.id, dump_state(CharacterState(level=Obs(value=31, at=AT))))
    [inp] = await OverviewReads(clean_db).account_inputs(uid)
    assert inp.state is not None and inp.state.level is not None
    assert inp.state.level.value == 31


async def test_heavy_keys_are_not_loaded(clean_db: Database) -> None:
    uid = await _user(clean_db, "alice")
    acc = await AccountRepo(clean_db).create(uid, "a")
    data = dump_state(CharacterState(level=Obs(value=31, at=AT)))
    data["applied"] = {f"1:{i}:deed": AT.isoformat() for i in range(500)}
    await _snapshot(clean_db, acc.id, data)
    [inp] = await OverviewReads(clean_db).account_inputs(uid)
    assert inp.state is not None
    assert inp.state.applied == {}
    assert inp.state.level is not None and inp.state.level.value == 31


async def test_settings_parsed_with_defaults_on_garbage(clean_db: Database) -> None:
    repo = AccountRepo(clean_db)
    uid = await _user(clean_db, "alice")
    good = await repo.create(uid, "good")
    bad = await repo.create(uid, "bad")
    live = Settings.model_validate({"engine": {"mode": "live"}}).model_dump(mode="json")
    async with clean_db.sessions() as session, session.begin():
        await session.execute(
            update(SettingsRow).where(SettingsRow.account_id == good.id).values(data=live)
        )
        await session.execute(
            update(SettingsRow)
            .where(SettingsRow.account_id == bad.id)
            .values(data={"engine": {"mode": 5}})
        )
    got = {i.info.id: i for i in await OverviewReads(clean_db).account_inputs(uid)}
    assert got[good.id].settings.engine.mode == "live"
    assert got[bad.id].settings == Settings()


async def test_account_without_settings_row_gets_defaults(clean_db: Database) -> None:
    uid = await _user(clean_db, "alice")
    acc = await AccountRepo(clean_db).create(uid, "a")
    async with clean_db.sessions() as session, session.begin():
        await session.execute(SettingsRow.__table__.delete())
    [inp] = await OverviewReads(clean_db).account_inputs(uid)
    assert inp.info.id == acc.id and inp.settings == Settings()


async def test_info_carries_status(clean_db: Database) -> None:
    repo = AccountRepo(clean_db)
    uid = await _user(clean_db, "alice")
    acc = await repo.create(uid, "a")
    async with clean_db.sessions() as session, session.begin():
        await session.execute(
            update(Account)
            .where(Account.id == acc.id)
            .values(status="error", status_reason="boom")
        )
    [inp] = await OverviewReads(clean_db).account_inputs(uid)
    assert (inp.info.status, inp.info.status_reason) == ("error", "boom")

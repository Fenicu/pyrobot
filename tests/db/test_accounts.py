from datetime import datetime

import pytest
from sqlalchemy import select

from app.db.accounts import (
    AccountDeleting,
    AccountInfo,
    AccountRepo,
    AccountStatus,
    CapacityReached,
    NameTaken,
    TgUserTaken,
)
from app.db.base import Database
from app.db.models import Account, AdminUser, SettingsRow
from app.engine.settings import Settings

pytestmark = pytest.mark.db


async def _admin(db: Database, login: str) -> int:
    async with db.sessions() as session, session.begin():
        admin = AdminUser(login=login, password_hash="x")
        session.add(admin)
        await session.flush()
        return admin.id


@pytest.fixture
async def repo(clean_db: Database) -> AccountRepo:
    return AccountRepo(clean_db)


@pytest.fixture
async def admin_id(clean_db: Database) -> int:
    return await _admin(clean_db, "admin")


async def _info(repo: AccountRepo, account_id: int) -> AccountInfo:
    info = await repo.get(account_id)
    assert info is not None
    return info


async def _settings_row(db: Database, account_id: int) -> SettingsRow | None:
    async with db.sessions() as session:
        return await session.get(SettingsRow, account_id)


async def _updated_at(db: Database, account_id: int) -> datetime:
    async with db.sessions() as session:
        value = await session.scalar(select(Account.updated_at).where(Account.id == account_id))
    assert value is not None
    return value


async def test_create_adds_enabled_account_with_default_settings(
    repo: AccountRepo, clean_db: Database, admin_id: int
) -> None:
    acc = await repo.create(admin_id, "Второй", capacity=20)
    assert acc.status == "enabled" and acc.engine_generation == 0
    assert acc.owner_id == admin_id and acc.name == "Второй"
    assert acc.status_reason is None and acc.tg_user_id is None
    row = await _settings_row(clean_db, acc.id)
    assert row is not None and row.version == 1
    assert row.data == Settings().model_dump(mode="json")
    assert await repo.get(acc.id) == acc


async def test_create_name_taken_per_owner(
    repo: AccountRepo, clean_db: Database, admin_id: int
) -> None:
    first = await repo.create(admin_id, "Второй", capacity=20)
    with pytest.raises(NameTaken):
        await repo.create(admin_id, "Второй", capacity=20)
    # Отказ не оставляет ни строки аккаунта, ни настроек.
    assert [a.id for a in await repo.owned(admin_id)] == [first.id]
    # Имя уникально только у одного владельца.
    other = await _admin(clean_db, "other")
    assert (await repo.create(other, "Второй", capacity=20)).owner_id == other


async def test_create_capacity_counts_only_enabled(repo: AccountRepo, admin_id: int) -> None:
    # Аккаунт 1 — enabled; с тремя прочими статусами место под один enabled ещё есть.
    others: tuple[tuple[str, AccountStatus], ...] = (
        ("Выключен", "disabled"),
        ("Сбой", "error"),
        ("Удаляется", "deleting"),
    )
    for name, status in others:
        await repo.set_status((await repo.create(admin_id, name, capacity=99)).id, status, None)
    await repo.create(admin_id, "Второй enabled", capacity=2)
    with pytest.raises(CapacityReached):
        await repo.create(admin_id, "Третий enabled", capacity=2)
    assert [a.name for a in await repo.owned(admin_id) if a.status == "enabled"] == [
        "Второй enabled"
    ]


async def test_update_enable_over_capacity_rejected(repo: AccountRepo, admin_id: int) -> None:
    acc = await repo.create(admin_id, "Второй", capacity=2)
    off = await repo.update(acc.id, enabled=False, capacity=2)
    assert off.status == "disabled"
    # Аккаунт 1 не выключен, второй место занял: третьему enabled места нет.
    third = await repo.create(admin_id, "Третий", capacity=2)
    with pytest.raises(CapacityReached):
        await repo.update(acc.id, enabled=True, capacity=2)
    assert (await _info(repo, acc.id)).status == "disabled"
    # Выключенный аккаунт при нехватке места остаётся выключенным, а переименовать его можно.
    assert (await repo.update(acc.id, name="Новое", capacity=2)).name == "Новое"
    await repo.update(third.id, enabled=False, capacity=2)
    assert (await repo.update(acc.id, enabled=True, capacity=2)).status == "enabled"


async def test_update_enable_clears_reason_and_disable_keeps_name(
    repo: AccountRepo, admin_id: int
) -> None:
    acc = await repo.create(admin_id, "Второй", capacity=20)
    await repo.set_status(acc.id, "error", "crash_loop:gateway")
    failed = await repo.get(acc.id)
    assert failed is not None and failed.status == "error"
    assert failed.status_reason == "crash_loop:gateway"
    assert (await repo.update(acc.id, enabled=True, capacity=20)).status_reason is None
    off = await repo.update(acc.id, enabled=False, capacity=20)
    assert (off.status, off.name) == ("disabled", "Второй")
    # Ничего не меняли — ничего не изменилось.
    assert await repo.update(acc.id, capacity=20) == off


async def test_update_rename_to_taken_name_rejected(repo: AccountRepo, admin_id: int) -> None:
    await repo.create(admin_id, "Второй", capacity=20)
    third = await repo.create(admin_id, "Третий", capacity=20)
    with pytest.raises(NameTaken):
        await repo.update(third.id, name="Второй", capacity=20)
    assert (await _info(repo, third.id)).name == "Третий"


async def test_second_account_after_migration_gets_next_id(
    repo: AccountRepo, admin_id: int
) -> None:
    # Аккаунт 1 создан с явным id (как миграцией 0001): счётчик его уже учитывает.
    assert (await repo.create(admin_id, "Второй", capacity=20)).id == 2
    assert (await repo.create(admin_id, "Третий", capacity=20)).id == 3


async def test_bind_telegram_once_and_unique(repo: AccountRepo, admin_id: int) -> None:
    second = await repo.create(admin_id, "Второй", capacity=20)
    await repo.bind_telegram(1, 111)
    await repo.bind_telegram(1, 222)  # уже привязан — без изменений
    assert (await _info(repo, 1)).tg_user_id == 111
    with pytest.raises(TgUserTaken):
        await repo.bind_telegram(second.id, 111)
    assert (await _info(repo, second.id)).tg_user_id is None
    await repo.bind_telegram(second.id, 222)
    assert (await _info(repo, second.id)).tg_user_id == 222


async def test_adopt_orphans_gives_first_admin(repo: AccountRepo, clean_db: Database) -> None:
    # Учёток нет: аккаунт остаётся без владельца.
    assert await repo.adopt_orphans() == 0
    assert (await _info(repo, 1)).owner_id is None
    first = await _admin(clean_db, "first")
    await _admin(clean_db, "second")
    assert await repo.adopt_orphans() == 1
    adopted = await repo.get(1)
    assert adopted is not None and adopted.owner_id == first
    assert await repo.adopt_orphans() == 0


async def test_restart_bumps_generation(repo: AccountRepo, admin_id: int) -> None:
    acc = await repo.create(admin_id, "Второй", capacity=20)
    await repo.restart(acc.id)
    await repo.restart(acc.id)
    assert (await _info(repo, acc.id)).engine_generation == 2
    assert (await _info(repo, 1)).engine_generation == 0


async def test_reads_are_ordered_and_filtered(
    repo: AccountRepo, clean_db: Database, admin_id: int
) -> None:
    other = await _admin(clean_db, "other")
    second = await repo.create(admin_id, "Второй", capacity=20)
    foreign = await repo.create(other, "Чужой", capacity=20)
    third = await repo.create(admin_id, "Третий", capacity=20)
    await repo.set_status(third.id, "disabled", "по просьбе")
    assert await repo.get(999) is None
    assert [a.id for a in await repo.owned(admin_id)] == [second.id, third.id]
    assert [a.id for a in await repo.owned(other)] == [foreign.id]
    assert [a.id for a in await repo.with_status("enabled")] == [1, second.id, foreign.id]
    assert [a.id for a in await repo.with_status("disabled", "error")] == [third.id]
    await repo.mark_deleting(foreign.id)
    assert [a.id for a in await repo.with_status("deleting")] == [foreign.id]
    assert [a.id for a in await repo.with_status()] == []


async def test_every_write_bumps_updated_at(
    repo: AccountRepo, clean_db: Database, admin_id: int
) -> None:
    acc = await repo.create(admin_id, "Второй", capacity=20)
    writes = {
        "update": lambda: repo.update(acc.id, name="Новое", capacity=20),
        "set_status": lambda: repo.set_status(acc.id, "error", "boom"),
        "restart": lambda: repo.restart(acc.id),
        "bind_telegram": lambda: repo.bind_telegram(acc.id, 111),
        "mark_deleting": lambda: repo.mark_deleting(acc.id),
    }
    for name, write in writes.items():
        before = await _updated_at(clean_db, acc.id)
        await write()
        assert await _updated_at(clean_db, acc.id) > before, name
    adopted = await _updated_at(clean_db, 1)
    await _admin(clean_db, "first")
    assert await repo.adopt_orphans() == 1
    assert await _updated_at(clean_db, 1) > adopted


async def test_update_of_deleting_account_rejected(repo: AccountRepo, admin_id: int) -> None:
    acc = await repo.create(admin_id, "Второй", capacity=20)
    await repo.mark_deleting(acc.id)
    deleting = await _info(repo, acc.id)
    assert deleting.status == "deleting"
    for change in ({"enabled": True}, {"enabled": False}, {"name": "Новое"}, {}):
        with pytest.raises(AccountDeleting):
            await repo.update(acc.id, capacity=20, **change)  # type: ignore[arg-type]
    # Ни имя, ни статус не тронуты, место под enabled не занято.
    assert await _info(repo, acc.id) == deleting
    assert (await repo.create(admin_id, "Третий", capacity=2)).status == "enabled"


async def test_set_status_and_restart_skip_deleting_account(
    repo: AccountRepo, clean_db: Database, admin_id: int
) -> None:
    acc = await repo.create(admin_id, "Второй", capacity=20)
    await repo.mark_deleting(acc.id)
    deleting = await _info(repo, acc.id)
    stamp = await _updated_at(clean_db, acc.id)
    await repo.set_status(acc.id, "error", "crash_loop:gateway")
    await repo.set_status(acc.id, "enabled", None)
    await repo.restart(acc.id)
    assert await _info(repo, acc.id) == deleting
    assert await _updated_at(clean_db, acc.id) == stamp
    assert [a.id for a in await repo.with_status("deleting")] == [acc.id]


async def test_mark_deleting_is_idempotent(repo: AccountRepo, admin_id: int) -> None:
    for status in ("enabled", "disabled", "error"):
        acc = await repo.create(admin_id, f"Из {status}", capacity=99)
        await repo.set_status(acc.id, status, "причина")  # type: ignore[arg-type]
        await repo.mark_deleting(acc.id)
        await repo.mark_deleting(acc.id)
        assert (await _info(repo, acc.id)).status == "deleting"
    await repo.mark_deleting(999)  # нет такого аккаунта — ничего не происходит

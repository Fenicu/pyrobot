from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select

from app.db.accounts import (
    AccountDeleting,
    AccountInfo,
    AccountRepo,
    AccountStatus,
    BlockedByOwner,
    CapacityReached,
    LimitReached,
    NameTaken,
    ServerFull,
    TgUserTaken,
)
from app.db.base import Database
from app.db.models import Account, NotificationRow, SettingsRow, StateSnapshot, User
from app.engine.settings import Settings
from app.engine.state.model import SCHEMA_VERSION, CharacterState, Obs, dump_state

pytestmark = pytest.mark.db


async def _user(db: Database, login: str, *, role: str = "owner") -> int:
    async with db.sessions() as session, session.begin():
        user = User(login=login, password_hash="x", role=role)
        session.add(user)
        await session.flush()
        return user.id


@pytest.fixture
async def repo(clean_db: Database) -> AccountRepo:
    return AccountRepo(clean_db)


@pytest.fixture
async def user_id(clean_db: Database) -> int:
    return await _user(clean_db, "admin")


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
    repo: AccountRepo, clean_db: Database, user_id: int
) -> None:
    acc = await repo.create(user_id, "Второй", capacity=20)
    assert acc.status == "enabled" and acc.engine_generation == 0
    assert acc.owner_id == user_id and acc.name == "Второй"
    assert acc.status_reason is None and acc.tg_user_id is None
    row = await _settings_row(clean_db, acc.id)
    assert row is not None and row.version == 1
    assert row.data == Settings().model_dump(mode="json")
    assert await repo.get(acc.id) == acc


async def test_create_name_taken_per_owner(
    repo: AccountRepo, clean_db: Database, user_id: int
) -> None:
    first = await repo.create(user_id, "Второй", capacity=20)
    with pytest.raises(NameTaken):
        await repo.create(user_id, "Второй", capacity=20)
    # Отказ не оставляет ни строки аккаунта, ни настроек.
    assert [a.id for a in await repo.owned(user_id)] == [first.id]
    # Имя уникально только у одного владельца.
    other = await _user(clean_db, "other")
    assert (await repo.create(other, "Второй", capacity=20)).owner_id == other


async def test_create_capacity_counts_only_enabled(repo: AccountRepo, user_id: int) -> None:
    # Аккаунт 1 — enabled; с тремя прочими статусами место под один enabled ещё есть.
    others: tuple[tuple[str, AccountStatus], ...] = (
        ("Выключен", "disabled"),
        ("Сбой", "error"),
        ("Удаляется", "deleting"),
    )
    for name, status in others:
        await repo.set_status((await repo.create(user_id, name, capacity=99)).id, status, None)
    await repo.create(user_id, "Второй enabled", capacity=2)
    with pytest.raises(CapacityReached):
        await repo.create(user_id, "Третий enabled", capacity=2)
    assert [a.name for a in await repo.owned(user_id) if a.status == "enabled"] == [
        "Второй enabled"
    ]


async def test_update_enable_over_capacity_rejected(repo: AccountRepo, user_id: int) -> None:
    acc = await repo.create(user_id, "Второй", capacity=2)
    off = await repo.update(acc.id, enabled=False, capacity=2)
    assert off.status == "disabled"
    # Аккаунт 1 не выключен, второй место занял: третьему enabled места нет.
    third = await repo.create(user_id, "Третий", capacity=2)
    with pytest.raises(CapacityReached):
        await repo.update(acc.id, enabled=True, capacity=2)
    assert (await _info(repo, acc.id)).status == "disabled"
    # Выключенный аккаунт при нехватке места остаётся выключенным, а переименовать его можно.
    assert (await repo.update(acc.id, name="Новое", capacity=2)).name == "Новое"
    await repo.update(third.id, enabled=False, capacity=2)
    assert (await repo.update(acc.id, enabled=True, capacity=2)).status == "enabled"


async def test_update_enable_clears_reason_and_disable_keeps_name(
    repo: AccountRepo, user_id: int
) -> None:
    acc = await repo.create(user_id, "Второй", capacity=20)
    await repo.set_status(acc.id, "error", "crash_loop:gateway")
    failed = await repo.get(acc.id)
    assert failed is not None and failed.status == "error"
    assert failed.status_reason == "crash_loop:gateway"
    assert (await repo.update(acc.id, enabled=True, capacity=20)).status_reason is None
    off = await repo.update(acc.id, enabled=False, capacity=20)
    assert (off.status, off.name) == ("disabled", "Второй")
    # Ничего не меняли — ничего не изменилось.
    assert await repo.update(acc.id, capacity=20) == off


async def test_update_rename_to_taken_name_rejected(repo: AccountRepo, user_id: int) -> None:
    await repo.create(user_id, "Второй", capacity=20)
    third = await repo.create(user_id, "Третий", capacity=20)
    with pytest.raises(NameTaken):
        await repo.update(third.id, name="Второй", capacity=20)
    assert (await _info(repo, third.id)).name == "Третий"


async def test_second_account_after_migration_gets_next_id(
    repo: AccountRepo, user_id: int
) -> None:
    # Аккаунт 1 создан с явным id (как миграцией 0001): счётчик его уже учитывает.
    assert (await repo.create(user_id, "Второй", capacity=20)).id == 2
    assert (await repo.create(user_id, "Третий", capacity=20)).id == 3


async def test_bind_telegram_once_and_unique(repo: AccountRepo, user_id: int) -> None:
    second = await repo.create(user_id, "Второй", capacity=20)
    assert await repo.bind_telegram(1, 111) == 111
    # Уже привязан — без изменений; ответ — привязка из базы.
    assert await repo.bind_telegram(1, 222) == 111
    assert await repo.bind_telegram(1, 111) == 111
    assert (await _info(repo, 1)).tg_user_id == 111
    with pytest.raises(TgUserTaken):
        await repo.bind_telegram(second.id, 111)
    assert (await _info(repo, second.id)).tg_user_id is None
    assert await repo.bind_telegram(second.id, 222) == 222
    assert (await _info(repo, second.id)).tg_user_id == 222
    with pytest.raises(KeyError):
        await repo.bind_telegram(second.id + 1, 333)


async def test_adopt_orphans_gives_first_admin(repo: AccountRepo, clean_db: Database) -> None:
    # Учёток нет: аккаунт остаётся без владельца.
    assert await repo.adopt_orphans() == 0
    assert (await _info(repo, 1)).owner_id is None
    first = await _user(clean_db, "first")
    await _user(clean_db, "second")
    assert await repo.adopt_orphans() == 1
    adopted = await repo.get(1)
    assert adopted is not None and adopted.owner_id == first
    assert await repo.adopt_orphans() == 0


async def test_restart_bumps_generation(repo: AccountRepo, user_id: int) -> None:
    acc = await repo.create(user_id, "Второй", capacity=20)
    await repo.restart(acc.id)
    await repo.restart(acc.id)
    assert (await _info(repo, acc.id)).engine_generation == 2
    assert (await _info(repo, 1)).engine_generation == 0


async def test_reads_are_ordered_and_filtered(
    repo: AccountRepo, clean_db: Database, user_id: int
) -> None:
    other = await _user(clean_db, "other")
    second = await repo.create(user_id, "Второй", capacity=20)
    foreign = await repo.create(other, "Чужой", capacity=20)
    third = await repo.create(user_id, "Третий", capacity=20)
    await repo.set_status(third.id, "disabled", "по просьбе")
    assert await repo.get(999) is None
    assert [a.id for a in await repo.owned(user_id)] == [second.id, third.id]
    assert [a.id for a in await repo.owned(other)] == [foreign.id]
    assert [a.id for a in await repo.with_status("enabled")] == [1, second.id, foreign.id]
    assert [a.id for a in await repo.with_status("disabled", "error")] == [third.id]
    await repo.mark_deleting(foreign.id)
    assert [a.id for a in await repo.with_status("deleting")] == [foreign.id]
    assert [a.id for a in await repo.with_status()] == []


async def test_every_write_bumps_updated_at(
    repo: AccountRepo, clean_db: Database, user_id: int
) -> None:
    acc = await repo.create(user_id, "Второй", capacity=20)
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
    await _user(clean_db, "first")
    assert await repo.adopt_orphans() == 1
    assert await _updated_at(clean_db, 1) > adopted


async def test_update_of_deleting_account_rejected(repo: AccountRepo, user_id: int) -> None:
    acc = await repo.create(user_id, "Второй", capacity=20)
    await repo.mark_deleting(acc.id)
    deleting = await _info(repo, acc.id)
    assert deleting.status == "deleting"
    for change in ({"enabled": True}, {"enabled": False}, {"name": "Новое"}, {}):
        with pytest.raises(AccountDeleting):
            await repo.update(acc.id, capacity=20, **change)  # type: ignore[arg-type]
    # Ни имя, ни статус не тронуты, место под enabled не занято.
    assert await _info(repo, acc.id) == deleting
    assert (await repo.create(user_id, "Третий", capacity=2)).status == "enabled"


async def test_set_status_and_restart_skip_deleting_account(
    repo: AccountRepo, clean_db: Database, user_id: int
) -> None:
    acc = await repo.create(user_id, "Второй", capacity=20)
    await repo.mark_deleting(acc.id)
    deleting = await _info(repo, acc.id)
    stamp = await _updated_at(clean_db, acc.id)
    await repo.set_status(acc.id, "error", "crash_loop:gateway")
    await repo.set_status(acc.id, "enabled", None)
    await repo.restart(acc.id)
    assert await _info(repo, acc.id) == deleting
    assert await _updated_at(clean_db, acc.id) == stamp
    assert [a.id for a in await repo.with_status("deleting")] == [acc.id]


async def test_mark_deleting_is_idempotent(repo: AccountRepo, user_id: int) -> None:
    for status in ("enabled", "disabled", "error"):
        acc = await repo.create(user_id, f"Из {status}", capacity=99)
        await repo.set_status(acc.id, status, "причина")  # type: ignore[arg-type]
        await repo.mark_deleting(acc.id)
        await repo.mark_deleting(acc.id)
        assert (await _info(repo, acc.id)).status == "deleting"
    await repo.mark_deleting(999)  # нет такого аккаунта — ничего не происходит


async def test_create_limit_reached_ignores_deleting(
    repo: AccountRepo, clean_db: Database, user_id: int
) -> None:
    # 1 аккаунт при max_accounts=1: второй вызовет LimitReached
    acc1 = await repo.create(user_id, "Первый", max_accounts=1)
    with pytest.raises(LimitReached):
        await repo.create(user_id, "Второй", max_accounts=1)
    # Помечаем один как deleting: теперь активных аккаунтов 0, создание разрешено
    await repo.mark_deleting(acc1.id)
    acc2 = await repo.create(user_id, "Второй", max_accounts=1)
    assert acc2.name == "Второй"


async def test_create_server_full_counts_all_statuses(
    repo: AccountRepo, clean_db: Database, user_id: int
) -> None:
    other_user = await _user(clean_db, "other")
    await repo.create(user_id, "Первый", total_max=10)
    acc2 = await repo.create(other_user, "Второй", total_max=10)
    await repo.set_status(acc2.id, "disabled", "test")
    # clean_db имеет аккаунт 1 изначально, плюс созданные = 3 аккаунта
    total = len(await repo.with_status("enabled")) + len(await repo.with_status("disabled"))
    with pytest.raises(ServerFull):
        await repo.create(user_id, "Лишний", total_max=total)


async def test_create_checks_in_spec_order(
    repo: AccountRepo, clean_db: Database, user_id: int
) -> None:
    # Порядок проверок: LimitReached -> ServerFull -> CapacityReached
    await repo.create(user_id, "Первый", max_accounts=5, total_max=50, capacity=20)
    # У пользователя 1 аккаунт. На сервере 2 аккаунта (seed + первый).
    # Все три лимита нарушаются: max_accounts=1, total_max=2, capacity=1 -> LimitReached
    with pytest.raises(LimitReached):
        await repo.create(user_id, "Второй", max_accounts=1, total_max=2, capacity=1)
    # Превышены ServerFull и CapacityReached, но max_accounts=5 позволяет -> ServerFull
    with pytest.raises(ServerFull):
        await repo.create(user_id, "Второй", max_accounts=5, total_max=2, capacity=1)
    # Превышен только CapacityReached (total_max=10 позволяет) -> CapacityReached
    with pytest.raises(CapacityReached):
        await repo.create(user_id, "Второй", max_accounts=5, total_max=10, capacity=2)


async def _snapshot(db: Database, account_id: int, state: dict[str, Any]) -> None:
    async with db.sessions() as session, session.begin():
        session.add(StateSnapshot(account_id=account_id, version=1, state=state))


def _seen(value: str | None) -> Obs[str | None]:
    return Obs(value=value, at=datetime(2026, 10, 1, tzinfo=UTC))


async def test_overview_company_and_team_tag_from_snapshot(
    repo: AccountRepo, clean_db: Database, user_id: int
) -> None:
    first = await repo.create(user_id, "Первый", capacity=20)
    second = await repo.create(user_id, "Второй", capacity=20)
    third = await repo.create(user_id, "Третий", capacity=20)
    await _snapshot(
        clean_db,
        first.id,
        dump_state(CharacterState(company=_seen("umbrl"), team_tag=_seen("SU"))),
    )
    await _snapshot(clean_db, second.id, dump_state(CharacterState(company=_seen("piper"))))
    by_id = {o.account.id: o for o in await repo.overview(user_id)}
    assert (by_id[first.id].company, by_id[first.id].team_tag) == ("umbrl", "SU")
    assert (by_id[second.id].company, by_id[second.id].team_tag) == ("piper", None)
    assert (by_id[third.id].company, by_id[third.id].team_tag) == (None, None)


async def test_overview_unreadable_snapshot_gives_none(
    repo: AccountRepo, clean_db: Database, user_id: int
) -> None:
    acc = await repo.create(user_id, "Второй", capacity=20)
    await _snapshot(
        clean_db,
        acc.id,
        {"schema_version": SCHEMA_VERSION, "company": {"value": 5}, "team_tag": "SU"},
    )
    (only,) = await repo.overview(user_id)
    assert (only.company, only.team_tag) == (None, None)


@pytest.mark.parametrize(
    "state",
    [
        {"company": {"value": "piper"}, "team_tag": {"value": "SU"}},
        {"schema_version": SCHEMA_VERSION + 1, "company": {"value": "piper"}},
        {"schema_version": str(SCHEMA_VERSION), "company": {"value": "piper"}},
        {"schema_version": SCHEMA_VERSION, "company": {"value": None}, "team_tag": {}},
        {"schema_version": SCHEMA_VERSION, "company": [], "team_tag": None},
    ],
)
async def test_overview_foreign_snapshot_shapes_give_none(
    repo: AccountRepo, clean_db: Database, user_id: int, state: dict[str, Any]
) -> None:
    acc = await repo.create(user_id, "Второй", capacity=20)
    await _snapshot(clean_db, acc.id, state)
    (only,) = await repo.overview(user_id)
    assert (only.company, only.team_tag) == (None, None)


async def test_block_disables_and_enable_is_refused(
    repo: AccountRepo, clean_db: Database, user_id: int
) -> None:
    acc = await repo.create(user_id, "Блокируемый")
    assert acc.status == "enabled"
    assert acc.blocked is False
    assert acc.blocked_reason is None

    # Блокировка переводит в disabled и выставляет причину
    await repo.block(acc.id, "Нарушение правил")
    info = await _info(repo, acc.id)
    assert info.blocked is True
    assert info.blocked_reason == "Нарушение правил"
    assert info.status == "disabled"
    assert info.status_reason == "blocked_by_owner"

    # Создано уведомление warn account_blocked
    async with clean_db.sessions() as session:
        notif = await session.scalar(
            select(NotificationRow).where(
                NotificationRow.account_id == acc.id, NotificationRow.code == "account_blocked"
            )
        )
        assert notif is not None
        assert notif.level == "warn"
        assert notif.text == "Нарушение правил"

    # Включение заблокированного аккаунта запрещено
    with pytest.raises(BlockedByOwner):
        await repo.update(acc.id, enabled=True, capacity=10)

    # Переименование разрешено
    renamed = await repo.update(acc.id, name="Новое имя", capacity=10)
    assert renamed.name == "Новое имя"
    assert renamed.blocked is True
    assert renamed.status == "disabled"


async def test_unblock_keeps_disabled(repo: AccountRepo, clean_db: Database, user_id: int) -> None:
    acc = await repo.create(user_id, "Разблокируемый")
    await repo.block(acc.id, "Причина")

    # Разблокировка снимает blocked/blocked_reason, статус не трогает
    await repo.unblock(acc.id)
    info = await _info(repo, acc.id)
    assert info.blocked is False
    assert info.blocked_reason is None
    assert info.status == "disabled"
    assert info.status_reason == "blocked_by_owner"

    # Создано уведомление info account_unblocked
    async with clean_db.sessions() as session:
        notif = await session.scalar(
            select(NotificationRow).where(
                NotificationRow.account_id == acc.id, NotificationRow.code == "account_unblocked"
            )
        )
        assert notif is not None
        assert notif.level == "info"
        assert notif.text == "account unblocked by owner"

    # Теперь аккаунт можно включить
    updated = await repo.update(acc.id, enabled=True, capacity=10)
    assert updated.status == "enabled"
    assert updated.status_reason is None


async def test_block_keeps_deleting_status(
    repo: AccountRepo, clean_db: Database, user_id: int
) -> None:
    acc = await repo.create(user_id, "Удаляемый")
    await repo.mark_deleting(acc.id)
    info = await _info(repo, acc.id)
    assert info.status == "deleting"

    # Блокировка удаляемого аккаунта не перезаписывает статус deleting
    await repo.block(acc.id, "Причина")
    info2 = await _info(repo, acc.id)
    assert info2.status == "deleting"
    assert info2.blocked is True
    assert info2.blocked_reason == "Причина"

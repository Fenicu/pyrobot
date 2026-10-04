import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db import models
from app.db.base import Base, Database

TEST_DB_URL = os.environ.get(
    "PYROBOT_TEST_DATABASE_URL",
    "postgresql+asyncpg://pyrobot:pyrobot@localhost:55432/pyrobot_test",
)
MIGTEST_DB_URL = os.environ.get(
    "PYROBOT_TEST_MIGRATIONS_URL",
    "postgresql+asyncpg://pyrobot:pyrobot@localhost:55432/pyrobot_migtest",
)


async def _seed_account(conn: AsyncConnection) -> None:
    """Аккаунт 1, как после миграций; `id` задан явно, поэтому счётчик сдвигается вручную."""
    await conn.execute(insert(models.Account).values(id=1, name="Основной"))
    await conn.execute(text("SELECT setval('accounts_id_seq', 1)"))


@pytest.fixture(scope="session")
async def db() -> AsyncIterator[Database]:
    database = Database(TEST_DB_URL)
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        await _seed_account(conn)
    yield database
    await database.dispose()


@pytest.fixture
async def clean_db(db: Database) -> AsyncIterator[Database]:
    tables = [t.name for t in Base.metadata.sorted_tables]
    async with db.engine.begin() as conn:
        # `accounts` чистится вместе со всеми: на неё ссылаются остальные таблицы, а она — на
        # `users`; аккаунт 1 создаётся заново.
        await conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
        await _seed_account(conn)
    yield db

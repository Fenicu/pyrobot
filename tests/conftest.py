import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import insert, text

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


@pytest.fixture(scope="session")
async def db() -> AsyncIterator[Database]:
    database = Database(TEST_DB_URL)
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(insert(models.Account).values(id=1))
    yield database
    await database.dispose()


@pytest.fixture
async def clean_db(db: Database) -> AsyncIterator[Database]:
    tables = [t.name for t in Base.metadata.sorted_tables if t.name != "accounts"]
    async with db.engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    yield db

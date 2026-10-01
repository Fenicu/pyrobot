from typing import Any, ClassVar

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[type, Any]] = {dict[str, Any]: JSONB, list[Any]: JSONB}


class Database:
    def __init__(self, url: str, *, pool_size: int = 10, max_overflow: int = 20) -> None:
        # Пул — на все движки процесса: `pool_size` постоянных соединений и до `max_overflow`
        # сверх них.
        self.engine: AsyncEngine = create_async_engine(
            url,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_pre_ping=True,
            connect_args={"command_timeout": 30},
        )
        self.sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine, expire_on_commit=False
        )

    async def dispose(self) -> None:
        await self.engine.dispose()

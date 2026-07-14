from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from timelapse.configuration import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    settings = get_settings()

    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
    )


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
    )


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    session_factory = get_session_factory()

    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def database_is_ready() -> bool:
    try:
        async with get_engine().connect() as connection:
            await connection.execute(sa.text("SELECT 1"))
    except Exception:
        return False

    return True


async def close_database() -> None:
    if get_engine.cache_info().currsize:
        await get_engine().dispose()

    get_session_factory.cache_clear()
    get_engine.cache_clear()

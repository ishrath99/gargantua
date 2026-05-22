"""End-to-end checks that the async engine actually talks to Postgres.

We re-use the same DSN as the sync-engine fixtures (psycopg-3 supports both
sync and async over the same URL prefix) but route it through the
``DATABASE_URL_ASYNC`` env var so ``gargantua.settings`` picks it up.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _reset_session_caches() -> None:
    from gargantua.db import session as session_module
    from gargantua.settings import get_settings

    session_module.get_engine.cache_clear()
    session_module.get_session_factory.cache_clear()
    get_settings.cache_clear()


@pytest.fixture
async def async_engine(
    monkeypatch: pytest.MonkeyPatch, _db_ready: str
) -> AsyncIterator[AsyncEngine]:
    """Async engine pointed at the test database; disposed after the test."""
    monkeypatch.setenv("DATABASE_URL_ASYNC", _db_ready)
    _reset_session_caches()

    from gargantua.db.session import dispose_engine, get_engine

    engine = get_engine()
    try:
        yield engine
    finally:
        await dispose_engine()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_engine_can_execute_select_one(async_engine: AsyncEngine) -> None:
    async with async_engine.connect() as conn:
        result = await conn.execute(text("SELECT 1"))
        assert result.scalar_one() == 1


async def test_get_session_yields_usable_async_session(
    async_engine: AsyncEngine,  # noqa: ARG001 — fixture triggers engine setup
) -> None:
    from gargantua.db.session import get_session

    gen = get_session()
    try:
        session = await anext(gen)
        assert isinstance(session, AsyncSession)
        result = await session.execute(text("SELECT 42"))
        assert result.scalar_one() == 42
    finally:
        with pytest.raises(StopAsyncIteration):
            await anext(gen)


async def test_dispose_engine_releases_pool_connections(
    monkeypatch: pytest.MonkeyPatch, _db_ready: str
) -> None:
    """After ``dispose_engine`` the cached engine slot is empty and a fresh
    call rebuilds it with the (possibly rotated) settings."""
    monkeypatch.setenv("DATABASE_URL_ASYNC", _db_ready)
    _reset_session_caches()

    from gargantua.db import session as session_module
    from gargantua.db.session import dispose_engine, get_engine

    engine_a = get_engine()
    async with engine_a.connect() as conn:
        await conn.execute(text("SELECT 1"))

    await dispose_engine()
    assert session_module.get_engine.cache_info().currsize == 0

    engine_b = get_engine()
    assert engine_b is not engine_a
    async with engine_b.connect() as conn:
        await conn.execute(text("SELECT 1"))

    await dispose_engine()

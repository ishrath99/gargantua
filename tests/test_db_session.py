"""Unit tests for the async DB engine / session factory.

These tests do **not** connect to Postgres — they verify the singleton
behaviour, URL propagation, and lifecycle helpers using SQLAlchemy's
lazy engine construction.  See ``tests/integration/test_db_session.py``
for the real-network round-trip.
"""

from __future__ import annotations

import pytest
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker


def _reset_session_caches() -> None:
    """Drop any cached engine/factory from a previous test."""
    from gargantua.db import session as session_module

    session_module.get_engine.cache_clear()
    session_module.get_session_factory.cache_clear()


@pytest.fixture(autouse=True)
def _isolate_engine_cache() -> None:
    """Make every test start from a clean slate."""
    _reset_session_caches()
    yield
    _reset_session_caches()


def test_get_engine_returns_async_engine() -> None:
    from gargantua.db.session import get_engine

    engine = get_engine()
    assert isinstance(engine, AsyncEngine)


def test_get_engine_is_cached_singleton() -> None:
    from gargantua.db.session import get_engine

    first = get_engine()
    second = get_engine()
    assert first is second


def test_get_engine_uses_settings_async_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL_ASYNC",
        "postgresql+psycopg://u:p@db.example.com:5433/myapp",
    )
    from gargantua.db.session import get_engine

    engine = get_engine()
    # SQLAlchemy stores the URL on the sync_engine.url; for AsyncEngine it
    # surfaces as .url too.
    url = make_url(str(engine.url))
    assert url.host == "db.example.com"
    assert url.port == 5433
    assert url.database == "myapp"
    assert url.drivername == "postgresql+psycopg"


def test_cache_clear_rebuilds_engine_with_new_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gargantua.db.session import get_engine

    monkeypatch.setenv(
        "DATABASE_URL_ASYNC", "postgresql+psycopg://u:p@a.example.com:5432/a"
    )
    first = get_engine()
    assert make_url(str(first.url)).host == "a.example.com"

    # Simulate a settings rotation: clear both caches and re-read env.
    _reset_session_caches()
    from gargantua.settings import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv(
        "DATABASE_URL_ASYNC", "postgresql+psycopg://u:p@b.example.com:5432/b"
    )

    second = get_engine()
    assert second is not first
    assert make_url(str(second.url)).host == "b.example.com"


def test_get_session_factory_returns_async_sessionmaker() -> None:
    from gargantua.db.session import get_session_factory

    factory = get_session_factory()
    assert isinstance(factory, async_sessionmaker)


def test_get_session_factory_is_singleton() -> None:
    from gargantua.db.session import get_session_factory

    assert get_session_factory() is get_session_factory()


def test_get_session_factory_is_bound_to_engine() -> None:
    from gargantua.db.session import get_engine, get_session_factory

    engine = get_engine()
    factory = get_session_factory()
    session = factory()
    try:
        assert session.bind is engine
    finally:
        # AsyncSession.close() is async; we just drop the ref — no I/O has
        # happened yet because we never awaited anything.
        del session


def test_get_session_factory_does_not_expire_on_commit() -> None:
    """``expire_on_commit=False`` is required so route handlers can read
    attributes off freshly-committed ORM objects without a second SELECT."""
    from gargantua.db.session import get_session_factory

    factory = get_session_factory()
    # async_sessionmaker stores its kw in .kw on SQLAlchemy 2.x.
    assert factory.kw.get("expire_on_commit") is False


async def test_get_session_is_async_generator_yielding_async_session() -> None:
    from gargantua.db.session import get_session

    gen = get_session()
    try:
        session = await anext(gen)
        assert isinstance(session, AsyncSession)
    finally:
        # Drain the generator so the ``async with`` inside ``get_session``
        # runs its ``__aexit__`` and releases the session.
        with pytest.raises(StopAsyncIteration):
            await anext(gen)


async def test_dispose_engine_is_noop_when_engine_never_built() -> None:
    """Calling ``dispose_engine()`` from lifespan shutdown must be safe
    even on a process that never opened a DB connection (e.g. CLI mode)."""
    from gargantua.db import session as session_module
    from gargantua.db.session import dispose_engine

    # Sanity check: caches are empty thanks to the autouse fixture.
    assert session_module.get_engine.cache_info().currsize == 0

    await dispose_engine()  # must not raise

    # Still empty afterwards — no engine was lazily built by dispose().
    assert session_module.get_engine.cache_info().currsize == 0


async def test_dispose_engine_disposes_built_engine_and_clears_caches() -> None:
    from gargantua.db import session as session_module
    from gargantua.db.session import dispose_engine, get_engine, get_session_factory

    # Build both caches by calling the singletons.
    engine = get_engine()
    _ = get_session_factory()
    assert session_module.get_engine.cache_info().currsize == 1
    assert session_module.get_session_factory.cache_info().currsize == 1

    await dispose_engine()

    # Both caches were dropped — a subsequent call would rebuild them.
    assert session_module.get_engine.cache_info().currsize == 0
    assert session_module.get_session_factory.cache_info().currsize == 0
    # The disposed engine reference still works structurally (we only check
    # .url here because actually using it would require a network round-trip).
    assert str(engine.url).startswith("postgresql+psycopg://")

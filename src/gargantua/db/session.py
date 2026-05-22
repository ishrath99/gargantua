"""Async DB engine + session factory + FastAPI dependency.

The engine is a process-wide singleton, built lazily on first use and
disposed in the FastAPI lifespan shutdown hook.  Wrapping the constructor in
:func:`functools.lru_cache` gives us, in one line:

* a single connection-pool per process (no accidental fan-out);
* trivial test isolation via :meth:`cache_clear` between tests;
* lazy construction, so ``import gargantua.db.session`` is cheap and does
  not require a reachable Postgres at import-time.

Pool sizing follows SQLAlchemy defaults (pool_size=5, max_overflow=10).  Tune
via the ``database_url_async`` query string once we see real load; e.g.
``?pool_size=20&max_overflow=20``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from gargantua.settings import get_settings


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """Return the process-wide :class:`AsyncEngine`, building it on first call.

    Idempotent: subsequent calls hand back the cached instance until
    :func:`dispose_engine` (or :meth:`get_engine.cache_clear`) wipes the slot.
    """
    settings = get_settings()
    return create_async_engine(
        settings.database_url_async,
        pool_pre_ping=True,
        future=True,
    )


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide :class:`async_sessionmaker` bound to ``get_engine()``.

    ``expire_on_commit=False`` is non-negotiable: route handlers routinely
    read ORM attributes off freshly-committed objects, and the default
    behaviour would trigger an extra SELECT (or a ``DetachedInstanceError``
    once the session closes).
    """
    return async_sessionmaker(
        bind=get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency — yields one :class:`AsyncSession` per request.

    The ``async with`` block guarantees the session is closed (and its
    connection returned to the pool) when the request handler completes.
    Uncommitted writes are rolled back implicitly by the AsyncSession's
    own context manager.

    Usage::

        @router.get("/me")
        async def me(
            session: Annotated[AsyncSession, Depends(get_session)],
        ) -> UserOut:
            ...
    """
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def dispose_engine() -> None:
    """Tear down the cached engine and drop both singleton caches.

    Safe to call from the FastAPI lifespan shutdown hook even on a process
    that never opened a DB connection (e.g. when the app crashes during
    startup before any handler runs).
    """
    if get_engine.cache_info().currsize == 0:
        # Nothing to dispose, and crucially: do not call ``get_engine()``
        # here — that would *build* an engine on the way to throwing it away.
        return
    engine = get_engine()
    await engine.dispose()
    get_engine.cache_clear()
    get_session_factory.cache_clear()

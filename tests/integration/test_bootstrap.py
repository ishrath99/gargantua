"""Bootstrap admin: first-boot admin creation when DB is empty + env is set."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from gargantua.db.models import User


@pytest.fixture
def sync_session_maker(migrated_engine: Engine) -> sessionmaker:
    return sessionmaker(bind=migrated_engine, expire_on_commit=False, future=True)


def _reset_caches() -> None:
    from gargantua.db import session as session_module
    from gargantua.settings import get_settings

    get_settings.cache_clear()
    session_module.get_engine.cache_clear()
    session_module.get_session_factory.cache_clear()


@pytest.fixture
def configured_env(
    monkeypatch: pytest.MonkeyPatch,
    truncate_db: Engine,
    _db_ready: str,
) -> None:
    monkeypatch.setenv("DATABASE_URL_ASYNC", _db_ready)
    _reset_caches()
    yield
    _reset_caches()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_bootstrap_creates_admin_when_db_empty_and_env_set(
    configured_env: None,
    monkeypatch: pytest.MonkeyPatch,
    sync_session_maker: sessionmaker,
) -> None:
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "root")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "s3cret!")

    from gargantua.bootstrap import bootstrap_admin_if_needed
    from gargantua.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        created = await bootstrap_admin_if_needed(session)
    assert created is True

    with sync_session_maker() as s:
        users = s.execute(select(User)).scalars().all()
    assert len(users) == 1
    assert users[0].username == "root"
    assert users[0].role == "admin"


async def test_bootstrap_is_noop_when_users_table_has_rows(
    configured_env: None,
    monkeypatch: pytest.MonkeyPatch,
    sync_session_maker: sessionmaker,
) -> None:
    from gargantua.auth.password import hash_password

    # Seed an existing user so the bootstrap precondition fails.
    with sync_session_maker() as s:
        s.add(
            User(
                username="existing",
                password_hash=hash_password("x"),
                role="user",
            )
        )
        s.commit()

    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "root")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "s3cret!")

    from gargantua.bootstrap import bootstrap_admin_if_needed
    from gargantua.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        created = await bootstrap_admin_if_needed(session)
    assert created is False

    with sync_session_maker() as s:
        usernames = {u.username for u in s.execute(select(User)).scalars().all()}
    assert usernames == {"existing"}


async def test_bootstrap_is_noop_when_env_not_set(
    configured_env: None,
    sync_session_maker: sessionmaker,
) -> None:
    from gargantua.bootstrap import bootstrap_admin_if_needed
    from gargantua.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        created = await bootstrap_admin_if_needed(session)
    assert created is False

    with sync_session_maker() as s:
        users = s.execute(select(User)).scalars().all()
    assert users == []


async def test_bootstrap_is_noop_when_only_username_is_set(
    configured_env: None,
    monkeypatch: pytest.MonkeyPatch,
    sync_session_maker: sessionmaker,
) -> None:
    """Half-configured bootstrap must not create accounts with empty passwords."""
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "root")
    # BOOTSTRAP_ADMIN_PASSWORD intentionally unset.

    from gargantua.bootstrap import bootstrap_admin_if_needed
    from gargantua.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        created = await bootstrap_admin_if_needed(session)
    assert created is False

    with sync_session_maker() as s:
        users = s.execute(select(User)).scalars().all()
    assert users == []


async def test_bootstrap_stored_hash_verifies_with_argon2id(
    configured_env: None,
    monkeypatch: pytest.MonkeyPatch,
    sync_session_maker: sessionmaker,
) -> None:
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "root")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "s3cret!")

    from gargantua.auth.password import verify_password
    from gargantua.bootstrap import bootstrap_admin_if_needed
    from gargantua.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await bootstrap_admin_if_needed(session)

    with sync_session_maker() as s:
        admin = s.execute(select(User).where(User.username == "root")).scalar_one()
    assert admin.password_hash.startswith("$argon2id$")
    assert verify_password("s3cret!", admin.password_hash) is True
    assert verify_password("wrong", admin.password_hash) is False

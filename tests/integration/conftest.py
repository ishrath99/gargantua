"""Fixtures for tests/integration/*.

Every test here needs a real Postgres.  We default to the locally-running
``gargantua-pg`` container (``postgresql+psycopg://ai:pg123@localhost:5432``)
but anything that exposes a Postgres 13+ DSN over ``TEST_DATABASE_URL`` works.

Per-session:
  * ensure the test database exists (created on the maintenance DB if missing)
  * drop and recreate the ``ai`` + ``ai_legacy`` schemas
  * run Alembic ``upgrade head``

Per-test:
  * open a SQLAlchemy session bound to a SAVEPOINT and roll it back at teardown,
    so tests can write freely without polluting each other.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

# ---------------------------------------------------------------------------
# Connection plumbing
# ---------------------------------------------------------------------------

DEFAULT_TEST_DSN = "postgresql+psycopg://ai:pg123@localhost:5432/gargantua_test"
MAINTENANCE_DSN_FALLBACK = "postgresql+psycopg://ai:pg123@localhost:5432/postgres"


def _test_dsn() -> str:
    return os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DSN)


def _maintenance_dsn(test_dsn: str) -> str:
    """Connect string for a DB we know already exists (used for CREATE DATABASE)."""
    # Reuse user/pw/host/port from the test DSN, swap the database for "postgres".
    from sqlalchemy.engine.url import make_url

    url = make_url(test_dsn).set(database="postgres")
    return url.render_as_string(hide_password=False)


def _ensure_database_exists(test_dsn: str) -> None:
    from sqlalchemy.engine.url import make_url

    url = make_url(test_dsn)
    db_name = url.database
    assert db_name, "TEST_DATABASE_URL must include a database name"

    # psycopg uses a raw libpq DSN, not the SA URL — strip the "+psycopg".
    libpq_dsn = (
        f"host={url.host or 'localhost'} port={url.port or 5432} "
        f"user={url.username} password={url.password} dbname=postgres"
    )
    with psycopg.connect(libpq_dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            if cur.fetchone():
                return
            # CREATE DATABASE cannot run in a transaction; autocommit conn handles that.
            cur.execute(f'CREATE DATABASE "{db_name}"')


def _reset_schemas(engine: Engine) -> None:
    """Drop and recreate ``ai`` + ``ai_legacy`` schemas."""
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS ai CASCADE"))
        conn.execute(text("DROP SCHEMA IF EXISTS ai_legacy CASCADE"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        conn.execute(text("CREATE SCHEMA ai"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def test_dsn() -> str:
    """The SQLAlchemy DSN for the test database (env-overridable)."""
    return _test_dsn()


@pytest.fixture(scope="session")
def _db_ready(test_dsn: str) -> str:
    """Ensure the test database exists and is reachable.

    Returns the DSN unchanged once preflight succeeds; skips the session if
    Postgres is unreachable so unit-only test runs aren't blocked.
    """
    try:
        _ensure_database_exists(test_dsn)
    except (psycopg.OperationalError, AssertionError) as exc:
        pytest.skip(f"Postgres not reachable for integration tests: {exc}")
    return test_dsn


@pytest.fixture(scope="session")
def engine(_db_ready: str) -> Iterator[Engine]:
    """Session-scoped synchronous engine bound to the test DB."""
    eng = create_engine(_db_ready, future=True)
    yield eng
    eng.dispose()


@pytest.fixture
def clean_db(engine: Engine) -> Iterator[Engine]:
    """Reset the ``ai``/``ai_legacy`` schemas before each test that needs raw DDL."""
    _reset_schemas(engine)
    yield engine


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """A per-test SQLAlchemy session wrapped in a rolled-back outer transaction."""
    connection = engine.connect()
    trans = connection.begin()
    sm = sessionmaker(bind=connection, expire_on_commit=False, future=True)
    s = sm()
    try:
        yield s
    finally:
        s.close()
        trans.rollback()
        connection.close()


# ---------------------------------------------------------------------------
# Alembic helpers (used by both migration tests and route tests)
# ---------------------------------------------------------------------------


def run_alembic_upgrade(test_dsn: str) -> None:
    """Run ``alembic upgrade head`` against *test_dsn* using the repo config."""
    from alembic import command
    from alembic.config import Config

    repo_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", test_dsn)
    cfg.set_main_option(
        "script_location", str(repo_root / "src" / "gargantua" / "db" / "migrations")
    )
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def migrated_engine(engine: Engine, _db_ready: str) -> Engine:
    """Schema reset + Alembic upgrade, once for the whole test session.

    Use this fixture (instead of ``clean_db``) when the test needs the full
    ``ai.*`` schema in place but does not itself exercise migrations.
    """
    _reset_schemas(engine)
    run_alembic_upgrade(_db_ready)
    return engine


@pytest.fixture
def truncate_db(migrated_engine: Engine) -> Engine:
    """Truncate every ``ai.*`` table before the test runs.

    Cheaper than dropping/recreating the schema; preserves PKs and seqs by
    using ``RESTART IDENTITY CASCADE``.  Add new table names here whenever
    a new table is added to the migration so route tests keep starting clean.
    """
    with migrated_engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE TABLE "
                "ai.audit_log, ai.team, ai.agent, ai.mcp_server_child_resource, "
                "ai.mcp_server, ai.mcp_server_type, ai.users "
                "RESTART IDENTITY CASCADE"
            )
        )
    return migrated_engine

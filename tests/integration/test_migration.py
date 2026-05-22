"""Alembic ``upgrade head`` applies cleanly to a fresh database.

Two scenarios:

  1. Empty database — every new table in ``ai.*`` lands.
  2. Database with pre-existing tables in the ``ai`` schema (the live one) — they
     are moved to ``ai_legacy.*`` before the new schema is created.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from gargantua.db.base import DB_SCHEMA

from tests.integration.conftest import run_alembic_upgrade as _run_alembic_upgrade


EXPECTED_TABLES = {
    "users",
    "mcp_server_type",
    "mcp_server",
    "mcp_server_child_resource",
    "agent",
    "team",
    "audit_log",
    "alembic_version",
}


def _tables_in_schema(engine: Engine, schema: str) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname = :schema"
            ),
            {"schema": schema},
        ).all()
    return {row[0] for row in rows}


def test_upgrade_creates_full_schema_on_empty_db(
    clean_db: Engine, test_dsn: str
) -> None:
    _run_alembic_upgrade(test_dsn)
    tables = _tables_in_schema(clean_db, DB_SCHEMA)
    missing = EXPECTED_TABLES - tables
    assert not missing, f"missing tables after upgrade: {missing}"


def test_upgrade_moves_preexisting_ai_tables_to_ai_legacy(
    clean_db: Engine, test_dsn: str
) -> None:
    # Seed an old-shaped table in ai/ before running the migration.
    with clean_db.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE ai.legacy_table (
                    id   INT PRIMARY KEY,
                    note TEXT
                )
                """
            )
        )
        conn.execute(text("INSERT INTO ai.legacy_table VALUES (1, 'hello')"))

    _run_alembic_upgrade(test_dsn)

    # legacy_table should have moved out of `ai` into `ai_legacy`.
    ai_tables = _tables_in_schema(clean_db, DB_SCHEMA)
    legacy_tables = _tables_in_schema(clean_db, "ai_legacy")

    assert "legacy_table" not in ai_tables
    assert "legacy_table" in legacy_tables

    # Data preserved.
    with clean_db.connect() as conn:
        rows = conn.execute(
            text("SELECT id, note FROM ai_legacy.legacy_table")
        ).all()
    assert rows == [(1, "hello")]

    # New tables still landed.
    assert EXPECTED_TABLES <= ai_tables


def test_upgrade_is_idempotent(clean_db: Engine, test_dsn: str) -> None:
    _run_alembic_upgrade(test_dsn)
    # Second run with everything already present must be a no-op.
    _run_alembic_upgrade(test_dsn)

    tables = _tables_in_schema(clean_db, DB_SCHEMA)
    assert EXPECTED_TABLES <= tables


def test_users_role_check_constraint_rejects_unknown_role(
    clean_db: Engine, test_dsn: str
) -> None:
    import psycopg

    _run_alembic_upgrade(test_dsn)

    with clean_db.begin() as conn:
        with pytest.raises(Exception) as excinfo:
            conn.execute(
                text(
                    "INSERT INTO ai.users (username, password_hash, role) "
                    "VALUES (:u, :p, :r)"
                ),
                {"u": "evil", "p": "x", "r": "superhacker"},
            )
        # Either an IntegrityError or a wrapped psycopg.errors.CheckViolation.
        assert "role" in str(excinfo.value).lower() or "check" in str(excinfo.value).lower()
        _ = psycopg  # keep import for clarity even though we don't catch directly


# ---------------------------------------------------------------------------
# 0002_user_is_active
# ---------------------------------------------------------------------------


def test_upgrade_adds_users_is_active_column(
    clean_db: Engine, test_dsn: str
) -> None:
    """After ``upgrade head``, ``ai.users.is_active`` exists with the right shape."""
    _run_alembic_upgrade(test_dsn)

    with clean_db.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema = 'ai'
                  AND table_name   = 'users'
                  AND column_name  = 'is_active'
                """
            )
        ).first()

    assert row is not None, "is_active column must exist on ai.users"
    data_type, is_nullable, column_default = row
    assert data_type == "boolean"
    assert is_nullable == "NO"
    # Postgres stores boolean defaults as the literal token "true" / "false".
    assert column_default is not None and "true" in column_default.lower()


def test_existing_user_gets_is_active_true_after_upgrade(
    clean_db: Engine, test_dsn: str
) -> None:
    """A user inserted before 0002 ran must end up active, not inactive.

    Simulates an upgrade against a live deployment with existing rows.
    """
    # Stand up the schema at revision 0001 first.
    from alembic import command
    from alembic.config import Config
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", test_dsn)
    cfg.set_main_option(
        "script_location", str(repo_root / "src" / "gargantua" / "db" / "migrations")
    )
    command.upgrade(cfg, "0001_initial_schema")

    with clean_db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ai.users (username, password_hash, role) "
                "VALUES ('legacy', 'x', 'user')"
            )
        )

    # Now upgrade the rest of the way.
    command.upgrade(cfg, "head")

    with clean_db.connect() as conn:
        is_active = conn.execute(
            text("SELECT is_active FROM ai.users WHERE username = 'legacy'")
        ).scalar_one()
    assert is_active is True


def test_downgrade_drops_is_active_column(clean_db: Engine, test_dsn: str) -> None:
    """Reversing 0002 removes the column (so the migration is bidirectional)."""
    from alembic import command
    from alembic.config import Config
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", test_dsn)
    cfg.set_main_option(
        "script_location", str(repo_root / "src" / "gargantua" / "db" / "migrations")
    )
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0001_initial_schema")

    with clean_db.connect() as conn:
        present = conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'ai' "
                "AND table_name = 'users' "
                "AND column_name = 'is_active'"
            )
        ).first()
    assert present is None, "downgrade should drop is_active"

    # Critical: restore the schema to ``head`` so the session-scoped
    # ``migrated_engine`` fixture (used by all the other integration
    # tests) doesn't observe a half-migrated database.  ``clean_db`` only
    # nukes the ``ai`` schema between tests; it doesn't reapply Alembic.
    command.upgrade(cfg, "head")

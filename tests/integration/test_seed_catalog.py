"""``gargantua-admin seed-catalog`` exit-code + idempotency contract.

Three core behaviours are pinned here:

1. On an empty catalog, all canonical types are inserted, each with a
   ``mcp_server_type.create`` audit row attributed to the system.
2. Re-running with no flags is a no-op (no inserts, no updates, no
   audit rows).
3. ``--overwrite`` updates fields that drifted from the canonical
   definition and writes ``mcp_server_type.update`` audit rows; it does
   **not** touch rows whose fields already match.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

from gargantua.db.models import AuditLog, MCPServerType


@pytest.fixture
def cli_env(
    monkeypatch: pytest.MonkeyPatch,
    truncate_db: Engine,
    _db_ready: str,
) -> Iterator[None]:
    """Wire the CLI's sync engine to the test DB; reset on exit."""
    monkeypatch.setenv("DATABASE_URL", _db_ready)

    from gargantua.cli_admin import _reset_engine
    from gargantua.settings import get_settings

    get_settings.cache_clear()
    _reset_engine()
    yield
    _reset_engine()
    get_settings.cache_clear()


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def sync_session_maker(migrated_engine: Engine) -> sessionmaker:
    return sessionmaker(bind=migrated_engine, expire_on_commit=False, future=True)


def _canonical_slugs() -> set[str]:
    from gargantua.catalog_seed import CANONICAL_TYPES

    return {t["slug"] for t in CANONICAL_TYPES}


# ---------------------------------------------------------------------------
# Fresh-DB seed
# ---------------------------------------------------------------------------


def test_seed_inserts_all_canonical_types(runner: CliRunner, cli_env, sync_session_maker) -> None:
    from gargantua.admin import app

    result = runner.invoke(app, ["seed-catalog"])
    assert result.exit_code == 0, result.stdout
    assert "inserted" in result.stdout.lower()

    expected = _canonical_slugs()
    with sync_session_maker() as s:
        slugs = {row.slug for row in s.execute(select(MCPServerType)).scalars().all()}
    assert slugs == expected


def test_seed_writes_one_audit_row_per_insert(
    runner: CliRunner, cli_env, sync_session_maker
) -> None:
    from gargantua.admin import app

    runner.invoke(app, ["seed-catalog"])

    with sync_session_maker() as s:
        creates = (
            s.execute(select(AuditLog).where(AuditLog.action == "mcp_server_type.create"))
            .scalars()
            .all()
        )
    assert len(creates) == len(_canonical_slugs())
    # System-driven actions: no actor.
    assert all(a.actor_id is None for a in creates)
    # before is null on create.
    assert all(a.before is None for a in creates)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_second_seed_run_is_a_noop_without_overwrite(
    runner: CliRunner, cli_env, sync_session_maker
) -> None:
    from gargantua.admin import app

    runner.invoke(app, ["seed-catalog"])
    second = runner.invoke(app, ["seed-catalog"])
    assert second.exit_code == 0, second.stdout
    assert "0 inserted" in second.stdout.lower()
    assert "0 updated" in second.stdout.lower()

    expected = _canonical_slugs()
    with sync_session_maker() as s:
        # No duplicate inserts.
        slugs = [row.slug for row in s.execute(select(MCPServerType)).scalars().all()]
        assert sorted(slugs) == sorted(expected)
        # No second wave of create audits.
        creates = (
            s.execute(select(AuditLog).where(AuditLog.action == "mcp_server_type.create"))
            .scalars()
            .all()
        )
        assert len(creates) == len(expected)


def test_seed_run_skips_existing_when_canonical_drift_unchanged(
    runner: CliRunner,
    cli_env,
    sync_session_maker,
) -> None:
    """Operator may have created one of the canonical slugs manually; we
    refuse to clobber it unless --overwrite is set."""
    from gargantua.admin import app
    from gargantua.catalog_seed import CANONICAL_TYPES

    target = CANONICAL_TYPES[0]["slug"]
    with sync_session_maker() as s:
        s.add(
            MCPServerType(
                slug=target,
                name="Operator-customized name",
                mode="stdio",
            )
        )
        s.commit()

    result = runner.invoke(app, ["seed-catalog"])
    assert result.exit_code == 0

    with sync_session_maker() as s:
        row = s.execute(select(MCPServerType).where(MCPServerType.slug == target)).scalar_one()
    # Operator's custom name preserved — seed-catalog did not overwrite.
    assert row.name == "Operator-customized name"


# ---------------------------------------------------------------------------
# --overwrite
# ---------------------------------------------------------------------------


def test_seed_overwrite_updates_drifted_rows(
    runner: CliRunner,
    cli_env,
    sync_session_maker,
) -> None:
    from gargantua.admin import app
    from gargantua.catalog_seed import CANONICAL_TYPES

    target = CANONICAL_TYPES[0]
    with sync_session_maker() as s:
        s.add(
            MCPServerType(
                slug=target["slug"],
                name="Drifted name",
                mode=target["mode"],
            )
        )
        s.commit()

    result = runner.invoke(app, ["seed-catalog", "--overwrite"])
    assert result.exit_code == 0, result.stdout
    assert "updated" in result.stdout.lower()

    with sync_session_maker() as s:
        row = s.execute(
            select(MCPServerType).where(MCPServerType.slug == target["slug"])
        ).scalar_one()
        assert row.name == target["name"]  # canonical value restored

        audits = (
            s.execute(select(AuditLog).where(AuditLog.action == "mcp_server_type.update"))
            .scalars()
            .all()
        )
    assert len(audits) == 1
    assert audits[0].before["name"] == "Drifted name"
    assert audits[0].after["name"] == target["name"]


def test_seed_overwrite_skips_rows_already_in_canonical_state(
    runner: CliRunner,
    cli_env,
    sync_session_maker,
) -> None:
    """--overwrite must NOT write update audits for rows already in sync."""
    from gargantua.admin import app

    runner.invoke(app, ["seed-catalog"])  # baseline seed
    result = runner.invoke(app, ["seed-catalog", "--overwrite"])
    assert result.exit_code == 0

    with sync_session_maker() as s:
        updates = (
            s.execute(select(AuditLog).where(AuditLog.action == "mcp_server_type.update"))
            .scalars()
            .all()
        )
    assert updates == []

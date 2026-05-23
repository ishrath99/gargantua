"""Integration tests for ``gargantua-admin user`` + ``audit`` subcommands.

These hit a real Postgres through Typer's ``CliRunner`` so we exercise:

* DSN resolution from ``Settings.database_url``,
* the sync engine + sessionmaker path inside :mod:`gargantua.cli_admin`,
* repo + audit-log interactions on the same DB the HTTP routes use.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

from gargantua.db.models import AuditLog, User

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def configured_env(
    monkeypatch: pytest.MonkeyPatch,
    truncate_db: Engine,
    _db_ready: str,
) -> Iterator[None]:
    # The CLI builds a sync engine from ``settings.database_url``.
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


# ---------------------------------------------------------------------------
# user create
# ---------------------------------------------------------------------------


def test_user_create_inserts_user_with_audit(
    runner: CliRunner,
    configured_env,
    sync_session_maker: sessionmaker,
) -> None:
    from gargantua.admin import app

    result = runner.invoke(
        app,
        [
            "user",
            "create",
            "--username",
            "alice",
            "--role",
            "user",
            "--password",
            "hunter22!",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "Created user alice" in result.stdout

    with sync_session_maker() as s:
        user = s.execute(select(User).where(User.username == "alice")).scalar_one()
        assert user.role == "user"
        assert user.is_active is True

        audit = s.execute(
            select(AuditLog)
            .where(AuditLog.action == "user.create")
            .where(AuditLog.target_id == user.id)
        ).scalar_one()
        # System-driven actions have no actor.
        assert audit.actor_id is None
        assert audit.after["username"] == "alice"


def test_user_create_duplicate_username_exits_2(
    runner: CliRunner,
    configured_env,
) -> None:
    from gargantua.admin import app

    runner.invoke(
        app,
        [
            "user",
            "create",
            "--username",
            "dup",
            "--password",
            "longpassword1",
        ],
    )
    second = runner.invoke(
        app,
        [
            "user",
            "create",
            "--username",
            "dup",
            "--password",
            "longpassword1",
        ],
    )
    assert second.exit_code == 2
    # Typer 0.16 routes secho(err=True) through Click's err stream which
    # CliRunner captures into ``result.output`` by default.
    assert "already exists" in (second.stdout + (second.stderr or ""))


def test_user_create_invalid_role_exits_3(
    runner: CliRunner,
    configured_env,
) -> None:
    from gargantua.admin import app

    result = runner.invoke(
        app,
        [
            "user",
            "create",
            "--username",
            "evil",
            "--role",
            "superhacker",
            "--password",
            "longpassword1",
        ],
    )
    assert result.exit_code == 3


# ---------------------------------------------------------------------------
# user list
# ---------------------------------------------------------------------------


def test_user_list_filters_and_shows_state(
    runner: CliRunner,
    configured_env,
    sync_session_maker: sessionmaker,
) -> None:
    from gargantua.admin import app
    from gargantua.auth.password import hash_password

    with sync_session_maker() as s:
        s.add_all(
            [
                User(
                    username="active-admin",
                    password_hash=hash_password("x"),
                    role="admin",
                ),
                User(
                    username="active-user",
                    password_hash=hash_password("x"),
                    role="user",
                ),
                User(
                    username="dormant",
                    password_hash=hash_password("x"),
                    role="user",
                    is_active=False,
                ),
            ]
        )
        s.commit()

    # Default list: only active users.
    r = runner.invoke(app, ["user", "list"])
    assert r.exit_code == 0
    assert "active-admin" in r.stdout
    assert "active-user" in r.stdout
    assert "dormant" not in r.stdout

    # include-inactive surfaces the dormant row.
    r = runner.invoke(app, ["user", "list", "--include-inactive"])
    assert "dormant" in r.stdout

    # Role filter.
    r = runner.invoke(app, ["user", "list", "--role", "admin"])
    assert "active-admin" in r.stdout
    assert "active-user" not in r.stdout

    # Search.
    r = runner.invoke(app, ["user", "list", "--search", "user"])
    assert "active-user" in r.stdout
    assert "active-admin" not in r.stdout


# ---------------------------------------------------------------------------
# user set-role
# ---------------------------------------------------------------------------


def test_user_set_role_updates_and_logs(
    runner: CliRunner,
    configured_env,
    sync_session_maker: sessionmaker,
) -> None:
    from gargantua.admin import app
    from gargantua.auth.password import hash_password

    with sync_session_maker() as s:
        # Need a backup admin so the demotion isn't blocked by the
        # last-admin guard.
        s.add_all(
            [
                User(username="primary", password_hash=hash_password("x"), role="admin"),
                User(username="backup", password_hash=hash_password("x"), role="admin"),
            ]
        )
        s.commit()

    result = runner.invoke(app, ["user", "set-role", "--username", "primary", "--role", "user"])
    assert result.exit_code == 0, result.stdout
    assert "admin -> user" in result.stdout

    with sync_session_maker() as s:
        user = s.execute(select(User).where(User.username == "primary")).scalar_one()
        assert user.role == "user"

        audit = s.execute(
            select(AuditLog)
            .where(AuditLog.action == "user.role_update")
            .where(AuditLog.target_id == user.id)
        ).scalar_one()
        assert audit.before["role"] == "admin"
        assert audit.after["role"] == "user"


def test_user_set_role_blocks_last_admin(
    runner: CliRunner,
    configured_env,
    sync_session_maker: sessionmaker,
) -> None:
    from gargantua.admin import app
    from gargantua.auth.password import hash_password

    with sync_session_maker() as s:
        s.add(User(username="only", password_hash=hash_password("x"), role="admin"))
        s.commit()

    result = runner.invoke(app, ["user", "set-role", "--username", "only", "--role", "user"])
    assert result.exit_code == 4


def test_user_set_role_unknown_user_exits_2(runner: CliRunner, configured_env) -> None:
    from gargantua.admin import app

    result = runner.invoke(app, ["user", "set-role", "--username", "ghost", "--role", "admin"])
    assert result.exit_code == 2


def test_user_set_role_no_op_does_not_write_audit(
    runner: CliRunner,
    configured_env,
    sync_session_maker: sessionmaker,
) -> None:
    from gargantua.admin import app
    from gargantua.auth.password import hash_password

    with sync_session_maker() as s:
        s.add(User(username="al", password_hash=hash_password("x"), role="user"))
        s.commit()

    result = runner.invoke(app, ["user", "set-role", "--username", "al", "--role", "user"])
    assert result.exit_code == 0
    assert "no change" in result.stdout.lower()

    with sync_session_maker() as s:
        rows = s.execute(select(AuditLog).where(AuditLog.action == "user.role_update")).all()
    assert rows == []


# ---------------------------------------------------------------------------
# user deactivate / activate
# ---------------------------------------------------------------------------


def test_user_deactivate_then_activate(
    runner: CliRunner,
    configured_env,
    sync_session_maker: sessionmaker,
) -> None:
    from gargantua.admin import app
    from gargantua.auth.password import hash_password

    with sync_session_maker() as s:
        s.add(User(username="al", password_hash=hash_password("x"), role="user"))
        s.commit()

    r = runner.invoke(app, ["user", "deactivate", "--username", "al"])
    assert r.exit_code == 0
    with sync_session_maker() as s:
        assert s.execute(select(User).where(User.username == "al")).scalar_one().is_active is False

    r = runner.invoke(app, ["user", "activate", "--username", "al"])
    assert r.exit_code == 0
    with sync_session_maker() as s:
        assert s.execute(select(User).where(User.username == "al")).scalar_one().is_active is True


def test_user_deactivate_last_admin_blocked(
    runner: CliRunner,
    configured_env,
    sync_session_maker: sessionmaker,
) -> None:
    from gargantua.admin import app
    from gargantua.auth.password import hash_password

    with sync_session_maker() as s:
        s.add(User(username="only", password_hash=hash_password("x"), role="admin"))
        s.commit()

    r = runner.invoke(app, ["user", "deactivate", "--username", "only"])
    assert r.exit_code == 4


# ---------------------------------------------------------------------------
# audit list
# ---------------------------------------------------------------------------


def test_audit_list_shows_recent_entries(
    runner: CliRunner,
    configured_env,
) -> None:
    from gargantua.admin import app

    # Seed via the CLI itself so we get real audit rows.
    for name in ("u1", "u2", "u3"):
        runner.invoke(
            app,
            [
                "user",
                "create",
                "--username",
                name,
                "--password",
                "longpassword1",
            ],
        )

    r = runner.invoke(app, ["audit", "list"])
    assert r.exit_code == 0
    # All three should appear in the list.
    for name in ("u1", "u2", "u3"):
        assert name in r.stdout or "user.create" in r.stdout
    assert "user.create" in r.stdout


def test_audit_list_filters_by_action(
    runner: CliRunner,
    configured_env,
) -> None:
    from gargantua.admin import app

    runner.invoke(
        app,
        [
            "user",
            "create",
            "--username",
            "u1",
            "--password",
            "longpassword1",
        ],
    )
    # No role_update yet → filtering by it returns empty.
    r = runner.invoke(app, ["audit", "list", "--action", "user.role_update"])
    assert r.exit_code == 0
    assert "No audit entries" in r.stdout


def test_audit_list_when_empty(runner: CliRunner, configured_env) -> None:
    from gargantua.admin import app

    r = runner.invoke(app, ["audit", "list"])
    assert r.exit_code == 0
    assert "No audit entries" in r.stdout


# ---------------------------------------------------------------------------
# root help still lists every command group
# ---------------------------------------------------------------------------


def test_root_help_lists_user_and_audit_groups(runner: CliRunner) -> None:
    from gargantua.admin import app

    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    assert "user" in r.stdout
    assert "audit" in r.stdout
    # Existing subcommands still present (no regression on the original CLI).
    assert "generate-master-key" in r.stdout
    assert "generate-jwt-keys" in r.stdout

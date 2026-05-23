"""KEK rotation end-to-end: helper round-trip + CLI exit codes.

Exercises both :func:`gargantua.rotation.rotate_all_secrets` (the
in-process worker) and ``gargantua-admin rotate-kek`` (the operator
surface).  Each test seeds real ciphertext into the DB under one KEK,
runs a rotation to another KEK, and asserts the rows now decrypt under
the new KEK and not the old.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from gargantua.db.models import MCPServer, MCPServerChildResource, MCPServerType
from gargantua.secrets import (
    decrypt_json_with_kek,
    encrypt_json_with_kek,
    kek_fingerprint,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_KEY_OLD = b"\x00" * 32  # deliberately predictable so tests are self-describing
_KEY_NEW = b"\xff" * 32


@pytest.fixture
def sync_session_maker(truncate_db: Engine) -> sessionmaker:
    return sessionmaker(bind=truncate_db, expire_on_commit=False, future=True)


@pytest.fixture
def cli_env(
    monkeypatch: pytest.MonkeyPatch,
    truncate_db: Engine,
    _db_ready: str,
) -> Iterator[None]:
    """Point the CLI's sync engine at the test DB; reset on exit."""
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


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_server_type(s: Session) -> MCPServerType:
    t = MCPServerType(
        slug="postgres",
        name="Postgres MCP",
        mode="stdio",
        default_command="uvx",
        default_args=["postgres-mcp"],
    )
    s.add(t)
    s.flush()
    return t


def _seed_server_with_env(
    s: Session, *, type_id, name: str, env: dict[str, str], kek: bytes
) -> MCPServer:
    ct, iv, kek_id = encrypt_json_with_kek(env, kek)
    server = MCPServer(
        type_id=type_id,
        name=name,
        env_tag="prod",
        env_vars=ct,
        env_var_iv=iv,
        env_var_kek_id=kek_id,
    )
    s.add(server)
    s.flush()
    return server


def _seed_child_with_headers(
    s: Session,
    *,
    parent_id,
    name: str,
    headers: dict[str, str],
    kek: bytes,
) -> MCPServerChildResource:
    ct, iv, kek_id = encrypt_json_with_kek(headers, kek)
    child = MCPServerChildResource(
        parent_mcp_server_id=parent_id,
        type="swagger",
        name=name,
        url="https://example.com/swagger.json",
        headers=ct,
        headers_iv=iv,
        headers_kek_id=kek_id,
    )
    s.add(child)
    s.flush()
    return child


# ---------------------------------------------------------------------------
# Worker tests (gargantua.rotation.rotate_all_secrets)
# ---------------------------------------------------------------------------


def test_rotate_all_secrets_re_encrypts_rows(sync_session_maker) -> None:
    from gargantua.rotation import rotate_all_secrets

    with sync_session_maker() as s:
        t = _seed_server_type(s)
        srv = _seed_server_with_env(
            s,
            type_id=t.id,
            name="db-prod",
            env={"DATABASE_URI": "postgres://...", "API_KEY": "secret1"},
            kek=_KEY_OLD,
        )
        child = _seed_child_with_headers(
            s,
            parent_id=srv.id,
            name="docs",
            headers={"Authorization": "Bearer xyz"},
            kek=_KEY_OLD,
        )
        s.commit()
        server_id, child_id = srv.id, child.id

    with sync_session_maker() as s:
        report = rotate_all_secrets(s, from_key=_KEY_OLD, to_key=_KEY_NEW, dry_run=False)
        s.commit()

    assert report.mcp_server_rotated == 1
    assert report.child_resource_rotated == 1
    assert report.dry_run is False

    # Rows now decrypt under the new KEK, not the old one.
    with sync_session_maker() as s:
        srv = s.get(MCPServer, server_id)
        child = s.get(MCPServerChildResource, child_id)

    assert srv.env_var_kek_id == kek_fingerprint(_KEY_NEW)
    assert decrypt_json_with_kek(srv.env_vars, srv.env_var_iv, _KEY_NEW) == {
        "DATABASE_URI": "postgres://...",
        "API_KEY": "secret1",
    }
    assert child.headers_kek_id == kek_fingerprint(_KEY_NEW)
    assert decrypt_json_with_kek(child.headers, child.headers_iv, _KEY_NEW) == {
        "Authorization": "Bearer xyz"
    }

    # Old KEK no longer decrypts the rotated rows.
    with pytest.raises(Exception):
        decrypt_json_with_kek(srv.env_vars, srv.env_var_iv, _KEY_OLD)


def test_rotate_all_secrets_dry_run_changes_nothing(sync_session_maker) -> None:
    from gargantua.rotation import rotate_all_secrets

    with sync_session_maker() as s:
        t = _seed_server_type(s)
        srv = _seed_server_with_env(
            s,
            type_id=t.id,
            name="db-prod",
            env={"DATABASE_URI": "postgres://..."},
            kek=_KEY_OLD,
        )
        s.commit()
        server_id = srv.id
        original_iv = srv.env_var_iv
        original_ct = srv.env_vars

    with sync_session_maker() as s:
        report = rotate_all_secrets(s, from_key=_KEY_OLD, to_key=_KEY_NEW, dry_run=True)
        s.commit()

    assert report.dry_run is True
    assert report.mcp_server_rotated == 1  # what *would* be rotated

    with sync_session_maker() as s:
        srv = s.get(MCPServer, server_id)
    assert srv.env_var_kek_id == kek_fingerprint(_KEY_OLD)
    assert srv.env_vars == original_ct
    assert srv.env_var_iv == original_iv


def test_rotate_all_secrets_is_idempotent(sync_session_maker) -> None:
    """Running the same rotation twice rotates zero rows the second time."""
    from gargantua.rotation import rotate_all_secrets

    with sync_session_maker() as s:
        t = _seed_server_type(s)
        _seed_server_with_env(
            s,
            type_id=t.id,
            name="db-prod",
            env={"DATABASE_URI": "postgres://..."},
            kek=_KEY_OLD,
        )
        s.commit()

    with sync_session_maker() as s:
        rotate_all_secrets(s, from_key=_KEY_OLD, to_key=_KEY_NEW, dry_run=False)
        s.commit()

    with sync_session_maker() as s:
        report = rotate_all_secrets(s, from_key=_KEY_OLD, to_key=_KEY_NEW, dry_run=False)
        s.commit()

    assert report.mcp_server_rotated == 0
    assert report.mcp_server_skipped_already_new == 1


def test_rotate_all_secrets_skips_empty_rows(sync_session_maker) -> None:
    """A row with no ciphertext (operator hasn't supplied creds yet) is left alone."""
    from gargantua.rotation import rotate_all_secrets

    with sync_session_maker() as s:
        t = _seed_server_type(s)
        s.add(
            MCPServer(
                type_id=t.id,
                name="empty",
                env_tag="prod",
                # env_vars/env_var_iv/env_var_kek_id all NULL
            )
        )
        s.commit()

    with sync_session_maker() as s:
        report = rotate_all_secrets(s, from_key=_KEY_OLD, to_key=_KEY_NEW, dry_run=False)
        s.commit()

    assert report.mcp_server_rotated == 0
    assert report.mcp_server_skipped_empty == 1


def test_rotate_all_secrets_raises_on_unknown_kek_id(sync_session_maker) -> None:
    """A row encrypted under a third KEK aborts the rotation before any writes."""
    from gargantua.rotation import rotate_all_secrets
    from gargantua.secrets import KekMismatch

    third_key = b"\x42" * 32
    with sync_session_maker() as s:
        t = _seed_server_type(s)
        _seed_server_with_env(
            s,
            type_id=t.id,
            name="orphan",
            env={"X": "y"},
            kek=third_key,
        )
        s.commit()

    with sync_session_maker() as s:
        with pytest.raises(KekMismatch):
            rotate_all_secrets(s, from_key=_KEY_OLD, to_key=_KEY_NEW, dry_run=False)
        s.rollback()


def test_rotate_all_secrets_rejects_identical_keys(sync_session_maker) -> None:
    from gargantua.rotation import rotate_all_secrets

    with sync_session_maker() as s:
        with pytest.raises(ValueError):
            rotate_all_secrets(s, from_key=_KEY_OLD, to_key=_KEY_OLD, dry_run=False)


def test_rotate_all_secrets_rejects_inconsistent_row(sync_session_maker) -> None:
    """Two of the three secret columns set, one NULL → refuse to guess."""
    from gargantua.rotation import rotate_all_secrets
    from gargantua.secrets import InvalidMasterKey

    with sync_session_maker() as s:
        t = _seed_server_type(s)
        s.add(
            MCPServer(
                type_id=t.id,
                name="weird",
                env_tag="prod",
                env_vars=b"some bytes",
                env_var_iv=b"\x00" * 12,
                # env_var_kek_id intentionally NULL
            )
        )
        s.commit()

    with sync_session_maker() as s:
        with pytest.raises(InvalidMasterKey):
            rotate_all_secrets(s, from_key=_KEY_OLD, to_key=_KEY_NEW, dry_run=False)


# ---------------------------------------------------------------------------
# CLI tests (gargantua-admin rotate-kek)
# ---------------------------------------------------------------------------


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def test_cli_rotate_kek_rotates_rows(
    runner: CliRunner,
    cli_env,
    sync_session_maker: sessionmaker,
) -> None:
    from gargantua.admin import app

    with sync_session_maker() as s:
        t = _seed_server_type(s)
        srv = _seed_server_with_env(
            s,
            type_id=t.id,
            name="db",
            env={"DATABASE_URI": "postgres://..."},
            kek=_KEY_OLD,
        )
        s.commit()
        server_id = srv.id

    result = runner.invoke(
        app,
        [
            "rotate-kek",
            "--from-key",
            _b64(_KEY_OLD),
            "--to-key",
            _b64(_KEY_NEW),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "rotated 1" in result.stdout

    with sync_session_maker() as s:
        srv = s.get(MCPServer, server_id)
    assert srv.env_var_kek_id == kek_fingerprint(_KEY_NEW)
    assert decrypt_json_with_kek(srv.env_vars, srv.env_var_iv, _KEY_NEW) == {
        "DATABASE_URI": "postgres://..."
    }


def test_cli_rotate_kek_dry_run_does_not_write(
    runner: CliRunner,
    cli_env,
    sync_session_maker: sessionmaker,
) -> None:
    from gargantua.admin import app

    with sync_session_maker() as s:
        t = _seed_server_type(s)
        srv = _seed_server_with_env(
            s,
            type_id=t.id,
            name="db",
            env={"DATABASE_URI": "postgres://..."},
            kek=_KEY_OLD,
        )
        s.commit()
        server_id = srv.id

    result = runner.invoke(
        app,
        [
            "rotate-kek",
            "--from-key",
            _b64(_KEY_OLD),
            "--to-key",
            _b64(_KEY_NEW),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "would rotate 1" in result.stdout

    with sync_session_maker() as s:
        srv = s.get(MCPServer, server_id)
    # Still on the old KEK — dry run is purely informative.
    assert srv.env_var_kek_id == kek_fingerprint(_KEY_OLD)


def test_cli_rotate_kek_rejects_bad_base64(runner: CliRunner, cli_env) -> None:
    from gargantua.admin import app

    result = runner.invoke(
        app,
        [
            "rotate-kek",
            "--from-key",
            "not!base64",
            "--to-key",
            _b64(_KEY_NEW),
        ],
    )
    assert result.exit_code == 2


def test_cli_rotate_kek_rejects_short_key(runner: CliRunner, cli_env) -> None:
    from gargantua.admin import app

    short = base64.b64encode(b"\x00" * 16).decode("ascii")  # 16 bytes ≠ 32
    result = runner.invoke(
        app,
        [
            "rotate-kek",
            "--from-key",
            short,
            "--to-key",
            _b64(_KEY_NEW),
        ],
    )
    assert result.exit_code == 2


def test_cli_rotate_kek_exits_3_on_unknown_kek_id(
    runner: CliRunner,
    cli_env,
    sync_session_maker: sessionmaker,
) -> None:
    """A row in an unknown KEK must surface as a clean non-zero exit."""
    from gargantua.admin import app

    third_key = b"\x42" * 32
    with sync_session_maker() as s:
        t = _seed_server_type(s)
        _seed_server_with_env(
            s,
            type_id=t.id,
            name="orphan",
            env={"X": "y"},
            kek=third_key,
        )
        s.commit()

    result = runner.invoke(
        app,
        [
            "rotate-kek",
            "--from-key",
            _b64(_KEY_OLD),
            "--to-key",
            _b64(_KEY_NEW),
        ],
    )
    assert result.exit_code == 3

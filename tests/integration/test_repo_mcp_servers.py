"""Repo layer for ``gargantua_app.mcp_server``.

Focus: the parts that aren't covered by the HTTP integration tests —
encryption round-trip, KekMismatch propagation, FK gating against
archived types, name uniqueness, version bumps.
"""

from __future__ import annotations

import base64
import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from gargantua.db.models import MCPServer, MCPServerType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def master_key(monkeypatch: pytest.MonkeyPatch) -> bytes:
    """Pin a deterministic KEK so kek_id fingerprints are stable across asserts."""
    raw = b"\x11" * 32
    monkeypatch.setenv("MASTER_KEY", base64.b64encode(raw).decode("ascii"))

    from gargantua.settings import get_settings

    get_settings.cache_clear()
    yield raw
    get_settings.cache_clear()


@pytest.fixture
def sync_session_maker(truncate_db: Engine) -> sessionmaker:
    return sessionmaker(bind=truncate_db, expire_on_commit=False, future=True)


def _seed_type(
    s: Session,
    *,
    slug: str = "postgres",
    name: str = "Postgres",
    mode: str = "stdio",
    archived: bool = False,
) -> MCPServerType:
    from datetime import datetime, timezone

    t = MCPServerType(slug=slug, name=name, mode=mode)
    if archived:
        t.archived_at = datetime.now(tz=timezone.utc)
    s.add(t)
    s.flush()
    return t


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_server_encrypts_env_vars(
    sync_session_maker, master_key
) -> None:
    from gargantua.repo.mcp_servers import create, decrypt_env_vars
    from gargantua.secrets import kek_fingerprint

    with sync_session_maker() as s:
        t = _seed_type(s)
        s.commit()
        tid = t.id

    with sync_session_maker() as s:
        srv = create(
            s,
            type_id=tid,
            name="db-prod",
            env_tag="prod",
            env_vars={"DSN": "postgres://...", "READ_ONLY": "true"},
        )
        s.commit()
        sid = srv.id

    with sync_session_maker() as s:
        row = s.get(MCPServer, sid)
    # Bytes on disk, not the plaintext dict.
    assert isinstance(row.env_vars, (bytes, memoryview))
    assert row.env_vars != b'{"DSN": "postgres://..."}'
    assert isinstance(row.env_var_iv, (bytes, memoryview))
    assert row.env_var_kek_id == kek_fingerprint(master_key)
    # Round-trip via the helper.
    assert decrypt_env_vars(row) == {
        "DSN": "postgres://...",
        "READ_ONLY": "true",
    }


def test_create_server_with_no_env_vars_writes_nulls(
    sync_session_maker, master_key
) -> None:
    from gargantua.repo.mcp_servers import create, decrypt_env_vars

    with sync_session_maker() as s:
        t = _seed_type(s)
        s.commit()
        tid = t.id

    with sync_session_maker() as s:
        srv = create(s, type_id=tid, name="x", env_tag="prod", env_vars=None)
        s.commit()
        sid = srv.id

    with sync_session_maker() as s:
        row = s.get(MCPServer, sid)
    assert row.env_vars is None
    assert row.env_var_iv is None
    assert row.env_var_kek_id is None
    assert decrypt_env_vars(row) == {}


def test_create_server_rejects_unknown_type(
    sync_session_maker, master_key  # noqa: ARG001
) -> None:
    from gargantua.repo.mcp_servers import InvalidTypeRef, create

    with sync_session_maker() as s:
        with pytest.raises(InvalidTypeRef):
            create(
                s, type_id=uuid4(), name="x", env_tag="prod", env_vars={}
            )


def test_create_server_rejects_archived_type(
    sync_session_maker, master_key  # noqa: ARG001
) -> None:
    from gargantua.repo.mcp_servers import InvalidTypeRef, create

    with sync_session_maker() as s:
        t = _seed_type(s, archived=True)
        s.commit()
        tid = t.id

    with sync_session_maker() as s:
        with pytest.raises(InvalidTypeRef):
            create(s, type_id=tid, name="x", env_tag="prod")


def test_create_server_rejects_duplicate_triple(
    sync_session_maker, master_key  # noqa: ARG001
) -> None:
    from gargantua.repo.mcp_servers import DuplicateName, create

    with sync_session_maker() as s:
        t = _seed_type(s)
        s.commit()
        tid = t.id

    with sync_session_maker() as s:
        create(s, type_id=tid, name="db", env_tag="prod")
        s.commit()

    with sync_session_maker() as s:
        with pytest.raises(DuplicateName):
            create(s, type_id=tid, name="db", env_tag="prod")


def test_create_server_allows_same_name_different_env_tag(
    sync_session_maker, master_key  # noqa: ARG001
) -> None:
    from gargantua.repo.mcp_servers import create

    with sync_session_maker() as s:
        t = _seed_type(s)
        s.commit()
        tid = t.id

    with sync_session_maker() as s:
        create(s, type_id=tid, name="db", env_tag="prod")
        create(s, type_id=tid, name="db", env_tag="dev")
        s.commit()


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


def test_update_changes_only_specified_fields(
    sync_session_maker, master_key  # noqa: ARG001
) -> None:
    from gargantua.repo.mcp_servers import create, update

    with sync_session_maker() as s:
        t = _seed_type(s)
        s.commit()
        srv = create(
            s,
            type_id=t.id,
            name="db",
            env_tag="prod",
            command="uvx",
            args=["postgres-mcp"],
        )
        s.commit()
        sid = srv.id

    with sync_session_maker() as s:
        update(s, server_id=sid, name="db-v2")
        s.commit()

    with sync_session_maker() as s:
        row = s.get(MCPServer, sid)
    assert row.name == "db-v2"
    assert row.command == "uvx"
    assert row.args == ["postgres-mcp"]
    assert row.version == 2


def test_update_env_vars_rotates_iv(
    sync_session_maker, master_key  # noqa: ARG001
) -> None:
    """Submitting env_vars (even unchanged) writes a fresh IV.

    AES-GCM mandates non-reused IVs.  Each PATCH that touches env_vars
    must produce a new IV even if the plaintext stayed the same.
    """
    from gargantua.repo.mcp_servers import create, update

    with sync_session_maker() as s:
        t = _seed_type(s)
        s.commit()
        srv = create(
            s,
            type_id=t.id,
            name="db",
            env_tag="prod",
            env_vars={"K": "v"},
        )
        s.commit()
        sid = srv.id
        original_iv = bytes(srv.env_var_iv)
        original_ct = bytes(srv.env_vars)

    with sync_session_maker() as s:
        update(s, server_id=sid, env_vars={"K": "v"})  # same value
        s.commit()

    with sync_session_maker() as s:
        row = s.get(MCPServer, sid)
    assert bytes(row.env_var_iv) != original_iv
    assert bytes(row.env_vars) != original_ct


def test_update_env_vars_empty_dict_clears(
    sync_session_maker, master_key  # noqa: ARG001
) -> None:
    from gargantua.repo.mcp_servers import create, decrypt_env_vars, update

    with sync_session_maker() as s:
        t = _seed_type(s)
        s.commit()
        srv = create(
            s,
            type_id=t.id,
            name="db",
            env_tag="prod",
            env_vars={"K": "v"},
        )
        s.commit()
        sid = srv.id

    with sync_session_maker() as s:
        update(s, server_id=sid, env_vars={})
        s.commit()

    with sync_session_maker() as s:
        row = s.get(MCPServer, sid)
    assert row.env_vars is None
    assert decrypt_env_vars(row) == {}


def test_update_no_changes_is_noop(
    sync_session_maker, master_key  # noqa: ARG001
) -> None:
    from gargantua.repo.mcp_servers import create, update

    with sync_session_maker() as s:
        t = _seed_type(s)
        s.commit()
        srv = create(s, type_id=t.id, name="db", env_tag="prod")
        s.commit()
        sid = srv.id

    with sync_session_maker() as s:
        update(s, server_id=sid)
        s.commit()

    with sync_session_maker() as s:
        row = s.get(MCPServer, sid)
    # Version was not bumped.
    assert row.version == 1


def test_update_missing_id_raises(sync_session_maker, master_key) -> None:  # noqa: ARG001
    from gargantua.repo.mcp_servers import NotFound, update

    with sync_session_maker() as s:
        with pytest.raises(NotFound):
            update(s, server_id=uuid4(), name="ghost")


def test_update_duplicate_name_raises(sync_session_maker, master_key) -> None:  # noqa: ARG001
    from gargantua.repo.mcp_servers import DuplicateName, create, update

    with sync_session_maker() as s:
        t = _seed_type(s)
        s.commit()
        a = create(s, type_id=t.id, name="alpha", env_tag="prod")
        b = create(s, type_id=t.id, name="beta", env_tag="prod")
        s.commit()
        _ = a

    with sync_session_maker() as s:
        with pytest.raises(DuplicateName):
            update(s, server_id=b.id, name="alpha")


# ---------------------------------------------------------------------------
# Archive / unarchive
# ---------------------------------------------------------------------------


def test_archive_then_unarchive(sync_session_maker, master_key) -> None:  # noqa: ARG001
    from gargantua.repo.mcp_servers import archive, create, unarchive

    with sync_session_maker() as s:
        t = _seed_type(s)
        s.commit()
        srv = create(s, type_id=t.id, name="db", env_tag="prod")
        s.commit()
        sid = srv.id

    with sync_session_maker() as s:
        a = archive(s, server_id=sid)
        s.commit()
    assert a.archived_at is not None

    with sync_session_maker() as s:
        u = unarchive(s, server_id=sid)
        s.commit()
    assert u.archived_at is None


def test_archive_idempotent(sync_session_maker, master_key) -> None:  # noqa: ARG001
    from gargantua.repo.mcp_servers import archive, create

    with sync_session_maker() as s:
        t = _seed_type(s)
        s.commit()
        srv = create(s, type_id=t.id, name="db", env_tag="prod")
        s.commit()
        sid = srv.id

    with sync_session_maker() as s:
        first = archive(s, server_id=sid)
        first_ts = first.archived_at
        s.commit()
    with sync_session_maker() as s:
        second = archive(s, server_id=sid)
        s.commit()
    assert second.archived_at == first_ts


# ---------------------------------------------------------------------------
# List + decrypt
# ---------------------------------------------------------------------------


def test_list_filters_by_type_and_env_tag(
    sync_session_maker, master_key  # noqa: ARG001
) -> None:
    from gargantua.repo.mcp_servers import create, list_servers

    with sync_session_maker() as s:
        t1 = _seed_type(s, slug="postgres")
        t2 = _seed_type(s, slug="opensearch")
        s.commit()
        create(s, type_id=t1.id, name="pg-prod", env_tag="prod")
        create(s, type_id=t1.id, name="pg-dev", env_tag="dev")
        create(s, type_id=t2.id, name="os-prod", env_tag="prod")
        s.commit()

    with sync_session_maker() as s:
        rows, total = list_servers(s, type_id=t1.id)
    assert total == 2

    with sync_session_maker() as s:
        rows, total = list_servers(s, env_tag="prod")
    assert total == 2

    with sync_session_maker() as s:
        rows, total = list_servers(s, type_id=t1.id, env_tag="prod")
    assert total == 1
    assert rows[0].name == "pg-prod"


def test_decrypt_env_vars_raises_kek_mismatch_under_different_key(
    sync_session_maker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If MASTER_KEY changes without rotation, reads must fail loudly."""
    from gargantua.repo.mcp_servers import (
        KekMismatchOnRead,
        create,
        decrypt_env_vars,
    )
    from gargantua.settings import get_settings

    # Write under key A.
    raw_a = b"\xaa" * 32
    monkeypatch.setenv("MASTER_KEY", base64.b64encode(raw_a).decode("ascii"))
    get_settings.cache_clear()

    with sync_session_maker() as s:
        t = _seed_type(s)
        s.commit()
        srv = create(
            s, type_id=t.id, name="db", env_tag="prod", env_vars={"K": "v"}
        )
        s.commit()
        sid = srv.id

    # Swap to key B without rotating.
    raw_b = b"\xbb" * 32
    monkeypatch.setenv("MASTER_KEY", base64.b64encode(raw_b).decode("ascii"))
    get_settings.cache_clear()

    with sync_session_maker() as s:
        row = s.get(MCPServer, sid)
        with pytest.raises(KekMismatchOnRead):
            decrypt_env_vars(row)

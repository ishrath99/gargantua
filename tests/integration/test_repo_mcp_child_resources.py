"""Repo layer for ``ai.mcp_server_child_resource``.

Focus on the tricky paths: parent gating (must exist, must not be
archived, parent type must support children), encryption round-trip,
duplicate-name handling, enable/disable idempotency.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from gargantua.db.models import (
    MCPServer,
    MCPServerChildResource,
    MCPServerType,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def master_key(monkeypatch: pytest.MonkeyPatch) -> bytes:
    raw = b"\x22" * 32
    monkeypatch.setenv("MASTER_KEY", base64.b64encode(raw).decode("ascii"))

    from gargantua.settings import get_settings

    get_settings.cache_clear()
    yield raw
    get_settings.cache_clear()


@pytest.fixture
def sync_session_maker(truncate_db: Engine) -> sessionmaker:
    return sessionmaker(bind=truncate_db, expire_on_commit=False, future=True)


def _seed_type_and_parent(
    s: Session,
    *,
    supports_children: bool = True,
    parent_archived: bool = False,
) -> tuple[MCPServerType, MCPServer]:
    t = MCPServerType(
        slug="swagger-mcp",
        name="Swagger",
        mode="streamable_http",
        supports_swagger_child=supports_children,
    )
    s.add(t)
    s.flush()

    p = MCPServer(
        type_id=t.id, name="api-gw", env_tag="prod"
    )
    if parent_archived:
        p.archived_at = datetime.now(tz=timezone.utc)
    s.add(p)
    s.flush()
    return t, p


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_encrypts_headers(sync_session_maker, master_key) -> None:
    from gargantua.repo.mcp_child_resources import create, decrypt_headers
    from gargantua.secrets import kek_fingerprint

    with sync_session_maker() as s:
        _, p = _seed_type_and_parent(s)
        s.commit()
        pid = p.id

    with sync_session_maker() as s:
        child = create(
            s,
            parent_id=pid,
            child_type="swagger",
            name="orders-api",
            url="https://example.com/swagger.json",
            headers={"Authorization": "Bearer xyz"},
        )
        s.commit()
        cid = child.id

    with sync_session_maker() as s:
        row = s.get(MCPServerChildResource, cid)
    assert isinstance(row.headers, (bytes, memoryview))
    assert bytes(row.headers) != b'{"Authorization": "Bearer xyz"}'
    assert row.headers_kek_id == kek_fingerprint(master_key)
    assert decrypt_headers(row) == {"Authorization": "Bearer xyz"}


def test_create_no_headers_writes_nulls(
    sync_session_maker, master_key  # noqa: ARG001
) -> None:
    from gargantua.repo.mcp_child_resources import create, decrypt_headers

    with sync_session_maker() as s:
        _, p = _seed_type_and_parent(s)
        s.commit()
        pid = p.id

    with sync_session_maker() as s:
        child = create(
            s,
            parent_id=pid,
            child_type="swagger",
            name="orders-api",
            url="https://example.com/swagger.json",
            headers=None,
        )
        s.commit()
        cid = child.id

    with sync_session_maker() as s:
        row = s.get(MCPServerChildResource, cid)
    assert row.headers is None
    assert row.headers_iv is None
    assert row.headers_kek_id is None
    assert decrypt_headers(row) == {}


def test_create_rejects_unknown_parent(
    sync_session_maker, master_key  # noqa: ARG001
) -> None:
    from gargantua.repo.mcp_child_resources import InvalidParentRef, create

    with sync_session_maker() as s:
        with pytest.raises(InvalidParentRef):
            create(
                s,
                parent_id=uuid4(),
                child_type="swagger",
                name="x",
                url="https://example.com",
            )


def test_create_rejects_archived_parent(
    sync_session_maker, master_key  # noqa: ARG001
) -> None:
    from gargantua.repo.mcp_child_resources import InvalidParentRef, create

    with sync_session_maker() as s:
        _, p = _seed_type_and_parent(s, parent_archived=True)
        s.commit()
        pid = p.id

    with sync_session_maker() as s:
        with pytest.raises(InvalidParentRef):
            create(
                s,
                parent_id=pid,
                child_type="swagger",
                name="x",
                url="https://example.com",
            )


def test_create_rejects_parent_type_that_disallows_children(
    sync_session_maker, master_key  # noqa: ARG001
) -> None:
    from gargantua.repo.mcp_child_resources import InvalidParentRef, create

    with sync_session_maker() as s:
        _, p = _seed_type_and_parent(s, supports_children=False)
        s.commit()
        pid = p.id

    with sync_session_maker() as s:
        with pytest.raises(InvalidParentRef):
            create(
                s,
                parent_id=pid,
                child_type="swagger",
                name="x",
                url="https://example.com",
            )


def test_create_rejects_unknown_type(
    sync_session_maker, master_key  # noqa: ARG001
) -> None:
    from gargantua.repo.mcp_child_resources import InvalidChildType, create

    with sync_session_maker() as s:
        _, p = _seed_type_and_parent(s)
        s.commit()
        pid = p.id

    with sync_session_maker() as s:
        with pytest.raises(InvalidChildType):
            create(
                s,
                parent_id=pid,
                child_type="postman",  # not in VALID_CHILD_TYPES
                name="x",
                url="https://example.com",
            )


def test_create_rejects_duplicate_name_per_parent(
    sync_session_maker, master_key  # noqa: ARG001
) -> None:
    from gargantua.repo.mcp_child_resources import DuplicateName, create

    with sync_session_maker() as s:
        _, p = _seed_type_and_parent(s)
        s.commit()
        pid = p.id

    with sync_session_maker() as s:
        create(
            s,
            parent_id=pid,
            child_type="swagger",
            name="orders",
            url="https://a/swagger.json",
        )
        s.commit()

    with sync_session_maker() as s:
        with pytest.raises(DuplicateName):
            create(
                s,
                parent_id=pid,
                child_type="swagger",
                name="orders",
                url="https://b/swagger.json",
            )


# ---------------------------------------------------------------------------
# Update / enable / disable
# ---------------------------------------------------------------------------


def test_update_partial(sync_session_maker, master_key) -> None:  # noqa: ARG001
    from gargantua.repo.mcp_child_resources import create, update

    with sync_session_maker() as s:
        _, p = _seed_type_and_parent(s)
        s.commit()
        child = create(
            s,
            parent_id=p.id,
            child_type="swagger",
            name="orders",
            url="https://a/swagger.json",
        )
        s.commit()
        cid = child.id

    with sync_session_maker() as s:
        update(s, child_id=cid, url="https://b/swagger.json")
        s.commit()

    with sync_session_maker() as s:
        row = s.get(MCPServerChildResource, cid)
    assert row.name == "orders"  # untouched
    assert row.url == "https://b/swagger.json"
    assert row.version == 2


def test_update_headers_rotates_iv(
    sync_session_maker, master_key  # noqa: ARG001
) -> None:
    from gargantua.repo.mcp_child_resources import create, update

    with sync_session_maker() as s:
        _, p = _seed_type_and_parent(s)
        s.commit()
        child = create(
            s,
            parent_id=p.id,
            child_type="swagger",
            name="orders",
            url="https://a/swagger.json",
            headers={"X-Token": "abc"},
        )
        s.commit()
        cid = child.id
        original_iv = bytes(child.headers_iv)

    with sync_session_maker() as s:
        update(s, child_id=cid, headers={"X-Token": "abc"})
        s.commit()

    with sync_session_maker() as s:
        row = s.get(MCPServerChildResource, cid)
    assert bytes(row.headers_iv) != original_iv


def test_update_missing_id_raises(
    sync_session_maker, master_key  # noqa: ARG001
) -> None:
    from gargantua.repo.mcp_child_resources import NotFound, update

    with sync_session_maker() as s:
        with pytest.raises(NotFound):
            update(s, child_id=uuid4(), name="ghost")


def test_enable_disable_round_trip(
    sync_session_maker, master_key  # noqa: ARG001
) -> None:
    from gargantua.repo.mcp_child_resources import (
        create,
        disable,
        enable,
    )

    with sync_session_maker() as s:
        _, p = _seed_type_and_parent(s)
        s.commit()
        child = create(
            s,
            parent_id=p.id,
            child_type="swagger",
            name="orders",
            url="https://a",
        )
        s.commit()
        cid = child.id
    assert child.enabled is True

    with sync_session_maker() as s:
        d = disable(s, child_id=cid)
        s.commit()
    assert d.enabled is False

    with sync_session_maker() as s:
        e = enable(s, child_id=cid)
        s.commit()
    assert e.enabled is True


def test_enable_disable_idempotent(
    sync_session_maker, master_key  # noqa: ARG001
) -> None:
    from gargantua.repo.mcp_child_resources import (
        create,
        disable,
        enable,
    )

    with sync_session_maker() as s:
        _, p = _seed_type_and_parent(s)
        s.commit()
        child = create(
            s,
            parent_id=p.id,
            child_type="swagger",
            name="orders",
            url="https://a",
        )
        s.commit()
        cid = child.id

    # already enabled — second enable is a no-op
    with sync_session_maker() as s:
        e = enable(s, child_id=cid)
        s.commit()
    assert e.enabled is True

    # disable twice — second disable is a no-op
    with sync_session_maker() as s:
        disable(s, child_id=cid)
        s.commit()
    with sync_session_maker() as s:
        d = disable(s, child_id=cid)
        s.commit()
    assert d.enabled is False


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


def test_list_scoped_to_parent_and_filters(
    sync_session_maker, master_key  # noqa: ARG001
) -> None:
    from gargantua.repo.mcp_child_resources import (
        create,
        disable,
        list_children,
    )

    with sync_session_maker() as s:
        _, p = _seed_type_and_parent(s)
        s.commit()
        pid = p.id

        # Second parent so we can prove scoping.
        t2 = MCPServerType(
            slug="swagger-mcp-2",
            name="Swagger 2",
            mode="streamable_http",
            supports_swagger_child=True,
        )
        s.add(t2)
        s.flush()
        p2 = MCPServer(type_id=t2.id, name="api-gw-2", env_tag="prod")
        s.add(p2)
        s.flush()
        p2id = p2.id
        s.commit()

        create(s, parent_id=pid, child_type="swagger", name="alpha", url="https://a")
        create(s, parent_id=pid, child_type="swagger", name="beta", url="https://b")
        gamma = create(
            s,
            parent_id=pid,
            child_type="swagger",
            name="gamma",
            url="https://c",
        )
        create(s, parent_id=p2id, child_type="swagger", name="delta", url="https://d")
        s.commit()

        disable(s, child_id=gamma.id)
        s.commit()

    with sync_session_maker() as s:
        rows, total = list_children(s, parent_id=pid)
    # scoped to parent, excludes disabled by default
    assert total == 2
    assert {r.name for r in rows} == {"alpha", "beta"}

    with sync_session_maker() as s:
        rows, total = list_children(
            s, parent_id=pid, include_disabled=True
        )
    assert total == 3

    with sync_session_maker() as s:
        rows, total = list_children(s, parent_id=pid, search="alph")
    assert total == 1
    assert rows[0].name == "alpha"


def test_decrypt_headers_kek_mismatch(
    sync_session_maker, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gargantua.repo.mcp_child_resources import (
        KekMismatchOnRead,
        create,
        decrypt_headers,
    )
    from gargantua.settings import get_settings

    raw_a = b"\x33" * 32
    monkeypatch.setenv("MASTER_KEY", base64.b64encode(raw_a).decode("ascii"))
    get_settings.cache_clear()

    with sync_session_maker() as s:
        _, p = _seed_type_and_parent(s)
        s.commit()
        child = create(
            s,
            parent_id=p.id,
            child_type="swagger",
            name="x",
            url="https://a",
            headers={"X": "y"},
        )
        s.commit()
        cid = child.id

    raw_b = b"\x44" * 32
    monkeypatch.setenv("MASTER_KEY", base64.b64encode(raw_b).decode("ascii"))
    get_settings.cache_clear()

    with sync_session_maker() as s:
        row = s.get(MCPServerChildResource, cid)
        with pytest.raises(KekMismatchOnRead):
            decrypt_headers(row)

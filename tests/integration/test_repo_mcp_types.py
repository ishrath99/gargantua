"""Repo layer for ``ai.mcp_server_type`` — focuses on the tricky paths.

The happy-path CRUD is also exercised by the HTTP integration tests in
``test_admin_mcp_types``; this file isolates:

* slug uniqueness (case-sensitive, raises on conflict),
* mode validation (only ``stdio`` / ``sse`` / ``streamable_http`` allowed),
* archive / unarchive idempotency,
* partial-update semantics (None means "leave alone", not "clear").
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from gargantua.db.models import MCPServerType


@pytest.fixture
def sync_session_maker(truncate_db: Engine) -> sessionmaker:
    return sessionmaker(bind=truncate_db, expire_on_commit=False, future=True)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_inserts_with_defaults(sync_session_maker) -> None:
    from gargantua.repo.mcp_server_types import create

    with sync_session_maker() as s:
        t = create(
            s,
            slug="postgres",
            name="PostgreSQL MCP",
            mode="stdio",
            description="Run SQL against a Postgres database.",
            default_command="uvx",
            default_args=["postgres-mcp"],
            config_schema=[
                {
                    "name": "DSN",
                    "label": "Connection string",
                    "type": "password",
                    "is_secret": True,
                    "required": True,
                }
            ],
        )
        s.commit()

    with sync_session_maker() as s:
        row = s.execute(
            select(MCPServerType).where(MCPServerType.slug == "postgres")
        ).scalar_one()
    assert row.name == "PostgreSQL MCP"
    assert row.mode == "stdio"
    assert row.default_args == ["postgres-mcp"]
    assert row.config_schema[0]["name"] == "DSN"
    # Server-side defaults landed.
    assert row.version == 1
    assert row.archived_at is None
    assert row.created_at is not None
    assert row.default_env_vars == {}
    assert row.optional_env_vars == {}
    assert row.supports_swagger_child is False


def test_create_rejects_duplicate_slug(sync_session_maker) -> None:
    from gargantua.repo.mcp_server_types import DuplicateSlug, create

    with sync_session_maker() as s:
        create(s, slug="postgres", name="PG 1", mode="stdio")
        s.commit()

    with sync_session_maker() as s:
        with pytest.raises(DuplicateSlug):
            create(s, slug="postgres", name="PG 2", mode="stdio")


def test_create_rejects_invalid_mode(sync_session_maker) -> None:
    from gargantua.repo.mcp_server_types import InvalidMode, create

    with sync_session_maker() as s:
        with pytest.raises(InvalidMode):
            create(s, slug="x", name="X", mode="websocket")


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


def test_update_partial_leaves_unspecified_fields_alone(sync_session_maker) -> None:
    """``None`` means "don't touch"; pass an empty list / dict to clear."""
    from gargantua.repo.mcp_server_types import create, update

    with sync_session_maker() as s:
        original = create(
            s,
            slug="postgres",
            name="PG",
            mode="stdio",
            default_command="uvx",
            default_args=["postgres-mcp"],
        )
        s.commit()
        type_id = original.id

    with sync_session_maker() as s:
        updated = update(s, type_id=type_id, name="Postgres v2")
        s.commit()

    with sync_session_maker() as s:
        row = s.get(MCPServerType, type_id)
    assert row.name == "Postgres v2"
    # Untouched fields preserved.
    assert row.mode == "stdio"
    assert row.default_command == "uvx"
    assert row.default_args == ["postgres-mcp"]


def test_update_can_clear_a_field_with_empty_collection(sync_session_maker) -> None:
    from gargantua.repo.mcp_server_types import create, update

    with sync_session_maker() as s:
        t = create(
            s,
            slug="x",
            name="X",
            mode="stdio",
            default_args=["a", "b"],
        )
        s.commit()
        tid = t.id

    with sync_session_maker() as s:
        update(s, type_id=tid, default_args=[])
        s.commit()

    with sync_session_maker() as s:
        assert s.get(MCPServerType, tid).default_args == []


def test_update_rejects_invalid_mode(sync_session_maker) -> None:
    from gargantua.repo.mcp_server_types import InvalidMode, create, update

    with sync_session_maker() as s:
        t = create(s, slug="x", name="X", mode="stdio")
        s.commit()
        tid = t.id

    with sync_session_maker() as s:
        with pytest.raises(InvalidMode):
            update(s, type_id=tid, mode="bogus")


def test_update_missing_id_raises(sync_session_maker) -> None:
    from gargantua.repo.mcp_server_types import NotFound, update

    with sync_session_maker() as s:
        with pytest.raises(NotFound):
            update(s, type_id=uuid4(), name="ghost")


# ---------------------------------------------------------------------------
# Archive / unarchive
# ---------------------------------------------------------------------------


def test_archive_sets_archived_at(sync_session_maker) -> None:
    from gargantua.repo.mcp_server_types import archive, create

    with sync_session_maker() as s:
        t = create(s, slug="x", name="X", mode="stdio")
        s.commit()
        tid = t.id

    with sync_session_maker() as s:
        archived = archive(s, type_id=tid)
        s.commit()
    assert archived.archived_at is not None


def test_archive_is_idempotent_returns_existing_timestamp(
    sync_session_maker,
) -> None:
    from gargantua.repo.mcp_server_types import archive, create

    with sync_session_maker() as s:
        t = create(s, slug="x", name="X", mode="stdio")
        s.commit()
        tid = t.id

    with sync_session_maker() as s:
        first = archive(s, type_id=tid)
        first_ts = first.archived_at
        s.commit()

    with sync_session_maker() as s:
        second = archive(s, type_id=tid)
        s.commit()
    # Re-archiving doesn't bump the timestamp.
    assert second.archived_at == first_ts


def test_unarchive_clears_archived_at(sync_session_maker) -> None:
    from gargantua.repo.mcp_server_types import archive, create, unarchive

    with sync_session_maker() as s:
        t = create(s, slug="x", name="X", mode="stdio")
        s.commit()
        tid = t.id

    with sync_session_maker() as s:
        archive(s, type_id=tid)
        s.commit()

    with sync_session_maker() as s:
        u = unarchive(s, type_id=tid)
        s.commit()
    assert u.archived_at is None


def test_unarchive_active_row_is_noop(sync_session_maker) -> None:
    from gargantua.repo.mcp_server_types import create, unarchive

    with sync_session_maker() as s:
        t = create(s, slug="x", name="X", mode="stdio")
        s.commit()
        tid = t.id

    with sync_session_maker() as s:
        u = unarchive(s, type_id=tid)
    assert u.archived_at is None


# ---------------------------------------------------------------------------
# Get / list
# ---------------------------------------------------------------------------


def test_get_by_slug(sync_session_maker) -> None:
    from gargantua.repo.mcp_server_types import create, get_by_slug

    with sync_session_maker() as s:
        create(s, slug="postgres", name="PG", mode="stdio")
        s.commit()

    with sync_session_maker() as s:
        assert get_by_slug(s, "postgres") is not None
        assert get_by_slug(s, "missing") is None


def test_list_filters_and_paginates(sync_session_maker) -> None:
    from gargantua.repo.mcp_server_types import create, list_types

    with sync_session_maker() as s:
        create(s, slug="postgres", name="Postgres", mode="stdio")
        create(s, slug="opensearch", name="OpenSearch", mode="sse")
        create(s, slug="swagger", name="Swagger", mode="streamable_http")
        s.commit()

    with sync_session_maker() as s:
        rows, total = list_types(s)
    assert total == 3

    with sync_session_maker() as s:
        rows, total = list_types(s, mode="stdio")
    assert total == 1
    assert rows[0].slug == "postgres"

    with sync_session_maker() as s:
        rows, total = list_types(s, search="search")
    assert total == 1
    assert rows[0].slug == "opensearch"

    with sync_session_maker() as s:
        rows, total = list_types(s, page=1, page_size=2)
    assert total == 3
    assert len(rows) == 2


def test_list_excludes_archived_by_default(sync_session_maker) -> None:
    from gargantua.repo.mcp_server_types import archive, create, list_types

    with sync_session_maker() as s:
        active = create(s, slug="active", name="Active", mode="stdio")
        dormant = create(s, slug="dormant", name="Dormant", mode="stdio")
        s.commit()
        archive(s, type_id=dormant.id)
        s.commit()
        _ = active

    with sync_session_maker() as s:
        rows, total = list_types(s)
    slugs = {r.slug for r in rows}
    assert total == 1
    assert slugs == {"active"}

    with sync_session_maker() as s:
        rows, total = list_types(s, include_archived=True)
    assert total == 2

"""Repo layer for ``gargantua_app.agent``.

Focus on the parts the route layer can't easily cover from the outside:
reference validation across ``mcp_server_ids`` / ``child_resource_ids``,
the parent-membership invariant, duplicate-name detection, and the
partial-update / archive semantics.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from gargantua.db.models import (
    Agent,
    MCPServer,
    MCPServerChildResource,
    MCPServerType,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def sync_session_maker(truncate_db: Engine) -> sessionmaker:
    return sessionmaker(bind=truncate_db, expire_on_commit=False, future=True)


def _seed_type(
    s: Session, *, slug: str = "swagger-mcp", supports_children: bool = True
) -> MCPServerType:
    t = MCPServerType(
        slug=slug,
        name=slug,
        mode="streamable_http",
        supports_swagger_child=supports_children,
    )
    s.add(t)
    s.flush()
    return t


def _seed_server(
    s: Session,
    *,
    type_id,
    name: str = "srv",
    env_tag: str = "prod",
    archived: bool = False,
) -> MCPServer:
    srv = MCPServer(type_id=type_id, name=name, env_tag=env_tag)
    if archived:
        srv.archived_at = datetime.now(tz=UTC)
    s.add(srv)
    s.flush()
    return srv


def _seed_child(
    s: Session,
    *,
    parent_id,
    name: str = "orders",
    enabled: bool = True,
    child_type: str = "swagger",
) -> MCPServerChildResource:
    c = MCPServerChildResource(
        parent_mcp_server_id=parent_id,
        type=child_type,
        name=name,
        url="https://example.com/swagger.json",
        enabled=enabled,
    )
    s.add(c)
    s.flush()
    return c


# ---------------------------------------------------------------------------
# Create — happy path & defaults
# ---------------------------------------------------------------------------


def test_create_minimal_payload_uses_defaults(sync_session_maker) -> None:
    from gargantua.repo.agents import create

    with sync_session_maker() as s:
        a = create(
            s,
            name="researcher",
            model="gpt-5",
            instructions="Be terse.",
        )
        s.commit()
        aid = a.id

    with sync_session_maker() as s:
        row = s.get(Agent, aid)
    assert row is not None
    assert row.name == "researcher"
    assert row.model == "gpt-5"
    assert row.instructions == "Be terse."
    assert row.description is None
    assert row.tools_config == {}
    assert row.mcp_server_ids == []
    assert row.child_resource_ids == []
    assert row.agent_config == {}
    assert row.archived_at is None
    assert row.version == 1


def test_create_with_valid_refs(sync_session_maker) -> None:
    from gargantua.repo.agents import create

    with sync_session_maker() as s:
        t = _seed_type(s)
        s.commit()
        srv = _seed_server(s, type_id=t.id)
        s.commit()
        child = _seed_child(s, parent_id=srv.id)
        s.commit()
        sid, cid = srv.id, child.id

    with sync_session_maker() as s:
        a = create(
            s,
            name="api-bot",
            model="gpt-5",
            instructions="Use the API.",
            mcp_server_ids=[sid],
            child_resource_ids=[cid],
        )
        s.commit()
        aid = a.id

    with sync_session_maker() as s:
        row = s.get(Agent, aid)
    assert row.mcp_server_ids == [sid]
    assert row.child_resource_ids == [cid]


def test_create_duplicate_name_raises(sync_session_maker) -> None:
    from gargantua.repo.agents import DuplicateName, create

    with sync_session_maker() as s:
        create(s, name="agentus", model="m", instructions="i")
        s.commit()

    with sync_session_maker() as s:
        with pytest.raises(DuplicateName):
            create(s, name="agentus", model="m", instructions="i")


# ---------------------------------------------------------------------------
# Create — reference validation
# ---------------------------------------------------------------------------


def test_create_rejects_missing_mcp_server_id(sync_session_maker) -> None:
    from gargantua.repo.agents import InvalidRefs, create

    ghost = uuid4()
    with sync_session_maker() as s:
        with pytest.raises(InvalidRefs) as exc:
            create(
                s,
                name="x",
                model="m",
                instructions="i",
                mcp_server_ids=[ghost],
            )
    assert exc.value.missing_mcp_server_ids == [ghost]


def test_create_rejects_archived_mcp_server_id(sync_session_maker) -> None:
    from gargantua.repo.agents import InvalidRefs, create

    with sync_session_maker() as s:
        t = _seed_type(s)
        s.commit()
        srv = _seed_server(s, type_id=t.id, archived=True)
        s.commit()
        sid = srv.id

    with sync_session_maker() as s:
        with pytest.raises(InvalidRefs) as exc:
            create(
                s,
                name="x",
                model="m",
                instructions="i",
                mcp_server_ids=[sid],
            )
    assert exc.value.archived_mcp_server_ids == [sid]


def test_create_rejects_missing_child_resource_id(sync_session_maker) -> None:
    from gargantua.repo.agents import InvalidRefs, create

    with sync_session_maker() as s:
        t = _seed_type(s)
        s.commit()
        srv = _seed_server(s, type_id=t.id)
        s.commit()
        sid = srv.id

    ghost = uuid4()
    with sync_session_maker() as s:
        with pytest.raises(InvalidRefs) as exc:
            create(
                s,
                name="x",
                model="m",
                instructions="i",
                mcp_server_ids=[sid],
                child_resource_ids=[ghost],
            )
    assert exc.value.missing_child_resource_ids == [ghost]


def test_create_rejects_disabled_child_resource_id(sync_session_maker) -> None:
    from gargantua.repo.agents import InvalidRefs, create

    with sync_session_maker() as s:
        t = _seed_type(s)
        s.commit()
        srv = _seed_server(s, type_id=t.id)
        s.commit()
        child = _seed_child(s, parent_id=srv.id, enabled=False)
        s.commit()
        sid, cid = srv.id, child.id

    with sync_session_maker() as s:
        with pytest.raises(InvalidRefs) as exc:
            create(
                s,
                name="x",
                model="m",
                instructions="i",
                mcp_server_ids=[sid],
                child_resource_ids=[cid],
            )
    assert exc.value.disabled_child_resource_ids == [cid]


def test_create_rejects_orphan_child_whose_parent_not_in_server_ids(
    sync_session_maker,
) -> None:
    """Pointing at a Swagger child while leaving its host server out
    of mcp_server_ids is almost always a UI bug — reject it."""
    from gargantua.repo.agents import InvalidRefs, create

    with sync_session_maker() as s:
        t = _seed_type(s)
        s.commit()
        srv_a = _seed_server(s, type_id=t.id, name="a")
        srv_b = _seed_server(s, type_id=t.id, name="b")
        s.commit()
        child_b = _seed_child(s, parent_id=srv_b.id, name="b-orders")
        s.commit()
        sid_a, cid_b = srv_a.id, child_b.id

    with sync_session_maker() as s:
        with pytest.raises(InvalidRefs) as exc:
            create(
                s,
                name="x",
                model="m",
                instructions="i",
                mcp_server_ids=[sid_a],  # only A
                child_resource_ids=[cid_b],  # but child belongs to B
            )
    assert exc.value.orphan_child_resource_ids == [cid_b]


def test_create_dedupes_ref_ids_before_validating(sync_session_maker) -> None:
    """Submitting the same id twice should not double-fail and should be
    valid once the referenced row exists."""
    from gargantua.repo.agents import create

    with sync_session_maker() as s:
        t = _seed_type(s)
        s.commit()
        srv = _seed_server(s, type_id=t.id)
        s.commit()
        sid = srv.id

    with sync_session_maker() as s:
        a = create(
            s,
            name="x",
            model="m",
            instructions="i",
            mcp_server_ids=[sid, sid],
        )
        s.commit()
    # We don't dedupe storage (that's a UI concern), but the row should
    # have been created without raising.
    assert a.mcp_server_ids == [sid, sid]


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def test_get_by_id_and_get_by_name(sync_session_maker) -> None:
    from gargantua.repo.agents import create, get_by_id, get_by_name

    with sync_session_maker() as s:
        a = create(s, name="alpha", model="m", instructions="i")
        s.commit()
        aid = a.id

    with sync_session_maker() as s:
        assert get_by_id(s, aid).id == aid
        assert get_by_name(s, "alpha").id == aid
        assert get_by_id(s, uuid4()) is None
        assert get_by_name(s, "ghost") is None


def test_list_agents_pagination_and_search(sync_session_maker) -> None:
    from gargantua.repo.agents import create, list_agents

    with sync_session_maker() as s:
        for name in ["alpha", "beta", "betatron", "delta"]:
            create(s, name=name, model="gpt-5", instructions="i")
        s.commit()

    with sync_session_maker() as s:
        rows, total = list_agents(s, page=1, page_size=2)
    assert total == 4
    assert [r.name for r in rows] == ["alpha", "beta"]

    with sync_session_maker() as s:
        rows, total = list_agents(s, page=2, page_size=2)
    assert [r.name for r in rows] == ["betatron", "delta"]

    with sync_session_maker() as s:
        rows, total = list_agents(s, search="beta")
    assert total == 2
    assert {r.name for r in rows} == {"beta", "betatron"}


def test_list_agents_excludes_archived_by_default(sync_session_maker) -> None:
    from gargantua.repo.agents import archive, create, list_agents

    with sync_session_maker() as s:
        a = create(s, name="alpha", model="m", instructions="i")
        create(s, name="beta", model="m", instructions="i")
        s.commit()
        archive(s, agent_id=a.id)
        s.commit()

    with sync_session_maker() as s:
        rows, total = list_agents(s)
    assert total == 1
    assert rows[0].name == "beta"

    with sync_session_maker() as s:
        rows, total = list_agents(s, include_archived=True)
    assert total == 2


def test_list_agents_model_filter(sync_session_maker) -> None:
    from gargantua.repo.agents import create, list_agents

    with sync_session_maker() as s:
        create(s, name="a", model="gpt-5", instructions="i")
        create(s, name="b", model="gpt-5-mini", instructions="i")
        create(s, name="c", model="gpt-5", instructions="i")
        s.commit()

    with sync_session_maker() as s:
        rows, total = list_agents(s, model_filter="gpt-5")
    assert total == 2
    assert {r.name for r in rows} == {"a", "c"}


def test_list_agents_rejects_bad_pagination(sync_session_maker) -> None:
    from gargantua.repo.agents import list_agents

    with sync_session_maker() as s:
        with pytest.raises(ValueError):
            list_agents(s, page=0)
        with pytest.raises(ValueError):
            list_agents(s, page_size=0)


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


def test_update_partial_changes_only_specified_fields(sync_session_maker) -> None:
    from gargantua.repo.agents import create, update

    with sync_session_maker() as s:
        a = create(
            s,
            name="x",
            model="m",
            instructions="i",
            tools_config={"foo": 1},
        )
        s.commit()
        aid = a.id

    with sync_session_maker() as s:
        update(s, agent_id=aid, instructions="be helpful")
        s.commit()

    with sync_session_maker() as s:
        row = s.get(Agent, aid)
    assert row.name == "x"  # untouched
    assert row.instructions == "be helpful"
    assert row.tools_config == {"foo": 1}
    assert row.version == 2


def test_update_no_changes_is_noop(sync_session_maker) -> None:
    from gargantua.repo.agents import create, update

    with sync_session_maker() as s:
        a = create(s, name="x", model="m", instructions="i")
        s.commit()
        aid = a.id

    with sync_session_maker() as s:
        update(s, agent_id=aid)  # nothing to change
        s.commit()

    with sync_session_maker() as s:
        row = s.get(Agent, aid)
    assert row.version == 1


def test_update_missing_id_raises(sync_session_maker) -> None:
    from gargantua.repo.agents import NotFound, update

    with sync_session_maker() as s:
        with pytest.raises(NotFound):
            update(s, agent_id=uuid4(), name="ghost")


def test_update_duplicate_name_raises(sync_session_maker) -> None:
    from gargantua.repo.agents import DuplicateName, create, update

    with sync_session_maker() as s:
        a = create(s, name="alpha", model="m", instructions="i")
        b = create(s, name="beta", model="m", instructions="i")
        s.commit()
        bid = b.id
        _ = a

    with sync_session_maker() as s:
        with pytest.raises(DuplicateName):
            update(s, agent_id=bid, name="alpha")


def test_update_revalidates_refs_against_post_update_world(
    sync_session_maker,
) -> None:
    """Clearing mcp_server_ids while leaving a child whose parent was only
    in the old set must fail validation."""
    from gargantua.repo.agents import InvalidRefs, create, update

    with sync_session_maker() as s:
        t = _seed_type(s)
        s.commit()
        srv = _seed_server(s, type_id=t.id)
        s.commit()
        child = _seed_child(s, parent_id=srv.id)
        s.commit()
        sid, cid = srv.id, child.id

    with sync_session_maker() as s:
        a = create(
            s,
            name="x",
            model="m",
            instructions="i",
            mcp_server_ids=[sid],
            child_resource_ids=[cid],
        )
        s.commit()
        aid = a.id

    with sync_session_maker() as s:
        with pytest.raises(InvalidRefs) as exc:
            update(s, agent_id=aid, mcp_server_ids=[])
    assert exc.value.orphan_child_resource_ids == [cid]


def test_update_ignores_unknown_kwargs(sync_session_maker) -> None:
    from gargantua.repo.agents import create, update

    with sync_session_maker() as s:
        a = create(s, name="x", model="m", instructions="i")
        s.commit()
        aid = a.id

    with sync_session_maker() as s:
        # ``id`` and ``version`` aren't in _UPDATABLE_FIELDS
        update(s, agent_id=aid, id=uuid4(), version=999)
        s.commit()

    with sync_session_maker() as s:
        row = s.get(Agent, aid)
    assert row.id == aid
    assert row.version == 1  # not bumped because no real change


def test_update_can_clear_refs_when_consistent(sync_session_maker) -> None:
    """Clearing both mcp_server_ids and child_resource_ids is valid."""
    from gargantua.repo.agents import create, update

    with sync_session_maker() as s:
        t = _seed_type(s)
        s.commit()
        srv = _seed_server(s, type_id=t.id)
        s.commit()
        child = _seed_child(s, parent_id=srv.id)
        s.commit()
        a = create(
            s,
            name="x",
            model="m",
            instructions="i",
            mcp_server_ids=[srv.id],
            child_resource_ids=[child.id],
        )
        s.commit()
        aid = a.id

    with sync_session_maker() as s:
        update(s, agent_id=aid, mcp_server_ids=[], child_resource_ids=[])
        s.commit()

    with sync_session_maker() as s:
        row = s.get(Agent, aid)
    assert row.mcp_server_ids == []
    assert row.child_resource_ids == []


# ---------------------------------------------------------------------------
# Archive / unarchive
# ---------------------------------------------------------------------------


def test_archive_then_unarchive(sync_session_maker) -> None:
    from gargantua.repo.agents import archive, create, unarchive

    with sync_session_maker() as s:
        a = create(s, name="x", model="m", instructions="i")
        s.commit()
        aid = a.id

    with sync_session_maker() as s:
        r = archive(s, agent_id=aid)
        s.commit()
    assert r.archived_at is not None

    with sync_session_maker() as s:
        r = unarchive(s, agent_id=aid)
        s.commit()
    assert r.archived_at is None


def test_archive_idempotent(sync_session_maker) -> None:
    from gargantua.repo.agents import archive, create

    with sync_session_maker() as s:
        a = create(s, name="x", model="m", instructions="i")
        s.commit()
        aid = a.id

    with sync_session_maker() as s:
        first = archive(s, agent_id=aid)
        first_ts = first.archived_at
        s.commit()

    with sync_session_maker() as s:
        second = archive(s, agent_id=aid)
        s.commit()
    assert second.archived_at == first_ts


def test_archive_missing_id_raises(sync_session_maker) -> None:
    from gargantua.repo.agents import NotFound, archive, unarchive

    with sync_session_maker() as s:
        with pytest.raises(NotFound):
            archive(s, agent_id=uuid4())
        with pytest.raises(NotFound):
            unarchive(s, agent_id=uuid4())


# ---------------------------------------------------------------------------
# Async API smoke
# ---------------------------------------------------------------------------


async def test_async_create_and_get(truncate_db: Engine) -> None:
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from gargantua.repo.agents import acreate, aget_by_id, aget_by_name

    # psycopg3 supports both sync and async — reuse the same DSN.
    dsn = truncate_db.url.render_as_string(hide_password=False)
    async_engine = create_async_engine(dsn, future=True)
    async_sm = async_sessionmaker(async_engine, expire_on_commit=False, class_=AsyncSession)

    try:
        async with async_sm() as s:
            a = await acreate(s, name="async-x", model="m", instructions="i")
            await s.commit()
            aid = a.id

        async with async_sm() as s:
            row = await aget_by_id(s, aid)
            assert row is not None
            assert row.name == "async-x"
            by_name = await aget_by_name(s, "async-x")
            assert by_name.id == aid
    finally:
        await async_engine.dispose()


async def test_async_update_revalidates_refs(truncate_db: Engine) -> None:
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from gargantua.repo.agents import InvalidRefs, acreate, aupdate

    # Seed via sync session to keep the fixture story simple.
    sync_sm = sessionmaker(bind=truncate_db, expire_on_commit=False, future=True)
    with sync_sm() as s:
        t = _seed_type(s)
        s.commit()
        srv = _seed_server(s, type_id=t.id)
        s.commit()
        child = _seed_child(s, parent_id=srv.id)
        s.commit()
        sid, cid = srv.id, child.id

    dsn = truncate_db.url.render_as_string(hide_password=False)
    async_engine = create_async_engine(dsn, future=True)
    async_sm = async_sessionmaker(async_engine, expire_on_commit=False, class_=AsyncSession)

    try:
        async with async_sm() as s:
            a = await acreate(
                s,
                name="async-y",
                model="m",
                instructions="i",
                mcp_server_ids=[sid],
                child_resource_ids=[cid],
            )
            await s.commit()
            aid = a.id

        async with async_sm() as s:
            with pytest.raises(InvalidRefs):
                await aupdate(s, agent_id=aid, mcp_server_ids=[])
    finally:
        await async_engine.dispose()

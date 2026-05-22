"""Repo layer for ``ai.team``.

Focus: mode validation, member-ref validation across the live
``agent`` table, duplicate name handling, and the partial-update /
archive semantics.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from gargantua.db.models import Agent, Team


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def sync_session_maker(truncate_db: Engine) -> sessionmaker:
    return sessionmaker(bind=truncate_db, expire_on_commit=False, future=True)


def _seed_agent(
    s: Session,
    *,
    name: str = "agent-x",
    archived: bool = False,
) -> Agent:
    a = Agent(name=name, model="gpt-5", instructions="i")
    if archived:
        a.archived_at = datetime.now(tz=timezone.utc)
    s.add(a)
    s.flush()
    return a


# ---------------------------------------------------------------------------
# Create — happy path & defaults
# ---------------------------------------------------------------------------


def test_create_minimal_payload(sync_session_maker) -> None:
    from gargantua.repo.teams import create

    with sync_session_maker() as s:
        t = create(s, name="ops", mode="route")
        s.commit()
        tid = t.id

    with sync_session_maker() as s:
        row = s.get(Team, tid)
    assert row.name == "ops"
    assert row.mode == "route"
    assert row.description is None
    assert row.member_agent_ids == []
    assert row.team_config == {}
    assert row.archived_at is None
    assert row.version == 1


def test_create_with_valid_members(sync_session_maker) -> None:
    from gargantua.repo.teams import create

    with sync_session_maker() as s:
        a1 = _seed_agent(s, name="a1")
        a2 = _seed_agent(s, name="a2")
        s.commit()
        ids = [a1.id, a2.id]

    with sync_session_maker() as s:
        team = create(
            s,
            name="ops",
            mode="coordinate",
            member_agent_ids=ids,
        )
        s.commit()
        tid = team.id

    with sync_session_maker() as s:
        row = s.get(Team, tid)
    assert row.member_agent_ids == ids


def test_create_duplicate_name_raises(sync_session_maker) -> None:
    from gargantua.repo.teams import DuplicateName, create

    with sync_session_maker() as s:
        create(s, name="ops", mode="route")
        s.commit()

    with sync_session_maker() as s:
        with pytest.raises(DuplicateName):
            create(s, name="ops", mode="collaborate")


# ---------------------------------------------------------------------------
# Create — mode validation
# ---------------------------------------------------------------------------


def test_create_rejects_unknown_mode(sync_session_maker) -> None:
    from gargantua.repo.teams import InvalidMode, create

    with sync_session_maker() as s:
        with pytest.raises(InvalidMode):
            create(s, name="ops", mode="freeform")


@pytest.mark.parametrize("mode", ["route", "coordinate", "collaborate"])
def test_create_accepts_all_known_modes(sync_session_maker, mode: str) -> None:
    from gargantua.repo.teams import create

    with sync_session_maker() as s:
        create(s, name=f"ops-{mode}", mode=mode)
        s.commit()


# ---------------------------------------------------------------------------
# Create — member validation
# ---------------------------------------------------------------------------


def test_create_rejects_missing_member(sync_session_maker) -> None:
    from gargantua.repo.teams import InvalidMembers, create

    ghost = uuid4()
    with sync_session_maker() as s:
        with pytest.raises(InvalidMembers) as exc:
            create(s, name="ops", mode="route", member_agent_ids=[ghost])
    assert exc.value.missing_agent_ids == [ghost]


def test_create_rejects_archived_member(sync_session_maker) -> None:
    from gargantua.repo.teams import InvalidMembers, create

    with sync_session_maker() as s:
        a = _seed_agent(s, archived=True)
        s.commit()
        aid = a.id

    with sync_session_maker() as s:
        with pytest.raises(InvalidMembers) as exc:
            create(s, name="ops", mode="route", member_agent_ids=[aid])
    assert exc.value.archived_agent_ids == [aid]


def test_create_member_dedupe_is_irrelevant(sync_session_maker) -> None:
    """Submitting the same id twice should still pass validation."""
    from gargantua.repo.teams import create

    with sync_session_maker() as s:
        a = _seed_agent(s)
        s.commit()
        aid = a.id

    with sync_session_maker() as s:
        team = create(
            s,
            name="ops",
            mode="route",
            member_agent_ids=[aid, aid],
        )
        s.commit()
    assert team.member_agent_ids == [aid, aid]


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def test_get_by_id_and_get_by_name(sync_session_maker) -> None:
    from gargantua.repo.teams import create, get_by_id, get_by_name

    with sync_session_maker() as s:
        t = create(s, name="ops", mode="route")
        s.commit()
        tid = t.id

    with sync_session_maker() as s:
        assert get_by_id(s, tid).id == tid
        assert get_by_name(s, "ops").id == tid
        assert get_by_id(s, uuid4()) is None
        assert get_by_name(s, "ghost") is None


def test_list_teams_pagination_and_search(sync_session_maker) -> None:
    from gargantua.repo.teams import create, list_teams

    with sync_session_maker() as s:
        for name in ["alpha", "beta", "betatron", "delta"]:
            create(s, name=name, mode="route")
        s.commit()

    with sync_session_maker() as s:
        rows, total = list_teams(s, page=1, page_size=2)
    assert total == 4
    assert [r.name for r in rows] == ["alpha", "beta"]

    with sync_session_maker() as s:
        rows, total = list_teams(s, search="beta")
    assert total == 2
    assert {r.name for r in rows} == {"beta", "betatron"}


def test_list_teams_excludes_archived_by_default(sync_session_maker) -> None:
    from gargantua.repo.teams import archive, create, list_teams

    with sync_session_maker() as s:
        a = create(s, name="alpha", mode="route")
        create(s, name="beta", mode="route")
        s.commit()
        archive(s, team_id=a.id)
        s.commit()

    with sync_session_maker() as s:
        rows, total = list_teams(s)
    assert total == 1
    assert rows[0].name == "beta"

    with sync_session_maker() as s:
        rows, total = list_teams(s, include_archived=True)
    assert total == 2


def test_list_teams_mode_filter(sync_session_maker) -> None:
    from gargantua.repo.teams import create, list_teams

    with sync_session_maker() as s:
        create(s, name="a", mode="route")
        create(s, name="b", mode="coordinate")
        create(s, name="c", mode="route")
        s.commit()

    with sync_session_maker() as s:
        rows, total = list_teams(s, mode_filter="route")
    assert total == 2
    assert {r.name for r in rows} == {"a", "c"}


def test_list_teams_rejects_unknown_mode_filter(sync_session_maker) -> None:
    from gargantua.repo.teams import InvalidMode, list_teams

    with sync_session_maker() as s:
        with pytest.raises(InvalidMode):
            list_teams(s, mode_filter="freeform")


def test_list_teams_rejects_bad_pagination(sync_session_maker) -> None:
    from gargantua.repo.teams import list_teams

    with sync_session_maker() as s:
        with pytest.raises(ValueError):
            list_teams(s, page=0)
        with pytest.raises(ValueError):
            list_teams(s, page_size=0)


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


def test_update_partial_changes_only_specified_fields(sync_session_maker) -> None:
    from gargantua.repo.teams import create, update

    with sync_session_maker() as s:
        t = create(s, name="ops", mode="route", description="old")
        s.commit()
        tid = t.id

    with sync_session_maker() as s:
        update(s, team_id=tid, description="new")
        s.commit()

    with sync_session_maker() as s:
        row = s.get(Team, tid)
    assert row.name == "ops"  # untouched
    assert row.mode == "route"
    assert row.description == "new"
    assert row.version == 2


def test_update_no_changes_is_noop(sync_session_maker) -> None:
    from gargantua.repo.teams import create, update

    with sync_session_maker() as s:
        t = create(s, name="ops", mode="route")
        s.commit()
        tid = t.id

    with sync_session_maker() as s:
        update(s, team_id=tid)
        s.commit()

    with sync_session_maker() as s:
        row = s.get(Team, tid)
    assert row.version == 1


def test_update_missing_id_raises(sync_session_maker) -> None:
    from gargantua.repo.teams import NotFound, update

    with sync_session_maker() as s:
        with pytest.raises(NotFound):
            update(s, team_id=uuid4(), name="ghost")


def test_update_duplicate_name_raises(sync_session_maker) -> None:
    from gargantua.repo.teams import DuplicateName, create, update

    with sync_session_maker() as s:
        create(s, name="alpha", mode="route")
        b = create(s, name="beta", mode="route")
        s.commit()
        bid = b.id

    with sync_session_maker() as s:
        with pytest.raises(DuplicateName):
            update(s, team_id=bid, name="alpha")


def test_update_rejects_unknown_mode(sync_session_maker) -> None:
    from gargantua.repo.teams import InvalidMode, create, update

    with sync_session_maker() as s:
        t = create(s, name="ops", mode="route")
        s.commit()
        tid = t.id

    with sync_session_maker() as s:
        with pytest.raises(InvalidMode):
            update(s, team_id=tid, mode="freeform")


def test_update_revalidates_members(sync_session_maker) -> None:
    from gargantua.repo.teams import InvalidMembers, create, update

    with sync_session_maker() as s:
        a = _seed_agent(s)
        s.commit()
        t = create(s, name="ops", mode="route", member_agent_ids=[a.id])
        s.commit()
        tid = t.id

    with sync_session_maker() as s:
        with pytest.raises(InvalidMembers) as exc:
            update(s, team_id=tid, member_agent_ids=[uuid4()])
    assert exc.value.missing_agent_ids


def test_update_can_clear_members(sync_session_maker) -> None:
    from gargantua.repo.teams import create, update

    with sync_session_maker() as s:
        a = _seed_agent(s)
        s.commit()
        t = create(s, name="ops", mode="route", member_agent_ids=[a.id])
        s.commit()
        tid = t.id

    with sync_session_maker() as s:
        update(s, team_id=tid, member_agent_ids=[])
        s.commit()

    with sync_session_maker() as s:
        row = s.get(Team, tid)
    assert row.member_agent_ids == []


def test_update_ignores_unknown_kwargs(sync_session_maker) -> None:
    from gargantua.repo.teams import create, update

    with sync_session_maker() as s:
        t = create(s, name="ops", mode="route")
        s.commit()
        tid = t.id

    with sync_session_maker() as s:
        update(s, team_id=tid, id=uuid4(), version=999)
        s.commit()

    with sync_session_maker() as s:
        row = s.get(Team, tid)
    assert row.id == tid
    assert row.version == 1


# ---------------------------------------------------------------------------
# Archive / unarchive
# ---------------------------------------------------------------------------


def test_archive_then_unarchive(sync_session_maker) -> None:
    from gargantua.repo.teams import archive, create, unarchive

    with sync_session_maker() as s:
        t = create(s, name="ops", mode="route")
        s.commit()
        tid = t.id

    with sync_session_maker() as s:
        r = archive(s, team_id=tid)
        s.commit()
    assert r.archived_at is not None

    with sync_session_maker() as s:
        r = unarchive(s, team_id=tid)
        s.commit()
    assert r.archived_at is None


def test_archive_idempotent(sync_session_maker) -> None:
    from gargantua.repo.teams import archive, create

    with sync_session_maker() as s:
        t = create(s, name="ops", mode="route")
        s.commit()
        tid = t.id

    with sync_session_maker() as s:
        first = archive(s, team_id=tid)
        first_ts = first.archived_at
        s.commit()

    with sync_session_maker() as s:
        second = archive(s, team_id=tid)
        s.commit()
    assert second.archived_at == first_ts


def test_archive_missing_id_raises(sync_session_maker) -> None:
    from gargantua.repo.teams import NotFound, archive, unarchive

    with sync_session_maker() as s:
        with pytest.raises(NotFound):
            archive(s, team_id=uuid4())
        with pytest.raises(NotFound):
            unarchive(s, team_id=uuid4())


# ---------------------------------------------------------------------------
# Async API smoke
# ---------------------------------------------------------------------------


async def test_async_create_and_update_revalidates_members(
    truncate_db: Engine,
) -> None:
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from gargantua.repo.teams import (
        InvalidMembers,
        acreate,
        aget_by_id,
        aupdate,
    )

    # Seed an agent via sync session.
    sync_sm = sessionmaker(bind=truncate_db, expire_on_commit=False, future=True)
    with sync_sm() as s:
        a = _seed_agent(s)
        s.commit()
        aid = a.id

    dsn = truncate_db.url.render_as_string(hide_password=False)
    async_engine = create_async_engine(dsn, future=True)
    async_sm = async_sessionmaker(
        async_engine, expire_on_commit=False, class_=AsyncSession
    )
    try:
        async with async_sm() as s:
            team = await acreate(
                s,
                name="async-ops",
                mode="coordinate",
                member_agent_ids=[aid],
            )
            await s.commit()
            tid = team.id

        async with async_sm() as s:
            row = await aget_by_id(s, tid)
            assert row is not None
            assert row.member_agent_ids == [aid]

        async with async_sm() as s:
            with pytest.raises(InvalidMembers):
                await aupdate(s, team_id=tid, member_agent_ids=[uuid4()])
    finally:
        await async_engine.dispose()

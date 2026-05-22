"""Repository for ``gargantua_app.team`` — DB-defined Agno teams.

A team row is a thin orchestration spec: a name, an Agno ``mode``
(``route`` / ``coordinate`` / ``collaborate``), a list of member
``agent.id`` UUIDs, plus a free-form ``team_config`` bag.

Like :mod:`gargantua.repo.agents`, the member references are a
uuid-array Postgres can't FK into, so we validate them in code on
every create / update:

* Every referenced ``agent`` must exist and must not be archived.
* Empty member lists are allowed (an "empty" team is a valid
  draft state in the admin UI).

Mode is validated against :data:`VALID_MODES` *before* we hit the DB
so callers see a typed Python error instead of an opaque check-
constraint violation in the rolled-back transaction.

Typed errors so the route layer can map them onto HTTP status codes:

* :class:`DuplicateName` — ``name`` collision.
* :class:`NotFound`      — ``team_id`` doesn't exist.
* :class:`InvalidMode`   — ``mode`` not in :data:`VALID_MODES`.
* :class:`InvalidMembers` — one or more ``member_agent_ids`` are
  missing or archived.  Carries structured detail so the route can
  surface it as 422 with a useful message.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Final
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from gargantua.db.models import Agent, Team


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


#: Mirrors the DB-level CHECK constraint ``mode_in_known_set`` on
#: ``gargantua_app.team``.  Keep in sync with the model.
VALID_MODES: Final[frozenset[str]] = frozenset(
    {"route", "coordinate", "collaborate"}
)


_UPDATABLE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "name",
        "description",
        "mode",
        "member_agent_ids",
        "team_config",
    }
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RepoError(Exception):
    """Base class for typed errors raised by this module."""


class DuplicateName(RepoError):
    """A team with this ``name`` already exists."""


class NotFound(RepoError):
    """``team_id`` doesn't exist."""


class InvalidMode(RepoError):
    """``mode`` is not in :data:`VALID_MODES`."""


@dataclass
class InvalidMembers(RepoError):
    """One or more ``member_agent_ids`` point at missing or archived rows."""

    missing_agent_ids: list[UUID] = field(default_factory=list)
    archived_agent_ids: list[UUID] = field(default_factory=list)

    def __post_init__(self) -> None:
        parts: list[str] = []
        if self.missing_agent_ids:
            parts.append(f"missing agent_ids={self.missing_agent_ids}")
        if self.archived_agent_ids:
            parts.append(f"archived agent_ids={self.archived_agent_ids}")
        super().__init__("; ".join(parts) if parts else "InvalidMembers")

    @property
    def has_problems(self) -> bool:
        return bool(self.missing_agent_ids or self.archived_agent_ids)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_mode(mode: str | None) -> None:
    if mode is None:
        return
    if mode not in VALID_MODES:
        raise InvalidMode(mode)


def _validate_members_sync(
    session: Session, *, member_agent_ids: list[UUID]
) -> None:
    """Raise :class:`InvalidMembers` if any agent id is missing or archived."""
    missing: list[UUID] = []
    archived: list[UUID] = []

    unique_ids = list({*member_agent_ids})
    if not unique_ids:
        return

    rows: dict[UUID, Agent] = {}
    for row in session.execute(
        select(Agent).where(Agent.id.in_(unique_ids))
    ).scalars():
        rows[row.id] = row

    for aid in unique_ids:
        row = rows.get(aid)
        if row is None:
            missing.append(aid)
        elif row.archived_at is not None:
            archived.append(aid)

    if missing or archived:
        raise InvalidMembers(
            missing_agent_ids=missing, archived_agent_ids=archived
        )


async def _validate_members_async(
    session: AsyncSession, *, member_agent_ids: list[UUID]
) -> None:
    missing: list[UUID] = []
    archived: list[UUID] = []

    unique_ids = list({*member_agent_ids})
    if not unique_ids:
        return

    result = await session.execute(
        select(Agent).where(Agent.id.in_(unique_ids))
    )
    rows: dict[UUID, Agent] = {r.id: r for r in result.scalars()}

    for aid in unique_ids:
        row = rows.get(aid)
        if row is None:
            missing.append(aid)
        elif row.archived_at is not None:
            archived.append(aid)

    if missing or archived:
        raise InvalidMembers(
            missing_agent_ids=missing, archived_agent_ids=archived
        )


def _build_list_query(
    *, search: str | None, include_archived: bool, mode_filter: str | None
):
    stmt = select(Team)
    count_stmt = select(func.count()).select_from(Team)

    if not include_archived:
        stmt = stmt.where(Team.archived_at.is_(None))
        count_stmt = count_stmt.where(Team.archived_at.is_(None))

    if mode_filter is not None:
        # Don't silently accept garbage filters.
        _check_mode(mode_filter)
        stmt = stmt.where(Team.mode == mode_filter)
        count_stmt = count_stmt.where(Team.mode == mode_filter)

    if search:
        pattern = f"%{search.lower()}%"
        like = or_(
            func.lower(Team.name).like(pattern),
            func.lower(Team.description).like(pattern),
        )
        stmt = stmt.where(like)
        count_stmt = count_stmt.where(like)

    stmt = stmt.order_by(Team.name.asc())
    return stmt, count_stmt


def _is_dup_name(exc: IntegrityError) -> bool:
    return "uq_team_name" in str(exc.orig)


# ---------------------------------------------------------------------------
# Sync API
# ---------------------------------------------------------------------------


def get_by_id(session: Session, team_id: UUID) -> Team | None:
    return session.get(Team, team_id)


def get_by_name(session: Session, name: str) -> Team | None:
    return session.execute(
        select(Team).where(Team.name == name)
    ).scalar_one_or_none()


def list_teams(
    session: Session,
    *,
    page: int = 1,
    page_size: int = 50,
    search: str | None = None,
    include_archived: bool = False,
    mode_filter: str | None = None,
) -> tuple[list[Team], int]:
    if page < 1 or page_size < 1:
        raise ValueError("page and page_size must be >= 1")
    stmt, count_stmt = _build_list_query(
        search=search,
        include_archived=include_archived,
        mode_filter=mode_filter,
    )
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = list(session.execute(stmt).scalars().all())
    total = session.execute(count_stmt).scalar_one()
    return rows, total


def create(
    session: Session,
    *,
    name: str,
    mode: str,
    description: str | None = None,
    member_agent_ids: list[UUID] | None = None,
    team_config: dict[str, Any] | None = None,
    created_by: UUID | None = None,
) -> Team:
    """Insert a new team. Validates mode and member refs before flush."""
    _check_mode(mode)
    member_agent_ids = member_agent_ids or []
    _validate_members_sync(session, member_agent_ids=member_agent_ids)

    row = Team(
        name=name,
        mode=mode,
        description=description,
        member_agent_ids=member_agent_ids,
        team_config=team_config or {},
        created_by=created_by,
    )
    session.add(row)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        if _is_dup_name(exc):
            raise DuplicateName(name) from exc
        raise
    session.refresh(row)
    return row


def update(
    session: Session, *, team_id: UUID, **kwargs: Any
) -> Team:
    """Partial update.  Re-validates members when ``member_agent_ids`` is
    in the payload; re-validates mode when ``mode`` is in the payload."""
    row = session.get(Team, team_id)
    if row is None:
        raise NotFound(str(team_id))

    changes: dict[str, Any] = {}
    for k, v in kwargs.items():
        if k not in _UPDATABLE_FIELDS or v is None:
            continue
        changes[k] = v

    if not changes:
        return row

    if "mode" in changes:
        _check_mode(changes["mode"])

    if "member_agent_ids" in changes:
        _validate_members_sync(
            session, member_agent_ids=changes["member_agent_ids"]
        )

    # Capture the would-be name BEFORE flush so we can include it in
    # DuplicateName without lazy-loading from a rolled-back row.
    attempted_name = changes.get("name", row.name)
    for k, v in changes.items():
        setattr(row, k, v)
    row.version = (row.version or 1) + 1

    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        if _is_dup_name(exc):
            raise DuplicateName(attempted_name) from exc
        raise
    session.refresh(row)
    return row


def archive(session: Session, *, team_id: UUID) -> Team:
    row = session.get(Team, team_id)
    if row is None:
        raise NotFound(str(team_id))
    if row.archived_at is not None:
        return row
    row.archived_at = datetime.now(tz=timezone.utc)
    session.flush()
    session.refresh(row)
    return row


def unarchive(session: Session, *, team_id: UUID) -> Team:
    row = session.get(Team, team_id)
    if row is None:
        raise NotFound(str(team_id))
    if row.archived_at is None:
        return row
    row.archived_at = None
    session.flush()
    session.refresh(row)
    return row


# ---------------------------------------------------------------------------
# Async API
# ---------------------------------------------------------------------------


async def aget_by_id(session: AsyncSession, team_id: UUID) -> Team | None:
    return await session.get(Team, team_id)


async def aget_by_name(session: AsyncSession, name: str) -> Team | None:
    result = await session.execute(
        select(Team).where(Team.name == name)
    )
    return result.scalar_one_or_none()


async def alist_teams(
    session: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 50,
    search: str | None = None,
    include_archived: bool = False,
    mode_filter: str | None = None,
) -> tuple[list[Team], int]:
    if page < 1 or page_size < 1:
        raise ValueError("page and page_size must be >= 1")
    stmt, count_stmt = _build_list_query(
        search=search,
        include_archived=include_archived,
        mode_filter=mode_filter,
    )
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = list((await session.execute(stmt)).scalars().all())
    total = (await session.execute(count_stmt)).scalar_one()
    return rows, total


async def acreate(
    session: AsyncSession,
    *,
    name: str,
    mode: str,
    description: str | None = None,
    member_agent_ids: list[UUID] | None = None,
    team_config: dict[str, Any] | None = None,
    created_by: UUID | None = None,
) -> Team:
    _check_mode(mode)
    member_agent_ids = member_agent_ids or []
    await _validate_members_async(
        session, member_agent_ids=member_agent_ids
    )

    row = Team(
        name=name,
        mode=mode,
        description=description,
        member_agent_ids=member_agent_ids,
        team_config=team_config or {},
        created_by=created_by,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        if _is_dup_name(exc):
            raise DuplicateName(name) from exc
        raise
    await session.refresh(row)
    return row


async def aupdate(
    session: AsyncSession, *, team_id: UUID, **kwargs: Any
) -> Team:
    row = await session.get(Team, team_id)
    if row is None:
        raise NotFound(str(team_id))

    changes: dict[str, Any] = {}
    for k, v in kwargs.items():
        if k not in _UPDATABLE_FIELDS or v is None:
            continue
        changes[k] = v

    if not changes:
        return row

    if "mode" in changes:
        _check_mode(changes["mode"])

    if "member_agent_ids" in changes:
        await _validate_members_async(
            session, member_agent_ids=changes["member_agent_ids"]
        )

    attempted_name = changes.get("name", row.name)
    for k, v in changes.items():
        setattr(row, k, v)
    row.version = (row.version or 1) + 1

    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        if _is_dup_name(exc):
            raise DuplicateName(attempted_name) from exc
        raise
    await session.refresh(row)
    return row


async def aarchive(session: AsyncSession, *, team_id: UUID) -> Team:
    row = await session.get(Team, team_id)
    if row is None:
        raise NotFound(str(team_id))
    if row.archived_at is not None:
        return row
    row.archived_at = datetime.now(tz=timezone.utc)
    await session.flush()
    await session.refresh(row)
    return row


async def aunarchive(session: AsyncSession, *, team_id: UUID) -> Team:
    row = await session.get(Team, team_id)
    if row is None:
        raise NotFound(str(team_id))
    if row.archived_at is None:
        return row
    row.archived_at = None
    await session.flush()
    await session.refresh(row)
    return row


__all__ = [
    "VALID_MODES",
    "DuplicateName",
    "InvalidMembers",
    "InvalidMode",
    "NotFound",
    "RepoError",
    "aarchive",
    "acreate",
    "aget_by_id",
    "aget_by_name",
    "alist_teams",
    "archive",
    "aunarchive",
    "aupdate",
    "create",
    "get_by_id",
    "get_by_name",
    "list_teams",
    "unarchive",
    "update",
]

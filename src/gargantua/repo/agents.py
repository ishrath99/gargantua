"""Repository for ``ai.agent`` — DB-defined Agno agents.

An agent row is the operator-authored spec for an Agno
:class:`~agno.agent.Agent`: model, instructions, MCP server / child
resource references, plus a free-form ``agent_config`` bag for
learning, compression, guardrails, etc.

Reference fields (:attr:`mcp_server_ids` and
:attr:`child_resource_ids`) are uuid-arrays — Postgres can't FK into
those, so we validate them in code on every create / update:

* Every referenced ``mcp_server`` must exist and not be archived.
* Every referenced ``mcp_server_child_resource`` must exist and have
  ``enabled = True``.
* Every referenced child resource's ``parent_mcp_server_id`` must be
  one of the agent's ``mcp_server_ids`` — otherwise the agent is
  pointing at a Swagger child whose host server it doesn't even know
  about, which is almost always a UI bug.

Typed errors so the route layer can map them onto HTTP status codes:

* :class:`DuplicateName` — ``name`` collision.
* :class:`NotFound`      — ``agent_id`` doesn't exist.
* :class:`InvalidRefs`   — one or more referenced IDs are missing,
  archived/disabled, or violate the parent-membership rule above.
  Carries structured detail so the route can surface it as 422 with
  a useful message.
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

from gargantua.db.models import (
    Agent,
    MCPServer,
    MCPServerChildResource,
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RepoError(Exception):
    """Base class for typed errors raised by this module."""


class DuplicateName(RepoError):
    """An agent with this ``name`` already exists."""


class NotFound(RepoError):
    """``agent_id`` doesn't exist."""


@dataclass
class InvalidRefs(RepoError):
    """One or more reference fields point at missing/archived/disabled rows.

    Structured so the route layer can produce a precise 422 payload
    instead of a single opaque error string.
    """

    missing_mcp_server_ids: list[UUID] = field(default_factory=list)
    archived_mcp_server_ids: list[UUID] = field(default_factory=list)
    missing_child_resource_ids: list[UUID] = field(default_factory=list)
    disabled_child_resource_ids: list[UUID] = field(default_factory=list)
    #: Child IDs whose parent_mcp_server_id is *not* in mcp_server_ids.
    orphan_child_resource_ids: list[UUID] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Build a stable message so str(exc) is useful for tests / logs.
        parts: list[str] = []
        if self.missing_mcp_server_ids:
            parts.append(f"missing mcp_server_ids={self.missing_mcp_server_ids}")
        if self.archived_mcp_server_ids:
            parts.append(
                f"archived mcp_server_ids={self.archived_mcp_server_ids}"
            )
        if self.missing_child_resource_ids:
            parts.append(
                f"missing child_resource_ids={self.missing_child_resource_ids}"
            )
        if self.disabled_child_resource_ids:
            parts.append(
                f"disabled child_resource_ids={self.disabled_child_resource_ids}"
            )
        if self.orphan_child_resource_ids:
            parts.append(
                f"child_resource_ids whose parent is not in mcp_server_ids="
                f"{self.orphan_child_resource_ids}"
            )
        super().__init__("; ".join(parts) if parts else "InvalidRefs")

    @property
    def has_problems(self) -> bool:
        return any(
            [
                self.missing_mcp_server_ids,
                self.archived_mcp_server_ids,
                self.missing_child_resource_ids,
                self.disabled_child_resource_ids,
                self.orphan_child_resource_ids,
            ]
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_UPDATABLE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "name",
        "description",
        "model",
        "instructions",
        "tools_config",
        "mcp_server_ids",
        "child_resource_ids",
        "agent_config",
    }
)


def _validate_refs_sync(
    session: Session,
    *,
    mcp_server_ids: list[UUID],
    child_resource_ids: list[UUID],
) -> None:
    """Raise :class:`InvalidRefs` if any referenced ID is missing or
    in a state that disqualifies it from being wired into an agent.

    All ID lists are deduplicated before lookup to keep DB load
    proportional to the unique-set size, not the submitted size.
    """
    missing_mcp_server_ids: list[UUID] = []
    archived_mcp_server_ids: list[UUID] = []
    missing_child_resource_ids: list[UUID] = []
    disabled_child_resource_ids: list[UUID] = []
    orphan_child_resource_ids: list[UUID] = []

    unique_server_ids = list({*mcp_server_ids})
    unique_child_ids = list({*child_resource_ids})

    server_rows: dict[UUID, MCPServer] = {}
    if unique_server_ids:
        for row in session.execute(
            select(MCPServer).where(MCPServer.id.in_(unique_server_ids))
        ).scalars():
            server_rows[row.id] = row

    for sid in unique_server_ids:
        row = server_rows.get(sid)
        if row is None:
            missing_mcp_server_ids.append(sid)
        elif row.archived_at is not None:
            archived_mcp_server_ids.append(sid)

    child_rows: dict[UUID, MCPServerChildResource] = {}
    if unique_child_ids:
        for row in session.execute(
            select(MCPServerChildResource).where(
                MCPServerChildResource.id.in_(unique_child_ids)
            )
        ).scalars():
            child_rows[row.id] = row

    for cid in unique_child_ids:
        row = child_rows.get(cid)
        if row is None:
            missing_child_resource_ids.append(cid)
            continue
        if not row.enabled:
            disabled_child_resource_ids.append(cid)
        if row.parent_mcp_server_id not in unique_server_ids:
            orphan_child_resource_ids.append(cid)

    if any(
        [
            missing_mcp_server_ids,
            archived_mcp_server_ids,
            missing_child_resource_ids,
            disabled_child_resource_ids,
            orphan_child_resource_ids,
        ]
    ):
        raise InvalidRefs(
            missing_mcp_server_ids=missing_mcp_server_ids,
            archived_mcp_server_ids=archived_mcp_server_ids,
            missing_child_resource_ids=missing_child_resource_ids,
            disabled_child_resource_ids=disabled_child_resource_ids,
            orphan_child_resource_ids=orphan_child_resource_ids,
        )


async def _validate_refs_async(
    session: AsyncSession,
    *,
    mcp_server_ids: list[UUID],
    child_resource_ids: list[UUID],
) -> None:
    """Async mirror of :func:`_validate_refs_sync`."""
    missing_mcp_server_ids: list[UUID] = []
    archived_mcp_server_ids: list[UUID] = []
    missing_child_resource_ids: list[UUID] = []
    disabled_child_resource_ids: list[UUID] = []
    orphan_child_resource_ids: list[UUID] = []

    unique_server_ids = list({*mcp_server_ids})
    unique_child_ids = list({*child_resource_ids})

    server_rows: dict[UUID, MCPServer] = {}
    if unique_server_ids:
        result = await session.execute(
            select(MCPServer).where(MCPServer.id.in_(unique_server_ids))
        )
        for row in result.scalars():
            server_rows[row.id] = row

    for sid in unique_server_ids:
        row = server_rows.get(sid)
        if row is None:
            missing_mcp_server_ids.append(sid)
        elif row.archived_at is not None:
            archived_mcp_server_ids.append(sid)

    child_rows: dict[UUID, MCPServerChildResource] = {}
    if unique_child_ids:
        result = await session.execute(
            select(MCPServerChildResource).where(
                MCPServerChildResource.id.in_(unique_child_ids)
            )
        )
        for row in result.scalars():
            child_rows[row.id] = row

    for cid in unique_child_ids:
        row = child_rows.get(cid)
        if row is None:
            missing_child_resource_ids.append(cid)
            continue
        if not row.enabled:
            disabled_child_resource_ids.append(cid)
        if row.parent_mcp_server_id not in unique_server_ids:
            orphan_child_resource_ids.append(cid)

    if any(
        [
            missing_mcp_server_ids,
            archived_mcp_server_ids,
            missing_child_resource_ids,
            disabled_child_resource_ids,
            orphan_child_resource_ids,
        ]
    ):
        raise InvalidRefs(
            missing_mcp_server_ids=missing_mcp_server_ids,
            archived_mcp_server_ids=archived_mcp_server_ids,
            missing_child_resource_ids=missing_child_resource_ids,
            disabled_child_resource_ids=disabled_child_resource_ids,
            orphan_child_resource_ids=orphan_child_resource_ids,
        )


def _build_list_query(
    *, search: str | None, include_archived: bool, model_filter: str | None
):
    stmt = select(Agent)
    count_stmt = select(func.count()).select_from(Agent)

    if not include_archived:
        stmt = stmt.where(Agent.archived_at.is_(None))
        count_stmt = count_stmt.where(Agent.archived_at.is_(None))

    if model_filter is not None:
        stmt = stmt.where(Agent.model == model_filter)
        count_stmt = count_stmt.where(Agent.model == model_filter)

    if search:
        pattern = f"%{search.lower()}%"
        like = or_(
            func.lower(Agent.name).like(pattern),
            func.lower(Agent.description).like(pattern),
        )
        stmt = stmt.where(like)
        count_stmt = count_stmt.where(like)

    stmt = stmt.order_by(Agent.name.asc())
    return stmt, count_stmt


def _is_dup_name(exc: IntegrityError) -> bool:
    return "uq_agent_name" in str(exc.orig)


# ---------------------------------------------------------------------------
# Sync API
# ---------------------------------------------------------------------------


def get_by_id(session: Session, agent_id: UUID) -> Agent | None:
    return session.get(Agent, agent_id)


def get_by_name(session: Session, name: str) -> Agent | None:
    return session.execute(
        select(Agent).where(Agent.name == name)
    ).scalar_one_or_none()


def list_agents(
    session: Session,
    *,
    page: int = 1,
    page_size: int = 50,
    search: str | None = None,
    include_archived: bool = False,
    model_filter: str | None = None,
) -> tuple[list[Agent], int]:
    if page < 1 or page_size < 1:
        raise ValueError("page and page_size must be >= 1")
    stmt, count_stmt = _build_list_query(
        search=search,
        include_archived=include_archived,
        model_filter=model_filter,
    )
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = list(session.execute(stmt).scalars().all())
    total = session.execute(count_stmt).scalar_one()
    return rows, total


def create(
    session: Session,
    *,
    name: str,
    model: str,
    instructions: str,
    description: str | None = None,
    tools_config: dict[str, Any] | None = None,
    mcp_server_ids: list[UUID] | None = None,
    child_resource_ids: list[UUID] | None = None,
    agent_config: dict[str, Any] | None = None,
    created_by: UUID | None = None,
) -> Agent:
    """Insert a new agent.  Validates references before flush.

    Wires plenty of defaults so the minimum payload is just
    ``name + model + instructions``.
    """
    mcp_server_ids = mcp_server_ids or []
    child_resource_ids = child_resource_ids or []
    _validate_refs_sync(
        session,
        mcp_server_ids=mcp_server_ids,
        child_resource_ids=child_resource_ids,
    )

    row = Agent(
        name=name,
        model=model,
        instructions=instructions,
        description=description,
        tools_config=tools_config or {},
        mcp_server_ids=mcp_server_ids,
        child_resource_ids=child_resource_ids,
        agent_config=agent_config or {},
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
    session: Session, *, agent_id: UUID, **kwargs: Any
) -> Agent:
    """Partial update.  Re-validates references whenever
    ``mcp_server_ids`` or ``child_resource_ids`` is in the payload."""
    row = session.get(Agent, agent_id)
    if row is None:
        raise NotFound(str(agent_id))

    changes: dict[str, Any] = {}
    for k, v in kwargs.items():
        if k not in _UPDATABLE_FIELDS or v is None:
            continue
        changes[k] = v

    if not changes:
        return row

    # If the refs are being touched, validate the *post-update* world
    # state.  This means: pull the new value if present, otherwise the
    # existing one.  Catches "I'm clearing mcp_server_ids but leaving a
    # child_resource_id whose parent was only in the old set".
    if (
        "mcp_server_ids" in changes
        or "child_resource_ids" in changes
    ):
        next_server_ids = changes.get("mcp_server_ids", row.mcp_server_ids)
        next_child_ids = changes.get(
            "child_resource_ids", row.child_resource_ids
        )
        _validate_refs_sync(
            session,
            mcp_server_ids=next_server_ids,
            child_resource_ids=next_child_ids,
        )

    # Capture the would-be name BEFORE the flush so we can include it in
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


def archive(session: Session, *, agent_id: UUID) -> Agent:
    row = session.get(Agent, agent_id)
    if row is None:
        raise NotFound(str(agent_id))
    if row.archived_at is not None:
        return row
    row.archived_at = datetime.now(tz=timezone.utc)
    session.flush()
    session.refresh(row)
    return row


def unarchive(session: Session, *, agent_id: UUID) -> Agent:
    row = session.get(Agent, agent_id)
    if row is None:
        raise NotFound(str(agent_id))
    if row.archived_at is None:
        return row
    row.archived_at = None
    session.flush()
    session.refresh(row)
    return row


# ---------------------------------------------------------------------------
# Async API
# ---------------------------------------------------------------------------


async def aget_by_id(
    session: AsyncSession, agent_id: UUID
) -> Agent | None:
    return await session.get(Agent, agent_id)


async def aget_by_name(
    session: AsyncSession, name: str
) -> Agent | None:
    result = await session.execute(
        select(Agent).where(Agent.name == name)
    )
    return result.scalar_one_or_none()


async def alist_agents(
    session: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 50,
    search: str | None = None,
    include_archived: bool = False,
    model_filter: str | None = None,
) -> tuple[list[Agent], int]:
    if page < 1 or page_size < 1:
        raise ValueError("page and page_size must be >= 1")
    stmt, count_stmt = _build_list_query(
        search=search,
        include_archived=include_archived,
        model_filter=model_filter,
    )
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = list((await session.execute(stmt)).scalars().all())
    total = (await session.execute(count_stmt)).scalar_one()
    return rows, total


async def acreate(
    session: AsyncSession,
    *,
    name: str,
    model: str,
    instructions: str,
    description: str | None = None,
    tools_config: dict[str, Any] | None = None,
    mcp_server_ids: list[UUID] | None = None,
    child_resource_ids: list[UUID] | None = None,
    agent_config: dict[str, Any] | None = None,
    created_by: UUID | None = None,
) -> Agent:
    mcp_server_ids = mcp_server_ids or []
    child_resource_ids = child_resource_ids or []
    await _validate_refs_async(
        session,
        mcp_server_ids=mcp_server_ids,
        child_resource_ids=child_resource_ids,
    )

    row = Agent(
        name=name,
        model=model,
        instructions=instructions,
        description=description,
        tools_config=tools_config or {},
        mcp_server_ids=mcp_server_ids,
        child_resource_ids=child_resource_ids,
        agent_config=agent_config or {},
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
    session: AsyncSession, *, agent_id: UUID, **kwargs: Any
) -> Agent:
    row = await session.get(Agent, agent_id)
    if row is None:
        raise NotFound(str(agent_id))

    changes: dict[str, Any] = {}
    for k, v in kwargs.items():
        if k not in _UPDATABLE_FIELDS or v is None:
            continue
        changes[k] = v

    if not changes:
        return row

    if (
        "mcp_server_ids" in changes
        or "child_resource_ids" in changes
    ):
        next_server_ids = changes.get("mcp_server_ids", row.mcp_server_ids)
        next_child_ids = changes.get(
            "child_resource_ids", row.child_resource_ids
        )
        await _validate_refs_async(
            session,
            mcp_server_ids=next_server_ids,
            child_resource_ids=next_child_ids,
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


async def aarchive(
    session: AsyncSession, *, agent_id: UUID
) -> Agent:
    row = await session.get(Agent, agent_id)
    if row is None:
        raise NotFound(str(agent_id))
    if row.archived_at is not None:
        return row
    row.archived_at = datetime.now(tz=timezone.utc)
    await session.flush()
    await session.refresh(row)
    return row


async def aunarchive(
    session: AsyncSession, *, agent_id: UUID
) -> Agent:
    row = await session.get(Agent, agent_id)
    if row is None:
        raise NotFound(str(agent_id))
    if row.archived_at is None:
        return row
    row.archived_at = None
    await session.flush()
    await session.refresh(row)
    return row


__all__ = [
    "DuplicateName",
    "InvalidRefs",
    "NotFound",
    "RepoError",
    "aarchive",
    "acreate",
    "aget_by_id",
    "aget_by_name",
    "alist_agents",
    "aunarchive",
    "aupdate",
    "archive",
    "create",
    "get_by_id",
    "get_by_name",
    "list_agents",
    "unarchive",
    "update",
]

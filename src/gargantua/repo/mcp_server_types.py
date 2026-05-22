"""Repository for ``gargantua_app.mcp_server_type`` — the curated catalog of MCP templates.

``mcp_server_type`` rows are **templates**: they declare what an MCP server
of a given kind looks like (mode, default command + args, the
``config_schema`` the admin UI renders into a form, etc.).  Operators
spin up concrete instances in :class:`~gargantua.db.models.MCPServer`
referencing a type via ``type_id``.

Domain errors are typed so callers can map them onto HTTP / CLI exit
codes without inspecting SQL exceptions:

* :class:`DuplicateSlug` — ``slug`` is taken (unique constraint trip).
* :class:`InvalidMode`   — mode not in :data:`VALID_MODES`.
* :class:`NotFound`      — the target ``type_id`` doesn't exist.

Conventions match the users repo: mutating helpers flush + refresh
so the caller can hand the row straight to Pydantic ``model_validate``
without tripping a lazy-load from a sync context.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from gargantua.db.models import MCPServerType

VALID_MODES: Final[frozenset[str]] = frozenset({"stdio", "sse", "streamable_http"})

#: Sentinel used by partial-update helpers to distinguish "leave the
#: field alone" (``None`` *or* not provided) from "clear it".  Today
#: every clearable field is a collection where an empty value already
#: means "clear", so we don't need a stronger sentinel — but keep the
#: distinction documented because the wire contract follows it.


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RepoError(Exception):
    """Base class for typed errors raised by this module."""


class DuplicateSlug(RepoError):
    """A type with this ``slug`` already exists."""


class InvalidMode(RepoError):
    """``mode`` is not one of :data:`VALID_MODES`."""


class NotFound(RepoError):
    """The target ``type_id`` doesn't exist."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_mode(mode: str) -> None:
    if mode not in VALID_MODES:
        raise InvalidMode(f"mode must be one of {sorted(VALID_MODES)}, got {mode!r}")


def _build_list_query(
    *,
    mode: str | None,
    search: str | None,
    include_archived: bool,
) -> tuple[Any, Any]:
    stmt = select(MCPServerType)
    count_stmt = select(func.count()).select_from(MCPServerType)

    if mode is not None:
        stmt = stmt.where(MCPServerType.mode == mode)
        count_stmt = count_stmt.where(MCPServerType.mode == mode)

    if not include_archived:
        stmt = stmt.where(MCPServerType.archived_at.is_(None))
        count_stmt = count_stmt.where(MCPServerType.archived_at.is_(None))

    if search:
        # Case-insensitive substring search across slug + name.  Sufficient
        # for an operator-curated catalog — never grows beyond a few dozen rows.
        pattern = f"%{search.lower()}%"
        like = or_(
            func.lower(MCPServerType.slug).like(pattern),
            func.lower(MCPServerType.name).like(pattern),
        )
        stmt = stmt.where(like)
        count_stmt = count_stmt.where(like)

    stmt = stmt.order_by(MCPServerType.slug.asc())
    return stmt, count_stmt


def _apply_create_kwargs(*, slug: str, name: str, mode: str, **extra: Any) -> dict[str, Any]:
    """Build the constructor kwargs for ``MCPServerType(...)`` from the
    create-call signature.  Extracted so the sync and async variants stay
    in lock-step.
    """
    return {
        "slug": slug,
        "name": name,
        "mode": mode,
        "description": extra.get("description"),
        "default_command": extra.get("default_command"),
        "default_args": extra.get("default_args", []),
        "config_schema": extra.get("config_schema", []),
        "default_env_vars": extra.get("default_env_vars", {}),
        "optional_env_vars": extra.get("optional_env_vars", {}),
        "default_swagger_url": extra.get("default_swagger_url"),
        "supports_swagger_child": extra.get("supports_swagger_child", False),
    }


_UPDATABLE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "name",
        "description",
        "mode",
        "default_command",
        "default_args",
        "config_schema",
        "default_env_vars",
        "optional_env_vars",
        "default_swagger_url",
        "supports_swagger_child",
    }
)


def _collect_update_changes(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Filter the kwargs dict to fields we know how to update, dropping
    keys whose value is ``None`` (partial-update semantics)."""
    changes: dict[str, Any] = {}
    for key, value in kwargs.items():
        if key not in _UPDATABLE_FIELDS or value is None:
            continue
        changes[key] = value
    if "mode" in changes:
        _validate_mode(changes["mode"])
    return changes


# ---------------------------------------------------------------------------
# Sync API
# ---------------------------------------------------------------------------


def get_by_id(session: Session, type_id: UUID) -> MCPServerType | None:
    return session.get(MCPServerType, type_id)


def get_by_slug(session: Session, slug: str) -> MCPServerType | None:
    return session.execute(
        select(MCPServerType).where(MCPServerType.slug == slug)
    ).scalar_one_or_none()


def list_types(
    session: Session,
    *,
    page: int = 1,
    page_size: int = 50,
    mode: str | None = None,
    search: str | None = None,
    include_archived: bool = False,
) -> tuple[list[MCPServerType], int]:
    if page < 1 or page_size < 1:
        raise ValueError("page and page_size must be >= 1")

    stmt, count_stmt = _build_list_query(
        mode=mode, search=search, include_archived=include_archived
    )
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = list(session.execute(stmt).scalars().all())
    total = session.execute(count_stmt).scalar_one()
    return rows, total


def create(
    session: Session,
    *,
    slug: str,
    name: str,
    mode: str,
    **extra: Any,
) -> MCPServerType:
    """Insert a new catalog row.  Flushes + refreshes; never commits."""
    _validate_mode(mode)
    row = MCPServerType(**_apply_create_kwargs(slug=slug, name=name, mode=mode, **extra))
    session.add(row)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        if "uq_mcp_server_type_slug" in str(exc.orig) or "slug" in str(exc.orig).lower():
            raise DuplicateSlug(slug) from exc
        raise
    session.refresh(row)
    return row


def update(
    session: Session,
    *,
    type_id: UUID,
    **kwargs: Any,
) -> MCPServerType:
    """Partial update.  Fields set to ``None`` (or absent) are left alone."""
    row = session.get(MCPServerType, type_id)
    if row is None:
        raise NotFound(str(type_id))

    changes = _collect_update_changes(kwargs)
    if not changes:
        # No-op — return the row unmodified.  Caller's responsibility to
        # decide whether to write an audit entry (typically: don't).
        return row

    for k, v in changes.items():
        setattr(row, k, v)
    row.version = (row.version or 1) + 1
    session.flush()
    session.refresh(row)
    return row


def archive(session: Session, *, type_id: UUID) -> MCPServerType:
    row = session.get(MCPServerType, type_id)
    if row is None:
        raise NotFound(str(type_id))
    if row.archived_at is not None:
        return row  # idempotent
    row.archived_at = datetime.now(tz=UTC)
    session.flush()
    session.refresh(row)
    return row


def unarchive(session: Session, *, type_id: UUID) -> MCPServerType:
    row = session.get(MCPServerType, type_id)
    if row is None:
        raise NotFound(str(type_id))
    if row.archived_at is None:
        return row  # idempotent
    row.archived_at = None
    session.flush()
    session.refresh(row)
    return row


# ---------------------------------------------------------------------------
# Async API
# ---------------------------------------------------------------------------


async def aget_by_id(session: AsyncSession, type_id: UUID) -> MCPServerType | None:
    return await session.get(MCPServerType, type_id)


async def aget_by_slug(session: AsyncSession, slug: str) -> MCPServerType | None:
    result = await session.execute(select(MCPServerType).where(MCPServerType.slug == slug))
    return result.scalar_one_or_none()


async def alist_types(
    session: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 50,
    mode: str | None = None,
    search: str | None = None,
    include_archived: bool = False,
) -> tuple[list[MCPServerType], int]:
    if page < 1 or page_size < 1:
        raise ValueError("page and page_size must be >= 1")
    stmt, count_stmt = _build_list_query(
        mode=mode, search=search, include_archived=include_archived
    )
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = list((await session.execute(stmt)).scalars().all())
    total = (await session.execute(count_stmt)).scalar_one()
    return rows, total


async def acreate(
    session: AsyncSession,
    *,
    slug: str,
    name: str,
    mode: str,
    **extra: Any,
) -> MCPServerType:
    _validate_mode(mode)
    row = MCPServerType(**_apply_create_kwargs(slug=slug, name=name, mode=mode, **extra))
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        if "uq_mcp_server_type_slug" in str(exc.orig) or "slug" in str(exc.orig).lower():
            raise DuplicateSlug(slug) from exc
        raise
    await session.refresh(row)
    return row


async def aupdate(
    session: AsyncSession,
    *,
    type_id: UUID,
    **kwargs: Any,
) -> MCPServerType:
    row = await session.get(MCPServerType, type_id)
    if row is None:
        raise NotFound(str(type_id))
    changes = _collect_update_changes(kwargs)
    if not changes:
        return row
    for k, v in changes.items():
        setattr(row, k, v)
    row.version = (row.version or 1) + 1
    await session.flush()
    await session.refresh(row)
    return row


async def aarchive(session: AsyncSession, *, type_id: UUID) -> MCPServerType:
    row = await session.get(MCPServerType, type_id)
    if row is None:
        raise NotFound(str(type_id))
    if row.archived_at is not None:
        return row
    row.archived_at = datetime.now(tz=UTC)
    await session.flush()
    await session.refresh(row)
    return row


async def aunarchive(session: AsyncSession, *, type_id: UUID) -> MCPServerType:
    row = await session.get(MCPServerType, type_id)
    if row is None:
        raise NotFound(str(type_id))
    if row.archived_at is None:
        return row
    row.archived_at = None
    await session.flush()
    await session.refresh(row)
    return row

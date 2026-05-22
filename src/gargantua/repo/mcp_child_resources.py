"""Repository for ``gargantua_app.mcp_server_child_resource``.

Child resources hang off an MCP server.  Today only ``type='swagger'``
is supported; the design allows new ``type`` values to land without a
schema change.

State machine: child resources use an ``enabled`` boolean (not a
soft-archive timestamp like ``mcp_server``).  Disabling is reversible
and instantaneous; there's no eventual-deletion semantic to preserve,
so a flag is the cleanest fit.

Encryption: ``headers`` (a JSON object of HTTP headers, often
containing ``Authorization: Bearer ...``) is AES-256-GCM-encrypted
under the active KEK exactly like ``mcp_server.env_vars`` in the
sibling repo.

Typed errors:

* :class:`DuplicateName`       — ``(parent_mcp_server_id, name)`` collision.
* :class:`NotFound`            — the target id doesn't exist.
* :class:`InvalidParentRef`    — parent server doesn't exist, is archived,
  or its type doesn't support child resources.
* :class:`InvalidChildType`    — ``type`` not in the allowed set
  (currently just ``'swagger'``).
* :class:`KekMismatchOnRead`   — stored ciphertext is under a different KEK.
"""

from __future__ import annotations

from typing import Any, Final
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from gargantua.db.models import (
    MCPServer,
    MCPServerChildResource,
    MCPServerType,
)
from gargantua.secrets import KekMismatch, decrypt_json, encrypt_json


VALID_CHILD_TYPES: Final[frozenset[str]] = frozenset({"swagger"})


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RepoError(Exception):
    """Base class for typed errors raised by this module."""


class DuplicateName(RepoError):
    """A child resource with this ``(parent_mcp_server_id, name)`` already exists."""


class NotFound(RepoError):
    """The target ``child_id`` doesn't exist."""


class InvalidParentRef(RepoError):
    """Parent server doesn't exist, is archived, or doesn't support children."""


class InvalidChildType(RepoError):
    """``type`` is not in :data:`VALID_CHILD_TYPES`."""


class KekMismatchOnRead(RepoError):
    """Stored ciphertext was encrypted under a different KEK than the active one."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_type(child_type: str) -> None:
    if child_type not in VALID_CHILD_TYPES:
        raise InvalidChildType(
            f"type must be one of {sorted(VALID_CHILD_TYPES)}, got {child_type!r}"
        )


def _check_parent_sync(session: Session, parent_id: UUID) -> MCPServer:
    parent = session.get(MCPServer, parent_id)
    if parent is None:
        raise InvalidParentRef(f"mcp_server {parent_id} does not exist")
    if parent.archived_at is not None:
        raise InvalidParentRef(
            f"mcp_server {parent_id} is archived; "
            "child resources can only attach to active servers."
        )
    # Confirm the parent's type supports children.
    parent_type = session.get(MCPServerType, parent.type_id)
    if parent_type is None or not parent_type.supports_swagger_child:
        raise InvalidParentRef(
            f"mcp_server {parent_id} (type {parent.type_id}) does not "
            "support child resources."
        )
    return parent


async def _check_parent_async(
    session: AsyncSession, parent_id: UUID
) -> MCPServer:
    parent = await session.get(MCPServer, parent_id)
    if parent is None:
        raise InvalidParentRef(f"mcp_server {parent_id} does not exist")
    if parent.archived_at is not None:
        raise InvalidParentRef(
            f"mcp_server {parent_id} is archived; "
            "child resources can only attach to active servers."
        )
    parent_type = await session.get(MCPServerType, parent.type_id)
    if parent_type is None or not parent_type.supports_swagger_child:
        raise InvalidParentRef(
            f"mcp_server {parent_id} (type {parent.type_id}) does not "
            "support child resources."
        )
    return parent


def _encrypt_headers_or_none(headers: dict[str, Any] | None):
    if headers is None or headers == {}:
        return None, None, None
    return encrypt_json(headers)


def _build_list_query(
    *,
    parent_id: UUID,
    child_type: str | None,
    search: str | None,
    include_disabled: bool,
):
    stmt = select(MCPServerChildResource).where(
        MCPServerChildResource.parent_mcp_server_id == parent_id
    )
    count_stmt = (
        select(func.count())
        .select_from(MCPServerChildResource)
        .where(MCPServerChildResource.parent_mcp_server_id == parent_id)
    )

    if child_type is not None:
        stmt = stmt.where(MCPServerChildResource.type == child_type)
        count_stmt = count_stmt.where(MCPServerChildResource.type == child_type)

    if not include_disabled:
        stmt = stmt.where(MCPServerChildResource.enabled.is_(True))
        count_stmt = count_stmt.where(MCPServerChildResource.enabled.is_(True))

    if search:
        pattern = f"%{search.lower()}%"
        like = or_(
            func.lower(MCPServerChildResource.name).like(pattern),
            func.lower(MCPServerChildResource.url).like(pattern),
        )
        stmt = stmt.where(like)
        count_stmt = count_stmt.where(like)

    stmt = stmt.order_by(MCPServerChildResource.name.asc())
    return stmt, count_stmt


def _is_dup_name(exc: IntegrityError) -> bool:
    detail = str(exc.orig)
    return (
        "uq_mcp_server_child_resource_parent_mcp_server_id_name" in detail
        or "uq_mcp_server_child_parent_name" in detail
    )


# ---------------------------------------------------------------------------
# Decryption surface
# ---------------------------------------------------------------------------


def decrypt_headers(child: MCPServerChildResource) -> dict[str, Any]:
    """Return the plaintext headers dict for a child resource.

    Empty rows (all three secret columns NULL) return ``{}``.

    Raises :class:`KekMismatchOnRead` if the stored ciphertext is under
    a different KEK than the one currently configured.
    """
    if (
        child.headers is None
        and child.headers_iv is None
        and child.headers_kek_id is None
    ):
        return {}
    if child.headers is None or child.headers_iv is None or child.headers_kek_id is None:
        raise KekMismatchOnRead(
            f"mcp_server_child_resource {child.id}: headers columns are "
            f"inconsistent (some NULL, some not).  Refusing to read."
        )
    try:
        return decrypt_json(
            ciphertext=bytes(child.headers),
            iv=bytes(child.headers_iv),
            kek_id=child.headers_kek_id,
        )
    except KekMismatch as exc:
        raise KekMismatchOnRead(str(exc)) from exc


# ---------------------------------------------------------------------------
# Sync API
# ---------------------------------------------------------------------------


def get_by_id(
    session: Session, child_id: UUID
) -> MCPServerChildResource | None:
    return session.get(MCPServerChildResource, child_id)


def list_children(
    session: Session,
    *,
    parent_id: UUID,
    page: int = 1,
    page_size: int = 50,
    child_type: str | None = None,
    search: str | None = None,
    include_disabled: bool = False,
) -> tuple[list[MCPServerChildResource], int]:
    if page < 1 or page_size < 1:
        raise ValueError("page and page_size must be >= 1")
    stmt, count_stmt = _build_list_query(
        parent_id=parent_id,
        child_type=child_type,
        search=search,
        include_disabled=include_disabled,
    )
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = list(session.execute(stmt).scalars().all())
    total = session.execute(count_stmt).scalar_one()
    return rows, total


def create(
    session: Session,
    *,
    parent_id: UUID,
    child_type: str,
    name: str,
    url: str,
    headers: dict[str, Any] | None = None,
) -> MCPServerChildResource:
    _validate_type(child_type)
    _check_parent_sync(session, parent_id)
    ct, iv, kek_id = _encrypt_headers_or_none(headers)
    row = MCPServerChildResource(
        parent_mcp_server_id=parent_id,
        type=child_type,
        name=name,
        url=url,
        headers=ct,
        headers_iv=iv,
        headers_kek_id=kek_id,
    )
    session.add(row)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        if _is_dup_name(exc):
            raise DuplicateName(
                f"child resource (parent={parent_id}, name={name!r}) already exists"
            ) from exc
        raise
    session.refresh(row)
    return row


def update(
    session: Session,
    *,
    child_id: UUID,
    name: str | None = None,
    url: str | None = None,
    headers: dict[str, Any] | None = None,
) -> MCPServerChildResource:
    """Partial update.  ``None`` means "don't touch".

    ``headers`` is replace-all (same semantics as ``env_vars`` in
    :mod:`gargantua.repo.mcp_servers`).  ``type`` is intentionally not
    updatable — change of type means a new row.  ``enabled`` is toggled
    via :func:`enable` / :func:`disable`, not here.
    """
    row = session.get(MCPServerChildResource, child_id)
    if row is None:
        raise NotFound(str(child_id))

    changed = False
    if name is not None and name != row.name:
        row.name = name
        changed = True
    if url is not None and url != row.url:
        row.url = url
        changed = True
    if headers is not None:
        ct, iv, kek_id = _encrypt_headers_or_none(headers)
        row.headers = ct
        row.headers_iv = iv
        row.headers_kek_id = kek_id
        changed = True

    if not changed:
        return row

    row.version = (row.version or 1) + 1
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        if _is_dup_name(exc):
            raise DuplicateName(
                f"another child resource (parent={row.parent_mcp_server_id}, "
                f"name={row.name!r}) already exists"
            ) from exc
        raise
    session.refresh(row)
    return row


def enable(session: Session, *, child_id: UUID) -> MCPServerChildResource:
    row = session.get(MCPServerChildResource, child_id)
    if row is None:
        raise NotFound(str(child_id))
    if row.enabled:
        return row
    row.enabled = True
    session.flush()
    session.refresh(row)
    return row


def disable(session: Session, *, child_id: UUID) -> MCPServerChildResource:
    row = session.get(MCPServerChildResource, child_id)
    if row is None:
        raise NotFound(str(child_id))
    if not row.enabled:
        return row
    row.enabled = False
    session.flush()
    session.refresh(row)
    return row


# ---------------------------------------------------------------------------
# Async API
# ---------------------------------------------------------------------------


async def aget_by_id(
    session: AsyncSession, child_id: UUID
) -> MCPServerChildResource | None:
    return await session.get(MCPServerChildResource, child_id)


async def aget_parent_map(
    session: AsyncSession, child_ids: list[UUID]
) -> dict[UUID, UUID]:
    """Return ``{child_id: parent_mcp_server_id}`` for every row found.

    Used by the runtime route to group an agent's ``child_resource_ids``
    by their parent MCP server before leasing.  Only loads two columns
    (``id``, ``parent_mcp_server_id``) — the headers + URL come later
    when the cache rebuilds the entry.

    Children that don't exist (deleted out from under the agent) are
    simply absent from the returned map; the caller decides how to
    handle that (typically: 503 + log, since the cache will surface
    them as ``ServerNotFound`` on acquire anyway).
    """
    if not child_ids:
        return {}
    stmt = select(
        MCPServerChildResource.id, MCPServerChildResource.parent_mcp_server_id
    ).where(MCPServerChildResource.id.in_(child_ids))
    result = await session.execute(stmt)
    return {row.id: row.parent_mcp_server_id for row in result}


async def alist_children(
    session: AsyncSession,
    *,
    parent_id: UUID,
    page: int = 1,
    page_size: int = 50,
    child_type: str | None = None,
    search: str | None = None,
    include_disabled: bool = False,
) -> tuple[list[MCPServerChildResource], int]:
    if page < 1 or page_size < 1:
        raise ValueError("page and page_size must be >= 1")
    stmt, count_stmt = _build_list_query(
        parent_id=parent_id,
        child_type=child_type,
        search=search,
        include_disabled=include_disabled,
    )
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = list((await session.execute(stmt)).scalars().all())
    total = (await session.execute(count_stmt)).scalar_one()
    return rows, total


async def acreate(
    session: AsyncSession,
    *,
    parent_id: UUID,
    child_type: str,
    name: str,
    url: str,
    headers: dict[str, Any] | None = None,
) -> MCPServerChildResource:
    _validate_type(child_type)
    await _check_parent_async(session, parent_id)
    ct, iv, kek_id = _encrypt_headers_or_none(headers)
    row = MCPServerChildResource(
        parent_mcp_server_id=parent_id,
        type=child_type,
        name=name,
        url=url,
        headers=ct,
        headers_iv=iv,
        headers_kek_id=kek_id,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        if _is_dup_name(exc):
            raise DuplicateName(
                f"child resource (parent={parent_id}, name={name!r}) already exists"
            ) from exc
        raise
    await session.refresh(row)
    return row


async def aupdate(
    session: AsyncSession,
    *,
    child_id: UUID,
    name: str | None = None,
    url: str | None = None,
    headers: dict[str, Any] | None = None,
) -> MCPServerChildResource:
    row = await session.get(MCPServerChildResource, child_id)
    if row is None:
        raise NotFound(str(child_id))

    changed = False
    if name is not None and name != row.name:
        row.name = name
        changed = True
    if url is not None and url != row.url:
        row.url = url
        changed = True
    if headers is not None:
        ct, iv, kek_id = _encrypt_headers_or_none(headers)
        row.headers = ct
        row.headers_iv = iv
        row.headers_kek_id = kek_id
        changed = True

    if not changed:
        return row

    row.version = (row.version or 1) + 1
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        if _is_dup_name(exc):
            raise DuplicateName(
                f"another child resource (parent={row.parent_mcp_server_id}, "
                f"name={row.name!r}) already exists"
            ) from exc
        raise
    await session.refresh(row)
    return row


async def aenable(
    session: AsyncSession, *, child_id: UUID
) -> MCPServerChildResource:
    row = await session.get(MCPServerChildResource, child_id)
    if row is None:
        raise NotFound(str(child_id))
    if row.enabled:
        return row
    row.enabled = True
    await session.flush()
    await session.refresh(row)
    return row


async def adisable(
    session: AsyncSession, *, child_id: UUID
) -> MCPServerChildResource:
    row = await session.get(MCPServerChildResource, child_id)
    if row is None:
        raise NotFound(str(child_id))
    if not row.enabled:
        return row
    row.enabled = False
    await session.flush()
    await session.refresh(row)
    return row


__all__ = [
    "DuplicateName",
    "InvalidChildType",
    "InvalidParentRef",
    "KekMismatchOnRead",
    "NotFound",
    "RepoError",
    "VALID_CHILD_TYPES",
    "acreate",
    "adisable",
    "aenable",
    "aget_by_id",
    "alist_children",
    "aupdate",
    "create",
    "decrypt_headers",
    "disable",
    "enable",
    "get_by_id",
    "list_children",
    "update",
]

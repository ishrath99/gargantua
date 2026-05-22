"""Repository for ``ai.mcp_server`` — instantiated MCP servers.

A server row glues together:

* a **type** (``mcp_server_type``) — the template,
* an **env_tag** — which environment this instance is for (``prod`` /
  ``dev`` / etc.); uniqueness is per ``(type_id, name, env_tag)``,
* an **(encrypted) env_vars dict** — the credentials and config that
  parameterize the type's ``config_schema``.

This module hides AES-256-GCM encryption from its callers: ``create``
and ``update`` accept a plaintext ``dict`` of env vars and write
ciphertext; :func:`decrypt_env_vars` turns a row back into the
plaintext dict.

Typed errors:

* :class:`DuplicateName`     — ``(type_id, name, env_tag)`` collision.
* :class:`NotFound`          — ``server_id`` doesn't exist.
* :class:`InvalidTypeRef`    — ``type_id`` doesn't point at a type, or
  the type is archived (can't instantiate from a retired template).
* :class:`KekMismatchOnRead` — stored ciphertext is under a different
  KEK than the active MASTER_KEY; usually means a half-finished
  rotation.  The route layer maps this to ``503``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Final
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from gargantua.db.models import MCPServer, MCPServerType
from gargantua.secrets import KekMismatch, decrypt_json, encrypt_json


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RepoError(Exception):
    """Base class for typed errors raised by this module."""


class DuplicateName(RepoError):
    """A server with this ``(type_id, name, env_tag)`` already exists."""


class NotFound(RepoError):
    """The target ``server_id`` doesn't exist."""


class InvalidTypeRef(RepoError):
    """``type_id`` doesn't reference an active ``mcp_server_type`` row."""


class KekMismatchOnRead(RepoError):
    """Stored ciphertext was encrypted under a different KEK than the
    one currently configured.  Means a KEK rotation was started but
    not run to completion, or MASTER_KEY was changed without
    rotation."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_UPDATABLE_FIELDS: Final[frozenset[str]] = frozenset(
    {"name", "env_tag", "command", "args"}
)


def _check_type_active_sync(session: Session, type_id: UUID) -> None:
    """Confirm the referenced type exists and is not archived.

    We do this in code (not just via FK) so the error is a clean
    :class:`InvalidTypeRef` with a useful message rather than an
    opaque integrity violation.
    """
    t = session.get(MCPServerType, type_id)
    if t is None:
        raise InvalidTypeRef(f"mcp_server_type {type_id} does not exist")
    if t.archived_at is not None:
        raise InvalidTypeRef(
            f"mcp_server_type {type_id} ({t.slug!r}) is archived; "
            "create from an active type or unarchive it first."
        )


async def _check_type_active_async(
    session: AsyncSession, type_id: UUID
) -> None:
    t = await session.get(MCPServerType, type_id)
    if t is None:
        raise InvalidTypeRef(f"mcp_server_type {type_id} does not exist")
    if t.archived_at is not None:
        raise InvalidTypeRef(
            f"mcp_server_type {type_id} ({t.slug!r}) is archived; "
            "create from an active type or unarchive it first."
        )


def _encrypt_env_vars_or_none(env_vars: dict[str, Any] | None):
    """Return (ciphertext, iv, kek_id) — or (None, None, None) if the dict is None / empty.

    Empty dicts collapse to all-NULL columns so the row never sits in a
    half-encrypted state.  The KEK rotation worker treats all-NULL rows
    as ``skipped_empty``.
    """
    if env_vars is None or env_vars == {}:
        return None, None, None
    return encrypt_json(env_vars)


def _build_list_query(
    *,
    type_id: UUID | None,
    env_tag: str | None,
    search: str | None,
    include_archived: bool,
):
    stmt = select(MCPServer)
    count_stmt = select(func.count()).select_from(MCPServer)

    if type_id is not None:
        stmt = stmt.where(MCPServer.type_id == type_id)
        count_stmt = count_stmt.where(MCPServer.type_id == type_id)

    if env_tag is not None:
        stmt = stmt.where(MCPServer.env_tag == env_tag)
        count_stmt = count_stmt.where(MCPServer.env_tag == env_tag)

    if not include_archived:
        stmt = stmt.where(MCPServer.archived_at.is_(None))
        count_stmt = count_stmt.where(MCPServer.archived_at.is_(None))

    if search:
        pattern = f"%{search.lower()}%"
        like = or_(
            func.lower(MCPServer.name).like(pattern),
            func.lower(MCPServer.env_tag).like(pattern),
        )
        stmt = stmt.where(like)
        count_stmt = count_stmt.where(like)

    stmt = stmt.order_by(
        MCPServer.env_tag.asc(), MCPServer.name.asc()
    )
    return stmt, count_stmt


def _is_dup_name(exc: IntegrityError) -> bool:
    """Catches the (type_id, name, env_tag) unique-constraint violation.

    The migration named the constraint ``uq_mcp_server_type_id_name_env_tag``;
    the model declares ``uq_mcp_server_type_name_env``.  Match either so
    a future rename doesn't silently turn this into a 500.
    """
    detail = str(exc.orig)
    return (
        "uq_mcp_server_type_id_name_env_tag" in detail
        or "uq_mcp_server_type_name_env" in detail
    )


# ---------------------------------------------------------------------------
# Decryption surface (used by routes/CLI to project rows -> plaintext dicts)
# ---------------------------------------------------------------------------


def decrypt_env_vars(server: MCPServer) -> dict[str, Any]:
    """Return the plaintext env_vars dict for a server row.

    Empty rows (all three secret columns NULL) return ``{}``.

    Raises :class:`KekMismatchOnRead` if the stored ciphertext is under
    a different KEK than the one currently configured.  The route
    layer should map this to ``503 Service Unavailable`` with a
    rotation hint — the data isn't lost, but operators must finish
    the in-progress rotation before reads can succeed.
    """
    if (
        server.env_vars is None
        and server.env_var_iv is None
        and server.env_var_kek_id is None
    ):
        return {}
    if server.env_vars is None or server.env_var_iv is None or server.env_var_kek_id is None:
        # Schema permits this but it's never expected — surface loudly
        # rather than silently swallowing partial state.
        raise KekMismatchOnRead(
            f"mcp_server {server.id}: env_vars columns are inconsistent "
            f"(some NULL, some not).  Refusing to read."
        )
    try:
        return decrypt_json(
            ciphertext=bytes(server.env_vars),
            iv=bytes(server.env_var_iv),
            kek_id=server.env_var_kek_id,
        )
    except KekMismatch as exc:
        raise KekMismatchOnRead(str(exc)) from exc


# ---------------------------------------------------------------------------
# Sync API
# ---------------------------------------------------------------------------


def get_by_id(session: Session, server_id: UUID) -> MCPServer | None:
    return session.get(MCPServer, server_id)


def list_servers(
    session: Session,
    *,
    page: int = 1,
    page_size: int = 50,
    type_id: UUID | None = None,
    env_tag: str | None = None,
    search: str | None = None,
    include_archived: bool = False,
) -> tuple[list[MCPServer], int]:
    if page < 1 or page_size < 1:
        raise ValueError("page and page_size must be >= 1")
    stmt, count_stmt = _build_list_query(
        type_id=type_id,
        env_tag=env_tag,
        search=search,
        include_archived=include_archived,
    )
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = list(session.execute(stmt).scalars().all())
    total = session.execute(count_stmt).scalar_one()
    return rows, total


def create(
    session: Session,
    *,
    type_id: UUID,
    name: str,
    env_tag: str,
    env_vars: dict[str, Any] | None = None,
    command: str | None = None,
    args: list[Any] | None = None,
    created_by: UUID | None = None,
) -> MCPServer:
    """Insert a new server, encrypting ``env_vars`` under the active KEK."""
    _check_type_active_sync(session, type_id)
    ct, iv, kek_id = _encrypt_env_vars_or_none(env_vars)
    row = MCPServer(
        type_id=type_id,
        name=name,
        env_tag=env_tag,
        env_vars=ct,
        env_var_iv=iv,
        env_var_kek_id=kek_id,
        command=command,
        args=args if args is not None else [],
        created_by=created_by,
    )
    session.add(row)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        if _is_dup_name(exc):
            raise DuplicateName(
                f"server (type_id={type_id}, name={name!r}, env_tag={env_tag!r}) already exists"
            ) from exc
        raise
    session.refresh(row)
    return row


def update(
    session: Session,
    *,
    server_id: UUID,
    name: str | None = None,
    env_tag: str | None = None,
    env_vars: dict[str, Any] | None = None,
    command: str | None = None,
    args: list[Any] | None = None,
) -> MCPServer:
    """Partial update.  ``None`` means "don't touch".

    ``env_vars`` is *replace-all*: passing a dict replaces the whole
    stored dict; passing ``{}`` clears it (writes NULLs).  Per-key
    rotation should be done at the route layer by reading the current
    dict, mutating one key, and PATCHing the whole map back.
    """
    row = session.get(MCPServer, server_id)
    if row is None:
        raise NotFound(str(server_id))

    changed = False
    if name is not None and name != row.name:
        row.name = name
        changed = True
    if env_tag is not None and env_tag != row.env_tag:
        row.env_tag = env_tag
        changed = True
    if command is not None and command != row.command:
        row.command = command
        changed = True
    if args is not None and args != row.args:
        row.args = args
        changed = True

    if env_vars is not None:
        # Re-encrypt unconditionally on submission: fresh IV is cheap
        # and the alternative (decrypt-then-compare) needs the active
        # KEK at hand, which we may not have during a rotation window.
        # Spurious version bumps on identical resubmissions are
        # acceptable; the audit row at the route layer surfaces the
        # before/after, so a no-op resubmit shows up as such.
        ct, iv, kek_id = _encrypt_env_vars_or_none(env_vars)
        row.env_vars = ct
        row.env_var_iv = iv
        row.env_var_kek_id = kek_id
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
                f"another server (type_id={row.type_id}, name={row.name!r}, "
                f"env_tag={row.env_tag!r}) already exists"
            ) from exc
        raise
    session.refresh(row)
    return row


def archive(session: Session, *, server_id: UUID) -> MCPServer:
    row = session.get(MCPServer, server_id)
    if row is None:
        raise NotFound(str(server_id))
    if row.archived_at is not None:
        return row
    row.archived_at = datetime.now(tz=timezone.utc)
    session.flush()
    session.refresh(row)
    return row


def unarchive(session: Session, *, server_id: UUID) -> MCPServer:
    row = session.get(MCPServer, server_id)
    if row is None:
        raise NotFound(str(server_id))
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
    session: AsyncSession, server_id: UUID
) -> MCPServer | None:
    return await session.get(MCPServer, server_id)


async def alist_servers(
    session: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 50,
    type_id: UUID | None = None,
    env_tag: str | None = None,
    search: str | None = None,
    include_archived: bool = False,
) -> tuple[list[MCPServer], int]:
    if page < 1 or page_size < 1:
        raise ValueError("page and page_size must be >= 1")
    stmt, count_stmt = _build_list_query(
        type_id=type_id,
        env_tag=env_tag,
        search=search,
        include_archived=include_archived,
    )
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = list((await session.execute(stmt)).scalars().all())
    total = (await session.execute(count_stmt)).scalar_one()
    return rows, total


async def acreate(
    session: AsyncSession,
    *,
    type_id: UUID,
    name: str,
    env_tag: str,
    env_vars: dict[str, Any] | None = None,
    command: str | None = None,
    args: list[Any] | None = None,
    created_by: UUID | None = None,
) -> MCPServer:
    await _check_type_active_async(session, type_id)
    ct, iv, kek_id = _encrypt_env_vars_or_none(env_vars)
    row = MCPServer(
        type_id=type_id,
        name=name,
        env_tag=env_tag,
        env_vars=ct,
        env_var_iv=iv,
        env_var_kek_id=kek_id,
        command=command,
        args=args if args is not None else [],
        created_by=created_by,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        if _is_dup_name(exc):
            raise DuplicateName(
                f"server (type_id={type_id}, name={name!r}, env_tag={env_tag!r}) already exists"
            ) from exc
        raise
    await session.refresh(row)
    return row


async def aupdate(
    session: AsyncSession,
    *,
    server_id: UUID,
    name: str | None = None,
    env_tag: str | None = None,
    env_vars: dict[str, Any] | None = None,
    command: str | None = None,
    args: list[Any] | None = None,
) -> MCPServer:
    row = await session.get(MCPServer, server_id)
    if row is None:
        raise NotFound(str(server_id))

    changed = False
    if name is not None and name != row.name:
        row.name = name
        changed = True
    if env_tag is not None and env_tag != row.env_tag:
        row.env_tag = env_tag
        changed = True
    if command is not None and command != row.command:
        row.command = command
        changed = True
    if args is not None and args != row.args:
        row.args = args
        changed = True

    if env_vars is not None:
        # See sync update() for rationale: always re-encrypt on submission.
        ct, iv, kek_id = _encrypt_env_vars_or_none(env_vars)
        row.env_vars = ct
        row.env_var_iv = iv
        row.env_var_kek_id = kek_id
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
                f"another server (type_id={row.type_id}, name={row.name!r}, "
                f"env_tag={row.env_tag!r}) already exists"
            ) from exc
        raise
    await session.refresh(row)
    return row


async def aarchive(
    session: AsyncSession, *, server_id: UUID
) -> MCPServer:
    row = await session.get(MCPServer, server_id)
    if row is None:
        raise NotFound(str(server_id))
    if row.archived_at is not None:
        return row
    row.archived_at = datetime.now(tz=timezone.utc)
    await session.flush()
    await session.refresh(row)
    return row


async def aunarchive(
    session: AsyncSession, *, server_id: UUID
) -> MCPServer:
    row = await session.get(MCPServer, server_id)
    if row is None:
        raise NotFound(str(server_id))
    if row.archived_at is None:
        return row
    row.archived_at = None
    await session.flush()
    await session.refresh(row)
    return row


# Re-export the helper from gargantua.secrets so route + CLI layers can
# import everything secret-related from one place.
__all__ = [
    "DuplicateName",
    "InvalidTypeRef",
    "KekMismatchOnRead",
    "NotFound",
    "RepoError",
    "aarchive",
    "acreate",
    "aget_by_id",
    "alist_servers",
    "aunarchive",
    "aupdate",
    "archive",
    "create",
    "decrypt_env_vars",
    "get_by_id",
    "list_servers",
    "unarchive",
    "update",
]

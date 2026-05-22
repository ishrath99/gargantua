"""Admin router: ``/admin/users/*`` + ``/admin/audit/*`` + ``/admin/mcp-server-types/*``.

Every route here is gated by :func:`gargantua.auth.require_admin`, so a
caller without ``agent_os:admin`` in their access token sees a 403.

Each mutating route writes an :class:`~gargantua.db.models.AuditLog`
entry in the *same* transaction as the change itself.  If the underlying
mutation fails (e.g. unique-violation), the audit row is rolled back too
— the audit log can never disagree with the row it describes.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from gargantua.api.schemas import (
    SECRET_PLACEHOLDER,
    AgentCreateIn,
    AgentListOut,
    AgentOut,
    AgentTemplateListOut,
    AgentTemplateOut,
    AgentUpdateIn,
    AuditLogListOut,
    AuditLogOut,
    MCPCacheEntryOut,
    MCPCacheListOut,
    MCPServerChildResourceCreateIn,
    MCPServerChildResourceListOut,
    MCPServerChildResourceOut,
    MCPServerChildResourceUpdateIn,
    MCPServerCreateIn,
    MCPServerListOut,
    MCPServerOut,
    MCPServerTypeCreateIn,
    MCPServerTypeListOut,
    MCPServerTypeOut,
    MCPServerTypeUpdateIn,
    MCPServerUpdateIn,
    TeamCreateIn,
    TeamListOut,
    TeamOut,
    TeamUpdateIn,
    UserCreateIn,
    UserListOut,
    UserOut,
    UserRoleUpdateIn,
)
from gargantua.auth import TokenClaims, require_admin
from gargantua.db.models import (
    Agent,
    MCPServer,
    MCPServerChildResource,
    MCPServerType,
    Team,
    User,
)
from gargantua.db.session import get_session
from gargantua.mcp_cache import MCPCache
from gargantua.repo import agents as agents_repo
from gargantua.repo import audit as audit_repo
from gargantua.repo import mcp_child_resources as children_repo
from gargantua.repo import mcp_server_types as types_repo
from gargantua.repo import mcp_servers as servers_repo
from gargantua.repo import teams as teams_repo
from gargantua.repo import users as users_repo
from gargantua.templates import (
    AgentTemplate,
    TemplateNotFound,
    load_template_by_slug,
    load_templates,
)


router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user_to_audit_dict(user: User) -> dict[str, Any]:
    """Project a ``User`` row into the JSON shape we store in ``audit_log.{before,after}``.

    Deliberately excludes ``password_hash`` so the audit log never carries
    credential material, even in an encrypted form.
    """
    return {
        "id": str(user.id),
        "username": user.username,
        "role": user.role,
        "is_active": user.is_active,
    }


def _actor_id(claims: TokenClaims) -> UUID:
    """Convert the JWT ``sub`` claim into a UUID; raise 401 if malformed."""
    try:
        return UUID(claims.sub)
    except (ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token has no valid subject",
        ) from exc


# ---------------------------------------------------------------------------
# /admin/users
# ---------------------------------------------------------------------------


@router.get("/users", response_model=UserListOut, tags=["admin"])
async def list_users_route(
    session: Annotated[AsyncSession, Depends(get_session)],
    _claims: Annotated[TokenClaims, Depends(require_admin)],
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    role: str | None = Query(
        None,
        description="Filter by role (admin or user).",
        pattern=r"^(admin|user)$",
    ),
    search: str | None = Query(
        None,
        description="Case-insensitive substring match on username.",
        max_length=255,
    ),
    include_inactive: bool = Query(
        False,
        description="If true, deactivated users are included in the listing.",
    ),
) -> UserListOut:
    rows, total = await users_repo.alist_users(
        session,
        page=page,
        page_size=page_size,
        role=role,
        search=search,
        include_inactive=include_inactive,
    )
    return UserListOut(
        page=page,
        page_size=page_size,
        total=total,
        items=[UserOut.model_validate(r) for r in rows],
    )


@router.get("/users/{user_id}", response_model=UserOut, tags=["admin"])
async def get_user_route(
    user_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _claims: Annotated[TokenClaims, Depends(require_admin)],
) -> UserOut:
    user = await users_repo.aget_by_id(session, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return UserOut.model_validate(user)


@router.post(
    "/users",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    tags=["admin"],
)
async def create_user_route(
    body: UserCreateIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    claims: Annotated[TokenClaims, Depends(require_admin)],
) -> UserOut:
    try:
        user = await users_repo.acreate_user(
            session,
            username=body.username,
            password=body.password,
            role=body.role,
        )
    except users_repo.DuplicateUsername as exc:
        raise HTTPException(status_code=409, detail="Username already exists") from exc
    except users_repo.InvalidRole as exc:
        # Belt-and-braces — the Pydantic regex should have already caught this.
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    await audit_repo.arecord(
        session,
        actor_id=_actor_id(claims),
        action="user.create",
        target_type="user",
        target_id=user.id,
        before=None,
        after=_user_to_audit_dict(user),
    )
    await session.commit()
    return UserOut.model_validate(user)


@router.patch(
    "/users/{user_id}/role", response_model=UserOut, tags=["admin"]
)
async def update_user_role_route(
    user_id: UUID,
    body: UserRoleUpdateIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    claims: Annotated[TokenClaims, Depends(require_admin)],
) -> UserOut:
    existing = await users_repo.aget_by_id(session, user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="User not found")
    before = _user_to_audit_dict(existing)

    # No-op short-circuit *before* touching the DB: avoids a wasted
    # UPDATE / rollback dance and the lazy-load hazard that comes with
    # using a rolled-back instance from sync ``model_validate``.
    if existing.role == body.role:
        return UserOut.model_validate(existing)

    try:
        user = await users_repo.aset_role(
            session, user_id=user_id, new_role=body.role
        )
    except users_repo.UserNotFound as exc:
        raise HTTPException(status_code=404, detail="User not found") from exc
    except users_repo.LastAdminError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await audit_repo.arecord(
        session,
        actor_id=_actor_id(claims),
        action="user.role_update",
        target_type="user",
        target_id=user.id,
        before=before,
        after=_user_to_audit_dict(user),
    )
    await session.commit()
    return UserOut.model_validate(user)


@router.post(
    "/users/{user_id}/deactivate", response_model=UserOut, tags=["admin"]
)
async def deactivate_user_route(
    user_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    claims: Annotated[TokenClaims, Depends(require_admin)],
) -> UserOut:
    return await _set_user_active(session, claims, user_id=user_id, is_active=False)


@router.post(
    "/users/{user_id}/activate", response_model=UserOut, tags=["admin"]
)
async def activate_user_route(
    user_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    claims: Annotated[TokenClaims, Depends(require_admin)],
) -> UserOut:
    return await _set_user_active(session, claims, user_id=user_id, is_active=True)


async def _set_user_active(
    session: AsyncSession,
    claims: TokenClaims,
    *,
    user_id: UUID,
    is_active: bool,
) -> UserOut:
    existing = await users_repo.aget_by_id(session, user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="User not found")
    before = _user_to_audit_dict(existing)

    # No-op short-circuit — don't write an audit row for a non-change.
    if existing.is_active is is_active:
        return UserOut.model_validate(existing)

    try:
        user = await users_repo.aset_active(
            session, user_id=user_id, is_active=is_active
        )
    except users_repo.UserNotFound as exc:
        raise HTTPException(status_code=404, detail="User not found") from exc
    except users_repo.LastAdminError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    action = "user.activate" if is_active else "user.deactivate"
    await audit_repo.arecord(
        session,
        actor_id=_actor_id(claims),
        action=action,
        target_type="user",
        target_id=user.id,
        before=before,
        after=_user_to_audit_dict(user),
    )
    await session.commit()
    return UserOut.model_validate(user)


# ---------------------------------------------------------------------------
# /admin/audit
# ---------------------------------------------------------------------------


@router.get("/audit", response_model=AuditLogListOut, tags=["admin"])
async def list_audit_route(
    session: Annotated[AsyncSession, Depends(get_session)],
    _claims: Annotated[TokenClaims, Depends(require_admin)],
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    actor_id: UUID | None = Query(None),
    target_type: str | None = Query(None, max_length=64),
    target_id: UUID | None = Query(None),
    action: str | None = Query(None, max_length=64),
) -> AuditLogListOut:
    rows, total = await audit_repo.alist_audit(
        session,
        page=page,
        page_size=page_size,
        actor_id=actor_id,
        target_type=target_type,
        target_id=target_id,
        action=action,
    )
    return AuditLogListOut(
        page=page,
        page_size=page_size,
        total=total,
        items=[AuditLogOut.model_validate(r) for r in rows],
    )


@router.get("/audit/{entry_id}", response_model=AuditLogOut, tags=["admin"])
async def get_audit_route(
    entry_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _claims: Annotated[TokenClaims, Depends(require_admin)],
) -> AuditLogOut:
    from gargantua.db.models import AuditLog

    row = await session.get(AuditLog, entry_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Audit entry not found")
    return AuditLogOut.model_validate(row)


# ---------------------------------------------------------------------------
# /admin/mcp-server-types — catalog of MCP server templates
# ---------------------------------------------------------------------------


def _type_to_audit_dict(row: MCPServerType) -> dict[str, Any]:
    """Project an ``MCPServerType`` row into the audit payload shape.

    Catalog rows have no secrets, but we still serialize through a
    deliberate projection so the audit shape is stable across model
    edits.
    """
    return {
        "id": str(row.id),
        "slug": row.slug,
        "name": row.name,
        "description": row.description,
        "mode": row.mode,
        "default_command": row.default_command,
        "default_args": row.default_args,
        "config_schema": row.config_schema,
        "default_env_vars": row.default_env_vars,
        "optional_env_vars": row.optional_env_vars,
        "default_swagger_url": row.default_swagger_url,
        "supports_swagger_child": row.supports_swagger_child,
        "version": row.version,
        "archived_at": row.archived_at.isoformat() if row.archived_at else None,
    }


@router.get(
    "/mcp-server-types", response_model=MCPServerTypeListOut, tags=["admin"]
)
async def list_mcp_server_types_route(
    session: Annotated[AsyncSession, Depends(get_session)],
    _claims: Annotated[TokenClaims, Depends(require_admin)],
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    mode: str | None = Query(
        None,
        description="Filter by transport mode.",
        pattern=r"^(stdio|sse|streamable_http)$",
    ),
    search: str | None = Query(
        None, description="Case-insensitive substring match on slug or name.", max_length=255
    ),
    include_archived: bool = Query(
        False, description="If true, archived types are included in the listing."
    ),
) -> MCPServerTypeListOut:
    rows, total = await types_repo.alist_types(
        session,
        page=page,
        page_size=page_size,
        mode=mode,
        search=search,
        include_archived=include_archived,
    )
    return MCPServerTypeListOut(
        page=page,
        page_size=page_size,
        total=total,
        items=[MCPServerTypeOut.model_validate(r) for r in rows],
    )


@router.get(
    "/mcp-server-types/{type_id}",
    response_model=MCPServerTypeOut,
    tags=["admin"],
)
async def get_mcp_server_type_route(
    type_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _claims: Annotated[TokenClaims, Depends(require_admin)],
) -> MCPServerTypeOut:
    row = await types_repo.aget_by_id(session, type_id)
    if row is None:
        raise HTTPException(status_code=404, detail="MCP server type not found")
    return MCPServerTypeOut.model_validate(row)


@router.post(
    "/mcp-server-types",
    response_model=MCPServerTypeOut,
    status_code=status.HTTP_201_CREATED,
    tags=["admin"],
)
async def create_mcp_server_type_route(
    body: MCPServerTypeCreateIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    claims: Annotated[TokenClaims, Depends(require_admin)],
) -> MCPServerTypeOut:
    try:
        row = await types_repo.acreate(
            session,
            slug=body.slug,
            name=body.name,
            mode=body.mode,
            description=body.description,
            default_command=body.default_command,
            default_args=body.default_args,
            config_schema=[f.model_dump() for f in body.config_schema],
            default_env_vars=body.default_env_vars,
            optional_env_vars=body.optional_env_vars,
            default_swagger_url=body.default_swagger_url,
            supports_swagger_child=body.supports_swagger_child,
        )
    except types_repo.DuplicateSlug as exc:
        raise HTTPException(
            status_code=409, detail=f"Slug '{body.slug}' already exists"
        ) from exc
    except types_repo.InvalidMode as exc:
        # Belt-and-braces; Pydantic regex would normally catch this.
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    await audit_repo.arecord(
        session,
        actor_id=_actor_id(claims),
        action="mcp_server_type.create",
        target_type="mcp_server_type",
        target_id=row.id,
        before=None,
        after=_type_to_audit_dict(row),
    )
    await session.commit()
    return MCPServerTypeOut.model_validate(row)


@router.patch(
    "/mcp-server-types/{type_id}",
    response_model=MCPServerTypeOut,
    tags=["admin"],
)
async def update_mcp_server_type_route(
    type_id: UUID,
    body: MCPServerTypeUpdateIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    claims: Annotated[TokenClaims, Depends(require_admin)],
) -> MCPServerTypeOut:
    existing = await types_repo.aget_by_id(session, type_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="MCP server type not found")
    before = _type_to_audit_dict(existing)

    # Normalize config_schema (list of ConfigSchemaField -> list of dict)
    # before handing the kwargs to the repo.  Other fields pass through.
    update_kwargs: dict[str, Any] = body.model_dump(exclude_unset=True)
    if "config_schema" in update_kwargs and update_kwargs["config_schema"] is not None:
        update_kwargs["config_schema"] = [
            f if isinstance(f, dict) else f.model_dump()
            for f in update_kwargs["config_schema"]
        ]

    # Empty body / all-None payload -> no-op, no audit row.
    if not any(v is not None for v in update_kwargs.values()):
        return MCPServerTypeOut.model_validate(existing)

    try:
        row = await types_repo.aupdate(session, type_id=type_id, **update_kwargs)
    except types_repo.NotFound as exc:
        raise HTTPException(status_code=404, detail="MCP server type not found") from exc
    except types_repo.InvalidMode as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    after = _type_to_audit_dict(row)
    if before == after:
        # Repo treated the change as a no-op (e.g. setting a field to its
        # current value via the partial-update API).  Don't audit.
        return MCPServerTypeOut.model_validate(row)

    await audit_repo.arecord(
        session,
        actor_id=_actor_id(claims),
        action="mcp_server_type.update",
        target_type="mcp_server_type",
        target_id=row.id,
        before=before,
        after=after,
    )
    await session.commit()
    return MCPServerTypeOut.model_validate(row)


@router.post(
    "/mcp-server-types/{type_id}/archive",
    response_model=MCPServerTypeOut,
    tags=["admin"],
)
async def archive_mcp_server_type_route(
    type_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    claims: Annotated[TokenClaims, Depends(require_admin)],
) -> MCPServerTypeOut:
    return await _toggle_archive(session, claims, type_id=type_id, archive=True)


@router.post(
    "/mcp-server-types/{type_id}/unarchive",
    response_model=MCPServerTypeOut,
    tags=["admin"],
)
async def unarchive_mcp_server_type_route(
    type_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    claims: Annotated[TokenClaims, Depends(require_admin)],
) -> MCPServerTypeOut:
    return await _toggle_archive(session, claims, type_id=type_id, archive=False)


async def _toggle_archive(
    session: AsyncSession,
    claims: TokenClaims,
    *,
    type_id: UUID,
    archive: bool,
) -> MCPServerTypeOut:
    existing = await types_repo.aget_by_id(session, type_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="MCP server type not found")

    # No-op short-circuit BEFORE the repo call so we never write a
    # redundant audit row and never touch the DB unnecessarily.
    already_in_state = (
        (archive and existing.archived_at is not None)
        or (not archive and existing.archived_at is None)
    )
    if already_in_state:
        return MCPServerTypeOut.model_validate(existing)

    before = _type_to_audit_dict(existing)
    try:
        if archive:
            row = await types_repo.aarchive(session, type_id=type_id)
        else:
            row = await types_repo.aunarchive(session, type_id=type_id)
    except types_repo.NotFound as exc:
        raise HTTPException(status_code=404, detail="MCP server type not found") from exc

    await audit_repo.arecord(
        session,
        actor_id=_actor_id(claims),
        action="mcp_server_type.archive" if archive else "mcp_server_type.unarchive",
        target_type="mcp_server_type",
        target_id=row.id,
        before=before,
        after=_type_to_audit_dict(row),
    )
    await session.commit()
    return MCPServerTypeOut.model_validate(row)


# ---------------------------------------------------------------------------
# /admin/mcp-servers — instantiated MCP servers
# ---------------------------------------------------------------------------


def _mask_env_vars(
    plaintext: dict[str, Any],
    type_row: MCPServerType | None,
) -> dict[str, Any]:
    """Project ``plaintext`` for response/audit serialisation.

    Rules:

    * Key declared as ``is_secret=true`` in the type's ``config_schema``
      → masked.
    * Key declared as ``is_secret=false`` → revealed.
    * Key not in the schema (drift / typo) → masked, on the safer side
      of "we don't know what this is".

    Falls back to "mask everything" when the type can't be loaded —
    better to under-share than to leak.
    """
    if type_row is None:
        return {k: SECRET_PLACEHOLDER for k in plaintext}

    schema_by_name = {
        field.get("name"): field for field in (type_row.config_schema or [])
    }
    out: dict[str, Any] = {}
    for k, v in plaintext.items():
        field = schema_by_name.get(k)
        if field is None or field.get("is_secret", False):
            out[k] = SECRET_PLACEHOLDER
        else:
            out[k] = v
    return out


async def _server_to_out(
    session: AsyncSession, row: MCPServer
) -> MCPServerOut:
    """Build the masked-on-read response projection.

    Raises :class:`HTTPException(503)` if the stored ciphertext is
    encrypted under a different KEK than the one currently configured —
    the data is intact but the operator must finish the KEK rotation.
    """
    try:
        plaintext = servers_repo.decrypt_env_vars(row)
    except servers_repo.KekMismatchOnRead as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Server env_vars are encrypted under a different KEK. "
                "Run `gargantua-admin rotate-kek` to finish rotation, "
                f"then retry. ({exc})"
            ),
        ) from exc

    type_row = await session.get(MCPServerType, row.type_id)
    masked = _mask_env_vars(plaintext, type_row)
    return MCPServerOut(
        id=row.id,
        type_id=row.type_id,
        name=row.name,
        env_tag=row.env_tag,
        command=row.command,
        args=row.args,
        env_vars=masked,
        archived_at=row.archived_at,
        version=row.version,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _server_to_audit_dict(row: MCPServer, masked_env_vars: dict[str, Any]) -> dict[str, Any]:
    """Audit payload uses the same *masked* env_vars projection.

    The audit log is a privileged read surface but still application-
    readable; secrets must never land there in plaintext.
    """
    return {
        "id": str(row.id),
        "type_id": str(row.type_id),
        "name": row.name,
        "env_tag": row.env_tag,
        "command": row.command,
        "args": row.args,
        "env_vars": masked_env_vars,
        "archived_at": row.archived_at.isoformat() if row.archived_at else None,
        "version": row.version,
    }


async def _audit_dict_for_server(
    session: AsyncSession, row: MCPServer
) -> dict[str, Any]:
    """Convenience: build the masked audit projection from a row."""
    plaintext = servers_repo.decrypt_env_vars(row)
    type_row = await session.get(MCPServerType, row.type_id)
    return _server_to_audit_dict(row, _mask_env_vars(plaintext, type_row))


@router.get(
    "/mcp-servers", response_model=MCPServerListOut, tags=["admin"]
)
async def list_mcp_servers_route(
    session: Annotated[AsyncSession, Depends(get_session)],
    _claims: Annotated[TokenClaims, Depends(require_admin)],
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    type_id: UUID | None = Query(None),
    env_tag: str | None = Query(None, max_length=32),
    search: str | None = Query(None, max_length=255),
    include_archived: bool = Query(False),
) -> MCPServerListOut:
    rows, total = await servers_repo.alist_servers(
        session,
        page=page,
        page_size=page_size,
        type_id=type_id,
        env_tag=env_tag,
        search=search,
        include_archived=include_archived,
    )
    items = [await _server_to_out(session, r) for r in rows]
    return MCPServerListOut(
        page=page, page_size=page_size, total=total, items=items
    )


@router.get(
    "/mcp-servers/{server_id}",
    response_model=MCPServerOut,
    tags=["admin"],
)
async def get_mcp_server_route(
    server_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _claims: Annotated[TokenClaims, Depends(require_admin)],
) -> MCPServerOut:
    row = await servers_repo.aget_by_id(session, server_id)
    if row is None:
        raise HTTPException(status_code=404, detail="MCP server not found")
    return await _server_to_out(session, row)


@router.post(
    "/mcp-servers",
    response_model=MCPServerOut,
    status_code=status.HTTP_201_CREATED,
    tags=["admin"],
)
async def create_mcp_server_route(
    body: MCPServerCreateIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    claims: Annotated[TokenClaims, Depends(require_admin)],
) -> MCPServerOut:
    try:
        row = await servers_repo.acreate(
            session,
            type_id=body.type_id,
            name=body.name,
            env_tag=body.env_tag,
            env_vars=body.env_vars,
            command=body.command,
            args=body.args,
            created_by=_actor_id(claims),
        )
    except servers_repo.InvalidTypeRef as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except servers_repo.DuplicateName as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    after = await _audit_dict_for_server(session, row)
    await audit_repo.arecord(
        session,
        actor_id=_actor_id(claims),
        action="mcp_server.create",
        target_type="mcp_server",
        target_id=row.id,
        before=None,
        after=after,
    )
    await session.commit()
    return await _server_to_out(session, row)


@router.patch(
    "/mcp-servers/{server_id}",
    response_model=MCPServerOut,
    tags=["admin"],
)
async def update_mcp_server_route(
    server_id: UUID,
    body: MCPServerUpdateIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    claims: Annotated[TokenClaims, Depends(require_admin)],
) -> MCPServerOut:
    existing = await servers_repo.aget_by_id(session, server_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="MCP server not found")

    update_kwargs = body.model_dump(exclude_unset=True)
    if not any(v is not None for v in update_kwargs.values()):
        return await _server_to_out(session, existing)

    # Capture BEFORE state with masked env_vars.  If we can't decrypt
    # (KekMismatch), surface as 503 — never silently overwrite.
    try:
        before = await _audit_dict_for_server(session, existing)
    except servers_repo.KekMismatchOnRead as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Cannot read existing env_vars under the active KEK; "
                f"finish rotate-kek before patching this server. ({exc})"
            ),
        ) from exc

    try:
        row = await servers_repo.aupdate(
            session, server_id=server_id, **update_kwargs
        )
    except servers_repo.DuplicateName as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except servers_repo.NotFound as exc:
        raise HTTPException(status_code=404, detail="MCP server not found") from exc

    after = await _audit_dict_for_server(session, row)
    if before == after:
        return await _server_to_out(session, row)

    await audit_repo.arecord(
        session,
        actor_id=_actor_id(claims),
        action="mcp_server.update",
        target_type="mcp_server",
        target_id=row.id,
        before=before,
        after=after,
    )
    await session.commit()
    return await _server_to_out(session, row)


@router.post(
    "/mcp-servers/{server_id}/archive",
    response_model=MCPServerOut,
    tags=["admin"],
)
async def archive_mcp_server_route(
    server_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    claims: Annotated[TokenClaims, Depends(require_admin)],
) -> MCPServerOut:
    return await _toggle_server_archive(
        session, claims, server_id=server_id, archive=True
    )


@router.post(
    "/mcp-servers/{server_id}/unarchive",
    response_model=MCPServerOut,
    tags=["admin"],
)
async def unarchive_mcp_server_route(
    server_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    claims: Annotated[TokenClaims, Depends(require_admin)],
) -> MCPServerOut:
    return await _toggle_server_archive(
        session, claims, server_id=server_id, archive=False
    )


async def _toggle_server_archive(
    session: AsyncSession,
    claims: TokenClaims,
    *,
    server_id: UUID,
    archive: bool,
) -> MCPServerOut:
    existing = await servers_repo.aget_by_id(session, server_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="MCP server not found")

    already = (
        (archive and existing.archived_at is not None)
        or (not archive and existing.archived_at is None)
    )
    if already:
        return await _server_to_out(session, existing)

    try:
        before = await _audit_dict_for_server(session, existing)
    except servers_repo.KekMismatchOnRead as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if archive:
        row = await servers_repo.aarchive(session, server_id=server_id)
    else:
        row = await servers_repo.aunarchive(session, server_id=server_id)

    after = await _audit_dict_for_server(session, row)
    await audit_repo.arecord(
        session,
        actor_id=_actor_id(claims),
        action="mcp_server.archive" if archive else "mcp_server.unarchive",
        target_type="mcp_server",
        target_id=row.id,
        before=before,
        after=after,
    )
    await session.commit()
    return await _server_to_out(session, row)


# ---------------------------------------------------------------------------
# /admin/mcp-servers/{server_id}/child-resources — Swagger sub-resources
# ---------------------------------------------------------------------------


def _mask_headers(plaintext: dict[str, Any]) -> dict[str, Any]:
    """Headers are unconditionally masked.

    Unlike server env_vars, there's no per-field schema to consult; HTTP
    headers attached to a tool integration are almost always credentials
    (``Authorization``, ``X-Api-Key``, …).  Keep the keys visible so the
    UI can render an "edit" form, but never the values.
    """
    return {k: SECRET_PLACEHOLDER for k in plaintext}


def _child_to_audit_dict(
    row: MCPServerChildResource, masked_headers: dict[str, Any]
) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "parent_mcp_server_id": str(row.parent_mcp_server_id),
        "type": row.type,
        "name": row.name,
        "url": row.url,
        "headers": masked_headers,
        "enabled": row.enabled,
        "version": row.version,
    }


def _child_to_out(row: MCPServerChildResource) -> MCPServerChildResourceOut:
    try:
        plaintext = children_repo.decrypt_headers(row)
    except children_repo.KekMismatchOnRead as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Child resource headers are encrypted under a different KEK. "
                "Run `gargantua-admin rotate-kek` to finish rotation, "
                f"then retry. ({exc})"
            ),
        ) from exc
    return MCPServerChildResourceOut(
        id=row.id,
        parent_mcp_server_id=row.parent_mcp_server_id,
        type=row.type,
        name=row.name,
        url=row.url,
        headers=_mask_headers(plaintext),
        enabled=row.enabled,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _child_audit_payload(row: MCPServerChildResource) -> dict[str, Any]:
    plaintext = children_repo.decrypt_headers(row)
    return _child_to_audit_dict(row, _mask_headers(plaintext))


async def _require_parent(
    session: AsyncSession, server_id: UUID
) -> MCPServer:
    """Resolve the parent server or raise 404; shared across nested routes."""
    parent = await servers_repo.aget_by_id(session, server_id)
    if parent is None:
        raise HTTPException(
            status_code=404, detail="MCP server (parent) not found"
        )
    return parent


async def _require_child_under_parent(
    session: AsyncSession, server_id: UUID, child_id: UUID
) -> MCPServerChildResource:
    """Resolve a child resource and confirm it belongs to ``server_id``."""
    child = await children_repo.aget_by_id(session, child_id)
    if child is None or child.parent_mcp_server_id != server_id:
        raise HTTPException(
            status_code=404, detail="MCP server child resource not found"
        )
    return child


@router.get(
    "/mcp-servers/{server_id}/child-resources",
    response_model=MCPServerChildResourceListOut,
    tags=["admin"],
)
async def list_child_resources_route(
    server_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _claims: Annotated[TokenClaims, Depends(require_admin)],
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    search: str | None = Query(None, max_length=255),
    include_disabled: bool = Query(False),
) -> MCPServerChildResourceListOut:
    await _require_parent(session, server_id)
    rows, total = await children_repo.alist_children(
        session,
        parent_id=server_id,
        page=page,
        page_size=page_size,
        search=search,
        include_disabled=include_disabled,
    )
    return MCPServerChildResourceListOut(
        page=page,
        page_size=page_size,
        total=total,
        items=[_child_to_out(r) for r in rows],
    )


@router.get(
    "/mcp-servers/{server_id}/child-resources/{child_id}",
    response_model=MCPServerChildResourceOut,
    tags=["admin"],
)
async def get_child_resource_route(
    server_id: UUID,
    child_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _claims: Annotated[TokenClaims, Depends(require_admin)],
) -> MCPServerChildResourceOut:
    await _require_parent(session, server_id)
    child = await _require_child_under_parent(session, server_id, child_id)
    return _child_to_out(child)


@router.post(
    "/mcp-servers/{server_id}/child-resources",
    response_model=MCPServerChildResourceOut,
    status_code=status.HTTP_201_CREATED,
    tags=["admin"],
)
async def create_child_resource_route(
    server_id: UUID,
    body: MCPServerChildResourceCreateIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    claims: Annotated[TokenClaims, Depends(require_admin)],
) -> MCPServerChildResourceOut:
    await _require_parent(session, server_id)
    try:
        row = await children_repo.acreate(
            session,
            parent_id=server_id,
            child_type=body.type,
            name=body.name,
            url=body.url,
            headers=body.headers,
        )
    except children_repo.InvalidParentRef as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except children_repo.InvalidChildType as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except children_repo.DuplicateName as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await audit_repo.arecord(
        session,
        actor_id=_actor_id(claims),
        action="mcp_server_child_resource.create",
        target_type="mcp_server_child_resource",
        target_id=row.id,
        before=None,
        after=_child_audit_payload(row),
    )
    await session.commit()
    return _child_to_out(row)


@router.patch(
    "/mcp-servers/{server_id}/child-resources/{child_id}",
    response_model=MCPServerChildResourceOut,
    tags=["admin"],
)
async def update_child_resource_route(
    server_id: UUID,
    child_id: UUID,
    body: MCPServerChildResourceUpdateIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    claims: Annotated[TokenClaims, Depends(require_admin)],
) -> MCPServerChildResourceOut:
    await _require_parent(session, server_id)
    existing = await _require_child_under_parent(session, server_id, child_id)

    update_kwargs = body.model_dump(exclude_unset=True)
    if not any(v is not None for v in update_kwargs.values()):
        return _child_to_out(existing)

    try:
        before = _child_audit_payload(existing)
    except children_repo.KekMismatchOnRead as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        row = await children_repo.aupdate(
            session, child_id=child_id, **update_kwargs
        )
    except children_repo.DuplicateName as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except children_repo.NotFound as exc:
        raise HTTPException(status_code=404, detail="not found") from exc

    after = _child_audit_payload(row)
    if before == after:
        return _child_to_out(row)

    await audit_repo.arecord(
        session,
        actor_id=_actor_id(claims),
        action="mcp_server_child_resource.update",
        target_type="mcp_server_child_resource",
        target_id=row.id,
        before=before,
        after=after,
    )
    await session.commit()
    return _child_to_out(row)


@router.post(
    "/mcp-servers/{server_id}/child-resources/{child_id}/enable",
    response_model=MCPServerChildResourceOut,
    tags=["admin"],
)
async def enable_child_resource_route(
    server_id: UUID,
    child_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    claims: Annotated[TokenClaims, Depends(require_admin)],
) -> MCPServerChildResourceOut:
    return await _toggle_child_enabled(
        session, claims, server_id=server_id, child_id=child_id, enable=True
    )


@router.post(
    "/mcp-servers/{server_id}/child-resources/{child_id}/disable",
    response_model=MCPServerChildResourceOut,
    tags=["admin"],
)
async def disable_child_resource_route(
    server_id: UUID,
    child_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    claims: Annotated[TokenClaims, Depends(require_admin)],
) -> MCPServerChildResourceOut:
    return await _toggle_child_enabled(
        session, claims, server_id=server_id, child_id=child_id, enable=False
    )


async def _toggle_child_enabled(
    session: AsyncSession,
    claims: TokenClaims,
    *,
    server_id: UUID,
    child_id: UUID,
    enable: bool,
) -> MCPServerChildResourceOut:
    await _require_parent(session, server_id)
    existing = await _require_child_under_parent(session, server_id, child_id)

    already = existing.enabled == enable
    if already:
        return _child_to_out(existing)

    try:
        before = _child_audit_payload(existing)
    except children_repo.KekMismatchOnRead as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if enable:
        row = await children_repo.aenable(session, child_id=child_id)
    else:
        row = await children_repo.adisable(session, child_id=child_id)

    after = _child_audit_payload(row)
    await audit_repo.arecord(
        session,
        actor_id=_actor_id(claims),
        action="mcp_server_child_resource.enable" if enable else "mcp_server_child_resource.disable",
        target_type="mcp_server_child_resource",
        target_id=row.id,
        before=before,
        after=after,
    )
    await session.commit()
    return _child_to_out(row)


# ---------------------------------------------------------------------------
# /admin/agents — DB-defined Agno agents
# ---------------------------------------------------------------------------


def _agent_to_audit_dict(row: Agent) -> dict[str, Any]:
    """Project an ``Agent`` row into the audit payload shape.

    Agents don't carry secrets directly — MCP credentials live on the
    referenced server rows.  Still, we project explicitly so the audit
    shape doesn't drift if the model gains new columns later.
    """
    return {
        "id": str(row.id),
        "name": row.name,
        "description": row.description,
        "model": row.model,
        "instructions": row.instructions,
        "tools_config": row.tools_config,
        "mcp_server_ids": [str(x) for x in (row.mcp_server_ids or [])],
        "child_resource_ids": [
            str(x) for x in (row.child_resource_ids or [])
        ],
        "agent_config": row.agent_config,
        "archived_at": row.archived_at.isoformat() if row.archived_at else None,
        "version": row.version,
    }


def _raise_invalid_refs(exc: agents_repo.InvalidRefs) -> None:
    """Convert :class:`InvalidRefs` into a structured 422.

    The repo carries five independent problem buckets; we surface each
    one so the admin UI can highlight the offending IDs precisely.
    """
    raise HTTPException(
        status_code=422,
        detail={
            "message": str(exc),
            "missing_mcp_server_ids": [str(x) for x in exc.missing_mcp_server_ids],
            "archived_mcp_server_ids": [
                str(x) for x in exc.archived_mcp_server_ids
            ],
            "missing_child_resource_ids": [
                str(x) for x in exc.missing_child_resource_ids
            ],
            "disabled_child_resource_ids": [
                str(x) for x in exc.disabled_child_resource_ids
            ],
            "orphan_child_resource_ids": [
                str(x) for x in exc.orphan_child_resource_ids
            ],
        },
    )


@router.get("/agents", response_model=AgentListOut, tags=["admin"])
async def list_agents_route(
    session: Annotated[AsyncSession, Depends(get_session)],
    _claims: Annotated[TokenClaims, Depends(require_admin)],
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    model: str | None = Query(
        None,
        description="Filter by exact model string.",
        max_length=255,
        alias="model",
    ),
    search: str | None = Query(
        None,
        description="Case-insensitive substring match on name or description.",
        max_length=255,
    ),
    include_archived: bool = Query(
        False, description="If true, archived agents are included in the listing."
    ),
) -> AgentListOut:
    rows, total = await agents_repo.alist_agents(
        session,
        page=page,
        page_size=page_size,
        search=search,
        include_archived=include_archived,
        model_filter=model,
    )
    return AgentListOut(
        page=page,
        page_size=page_size,
        total=total,
        items=[AgentOut.model_validate(r) for r in rows],
    )


@router.get("/agents/{agent_id}", response_model=AgentOut, tags=["admin"])
async def get_agent_route(
    agent_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _claims: Annotated[TokenClaims, Depends(require_admin)],
) -> AgentOut:
    row = await agents_repo.aget_by_id(session, agent_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return AgentOut.model_validate(row)


@router.post(
    "/agents",
    response_model=AgentOut,
    status_code=status.HTTP_201_CREATED,
    tags=["admin"],
)
async def create_agent_route(
    body: AgentCreateIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    claims: Annotated[TokenClaims, Depends(require_admin)],
) -> AgentOut:
    try:
        row = await agents_repo.acreate(
            session,
            name=body.name,
            model=body.model,
            instructions=body.instructions,
            description=body.description,
            tools_config=body.tools_config,
            mcp_server_ids=body.mcp_server_ids,
            child_resource_ids=body.child_resource_ids,
            agent_config=body.agent_config,
            created_by=_actor_id(claims),
        )
    except agents_repo.DuplicateName as exc:
        raise HTTPException(
            status_code=409, detail=f"Agent name '{body.name}' already exists"
        ) from exc
    except agents_repo.InvalidRefs as exc:
        _raise_invalid_refs(exc)

    await audit_repo.arecord(
        session,
        actor_id=_actor_id(claims),
        action="agent.create",
        target_type="agent",
        target_id=row.id,
        before=None,
        after=_agent_to_audit_dict(row),
    )
    await session.commit()
    return AgentOut.model_validate(row)


@router.patch(
    "/agents/{agent_id}", response_model=AgentOut, tags=["admin"]
)
async def update_agent_route(
    agent_id: UUID,
    body: AgentUpdateIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    claims: Annotated[TokenClaims, Depends(require_admin)],
) -> AgentOut:
    existing = await agents_repo.aget_by_id(session, agent_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    update_kwargs = body.model_dump(exclude_unset=True)
    if not any(v is not None for v in update_kwargs.values()):
        return AgentOut.model_validate(existing)

    before = _agent_to_audit_dict(existing)

    try:
        row = await agents_repo.aupdate(
            session, agent_id=agent_id, **update_kwargs
        )
    except agents_repo.NotFound as exc:
        raise HTTPException(status_code=404, detail="Agent not found") from exc
    except agents_repo.DuplicateName as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except agents_repo.InvalidRefs as exc:
        _raise_invalid_refs(exc)

    after = _agent_to_audit_dict(row)
    if before == after:
        # Repo treated the change as a no-op (e.g. setting every field
        # to its current value via partial update).  Don't audit.
        return AgentOut.model_validate(row)

    await audit_repo.arecord(
        session,
        actor_id=_actor_id(claims),
        action="agent.update",
        target_type="agent",
        target_id=row.id,
        before=before,
        after=after,
    )
    await session.commit()
    return AgentOut.model_validate(row)


@router.post(
    "/agents/{agent_id}/archive",
    response_model=AgentOut,
    tags=["admin"],
)
async def archive_agent_route(
    agent_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    claims: Annotated[TokenClaims, Depends(require_admin)],
) -> AgentOut:
    return await _toggle_agent_archive(
        session, claims, agent_id=agent_id, archive=True
    )


@router.post(
    "/agents/{agent_id}/unarchive",
    response_model=AgentOut,
    tags=["admin"],
)
async def unarchive_agent_route(
    agent_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    claims: Annotated[TokenClaims, Depends(require_admin)],
) -> AgentOut:
    return await _toggle_agent_archive(
        session, claims, agent_id=agent_id, archive=False
    )


async def _toggle_agent_archive(
    session: AsyncSession,
    claims: TokenClaims,
    *,
    agent_id: UUID,
    archive: bool,
) -> AgentOut:
    existing = await agents_repo.aget_by_id(session, agent_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    already = (
        (archive and existing.archived_at is not None)
        or (not archive and existing.archived_at is None)
    )
    if already:
        return AgentOut.model_validate(existing)

    before = _agent_to_audit_dict(existing)
    try:
        if archive:
            row = await agents_repo.aarchive(session, agent_id=agent_id)
        else:
            row = await agents_repo.aunarchive(session, agent_id=agent_id)
    except agents_repo.NotFound as exc:
        raise HTTPException(status_code=404, detail="Agent not found") from exc

    await audit_repo.arecord(
        session,
        actor_id=_actor_id(claims),
        action="agent.archive" if archive else "agent.unarchive",
        target_type="agent",
        target_id=row.id,
        before=before,
        after=_agent_to_audit_dict(row),
    )
    await session.commit()
    return AgentOut.model_validate(row)


# ---------------------------------------------------------------------------
# /admin/teams — DB-defined Agno teams
# ---------------------------------------------------------------------------


def _team_to_audit_dict(row: Team) -> dict[str, Any]:
    """Project a ``Team`` row into the audit payload shape."""
    return {
        "id": str(row.id),
        "name": row.name,
        "description": row.description,
        "mode": row.mode,
        "member_agent_ids": [str(x) for x in (row.member_agent_ids or [])],
        "team_config": row.team_config,
        "archived_at": row.archived_at.isoformat() if row.archived_at else None,
        "version": row.version,
    }


def _raise_invalid_members(exc: teams_repo.InvalidMembers) -> None:
    """Convert :class:`InvalidMembers` into a structured 422."""
    raise HTTPException(
        status_code=422,
        detail={
            "message": str(exc),
            "missing_agent_ids": [str(x) for x in exc.missing_agent_ids],
            "archived_agent_ids": [str(x) for x in exc.archived_agent_ids],
        },
    )


@router.get("/teams", response_model=TeamListOut, tags=["admin"])
async def list_teams_route(
    session: Annotated[AsyncSession, Depends(get_session)],
    _claims: Annotated[TokenClaims, Depends(require_admin)],
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    mode: str | None = Query(
        None,
        description="Filter by team mode.",
        pattern=r"^(route|coordinate|collaborate)$",
    ),
    search: str | None = Query(
        None,
        description="Case-insensitive substring match on name or description.",
        max_length=255,
    ),
    include_archived: bool = Query(
        False, description="If true, archived teams are included in the listing."
    ),
) -> TeamListOut:
    try:
        rows, total = await teams_repo.alist_teams(
            session,
            page=page,
            page_size=page_size,
            search=search,
            include_archived=include_archived,
            mode_filter=mode,
        )
    except teams_repo.InvalidMode as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return TeamListOut(
        page=page,
        page_size=page_size,
        total=total,
        items=[TeamOut.model_validate(r) for r in rows],
    )


@router.get("/teams/{team_id}", response_model=TeamOut, tags=["admin"])
async def get_team_route(
    team_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _claims: Annotated[TokenClaims, Depends(require_admin)],
) -> TeamOut:
    row = await teams_repo.aget_by_id(session, team_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Team not found")
    return TeamOut.model_validate(row)


@router.post(
    "/teams",
    response_model=TeamOut,
    status_code=status.HTTP_201_CREATED,
    tags=["admin"],
)
async def create_team_route(
    body: TeamCreateIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    claims: Annotated[TokenClaims, Depends(require_admin)],
) -> TeamOut:
    try:
        row = await teams_repo.acreate(
            session,
            name=body.name,
            mode=body.mode,
            description=body.description,
            member_agent_ids=body.member_agent_ids,
            team_config=body.team_config,
            created_by=_actor_id(claims),
        )
    except teams_repo.DuplicateName as exc:
        raise HTTPException(
            status_code=409, detail=f"Team name '{body.name}' already exists"
        ) from exc
    except teams_repo.InvalidMode as exc:
        # Belt-and-braces; Pydantic regex would normally catch this.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except teams_repo.InvalidMembers as exc:
        _raise_invalid_members(exc)

    await audit_repo.arecord(
        session,
        actor_id=_actor_id(claims),
        action="team.create",
        target_type="team",
        target_id=row.id,
        before=None,
        after=_team_to_audit_dict(row),
    )
    await session.commit()
    return TeamOut.model_validate(row)


@router.patch(
    "/teams/{team_id}", response_model=TeamOut, tags=["admin"]
)
async def update_team_route(
    team_id: UUID,
    body: TeamUpdateIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    claims: Annotated[TokenClaims, Depends(require_admin)],
) -> TeamOut:
    existing = await teams_repo.aget_by_id(session, team_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Team not found")

    update_kwargs = body.model_dump(exclude_unset=True)
    if not any(v is not None for v in update_kwargs.values()):
        return TeamOut.model_validate(existing)

    before = _team_to_audit_dict(existing)

    try:
        row = await teams_repo.aupdate(
            session, team_id=team_id, **update_kwargs
        )
    except teams_repo.NotFound as exc:
        raise HTTPException(status_code=404, detail="Team not found") from exc
    except teams_repo.DuplicateName as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except teams_repo.InvalidMode as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except teams_repo.InvalidMembers as exc:
        _raise_invalid_members(exc)

    after = _team_to_audit_dict(row)
    if before == after:
        return TeamOut.model_validate(row)

    await audit_repo.arecord(
        session,
        actor_id=_actor_id(claims),
        action="team.update",
        target_type="team",
        target_id=row.id,
        before=before,
        after=after,
    )
    await session.commit()
    return TeamOut.model_validate(row)


@router.post(
    "/teams/{team_id}/archive",
    response_model=TeamOut,
    tags=["admin"],
)
async def archive_team_route(
    team_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    claims: Annotated[TokenClaims, Depends(require_admin)],
) -> TeamOut:
    return await _toggle_team_archive(
        session, claims, team_id=team_id, archive=True
    )


@router.post(
    "/teams/{team_id}/unarchive",
    response_model=TeamOut,
    tags=["admin"],
)
async def unarchive_team_route(
    team_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    claims: Annotated[TokenClaims, Depends(require_admin)],
) -> TeamOut:
    return await _toggle_team_archive(
        session, claims, team_id=team_id, archive=False
    )


async def _toggle_team_archive(
    session: AsyncSession,
    claims: TokenClaims,
    *,
    team_id: UUID,
    archive: bool,
) -> TeamOut:
    existing = await teams_repo.aget_by_id(session, team_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Team not found")

    already = (
        (archive and existing.archived_at is not None)
        or (not archive and existing.archived_at is None)
    )
    if already:
        return TeamOut.model_validate(existing)

    before = _team_to_audit_dict(existing)
    try:
        if archive:
            row = await teams_repo.aarchive(session, team_id=team_id)
        else:
            row = await teams_repo.aunarchive(session, team_id=team_id)
    except teams_repo.NotFound as exc:
        raise HTTPException(status_code=404, detail="Team not found") from exc

    await audit_repo.arecord(
        session,
        actor_id=_actor_id(claims),
        action="team.archive" if archive else "team.unarchive",
        target_type="team",
        target_id=row.id,
        before=before,
        after=_team_to_audit_dict(row),
    )
    await session.commit()
    return TeamOut.model_validate(row)


# ---------------------------------------------------------------------------
# /admin/mcp-cache — operational view onto the warm MCP tool handles
# ---------------------------------------------------------------------------


def _get_mcp_cache(request: Request) -> MCPCache:
    """Resolve the process-wide cache from ``app.state``.

    The cache is attached during lifespan startup (see
    :func:`gargantua.main.lifespan`).  If it's missing here we're in a
    misconfigured environment (e.g. the lifespan never ran) — return a
    clear 503 rather than a confusing AttributeError.
    """
    cache = getattr(request.app.state, "mcp_cache", None)
    if cache is None:
        raise HTTPException(
            status_code=503,
            detail="MCP cache is not initialized on this instance",
        )
    return cache


@router.get("/mcp-cache", response_model=MCPCacheListOut, tags=["admin"])
async def list_mcp_cache_route(
    request: Request,
    _claims: Annotated[TokenClaims, Depends(require_admin)],
) -> MCPCacheListOut:
    """Snapshot every warm MCP tool handle held by this process.

    Includes both current entries and **orphans** (post-version-bump
    handles still alive because at least one caller holds a lease) so
    operators can see when something is stuck.
    """
    cache = _get_mcp_cache(request)
    snaps = cache.inspect()
    items = [
        MCPCacheEntryOut(
            server_id=s.server_id,
            child_resource_ids=list(s.child_resource_ids),
            version=s.version,
            ref_count=s.ref_count,
            last_used=s.last_used,
            is_orphan=s.is_orphan,
        )
        for s in snaps
    ]
    return MCPCacheListOut(items=items, total=len(items))


@router.post(
    "/mcp-cache/{server_id}/evict",
    tags=["admin"],
)
async def evict_mcp_cache_route(
    server_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    claims: Annotated[TokenClaims, Depends(require_admin)],
) -> dict[str, bool]:
    """Force-close the cached handle for ``server_id``.

    Subsequent acquires rebuild from the latest DB state.  Returns 404
    if nothing was cached so the operator gets an explicit signal
    rather than a silent success.

    Audited as ``mcp_cache.evict`` against the ``mcp_server`` target
    type — the cache itself isn't a domain object, but the action is
    fundamentally "I touched the runtime state for this server".
    """
    cache = _get_mcp_cache(request)
    evicted = await cache.evict(server_id)
    if not evicted:
        raise HTTPException(
            status_code=404,
            detail="MCP server is not currently cached",
        )

    await audit_repo.arecord(
        session,
        actor_id=_actor_id(claims),
        action="mcp_cache.evict",
        target_type="mcp_server",
        target_id=server_id,
        before=None,
        after=None,
    )
    await session.commit()
    return {"evicted": True}


# ---------------------------------------------------------------------------
# /admin/agent-templates — read-only seeds for the "New from template" UI
# ---------------------------------------------------------------------------


def _template_to_out(t: AgentTemplate) -> AgentTemplateOut:
    """Map the dataclass to its API projection.

    Trivial today but kept as a function so future fields (e.g. icon,
    version, deprecation flag) have a single place to land.
    """
    return AgentTemplateOut(
        slug=t.slug,
        name=t.name,
        description=t.description,
        model=t.model,
        suggested_mcp_server_type_slugs=list(t.suggested_mcp_server_type_slugs),
        agent_config=dict(t.agent_config),
        instructions=t.instructions,
    )


@router.get(
    "/agent-templates",
    response_model=AgentTemplateListOut,
    tags=["admin"],
)
async def list_agent_templates_route(
    _claims: Annotated[TokenClaims, Depends(require_admin)],
) -> AgentTemplateListOut:
    """List every agent template shipped with the package.

    Not paginated, no filters — the catalog is small (handful of seeds)
    and the UI always wants the full list to populate its picker.
    Re-reads from disk on every call so editing a seed during local
    development takes effect on the next request.
    """
    templates = load_templates()
    return AgentTemplateListOut(
        items=[_template_to_out(t) for t in templates],
        total=len(templates),
    )


@router.get(
    "/agent-templates/{slug}",
    response_model=AgentTemplateOut,
    tags=["admin"],
)
async def get_agent_template_route(
    slug: str,
    _claims: Annotated[TokenClaims, Depends(require_admin)],
) -> AgentTemplateOut:
    """Return one template by slug.

    404 (not 422) on unknown slug: the URL itself is well-formed, the
    resource is just absent.  The UI's picker should never hit this
    path with an invalid slug since it's chosen from the list result,
    but the error case is here for direct API consumers.
    """
    try:
        tpl = load_template_by_slug(slug)
    except TemplateNotFound as exc:
        raise HTTPException(
            status_code=404, detail=f"Agent template '{slug}' not found"
        ) from exc
    return _template_to_out(tpl)

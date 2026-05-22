"""Pydantic request/response schemas shared by admin routes + CLI output.

Kept in a single module (rather than per-domain) until the surface grows
large enough to warrant splitting — saves churn while we're still
iterating on the API shape.

Naming convention:

* ``*In``   — request body shape.
* ``*Out``  — response shape (single row).
* ``*List`` — paginated response wrapping a list of ``*Out``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Common
# ---------------------------------------------------------------------------


class PaginationMeta(BaseModel):
    """Envelope fields attached to every paginated list response."""

    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total: int = Field(ge=0)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


class UserOut(BaseModel):
    """Public projection of an ``ai.users`` row.  Never contains the password hash."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class UserCreateIn(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=8, max_length=1024)
    role: str = Field(pattern=r"^(admin|user)$")


class UserRoleUpdateIn(BaseModel):
    role: str = Field(pattern=r"^(admin|user)$")


class UserListOut(PaginationMeta):
    items: list[UserOut]


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


class AuditLogOut(BaseModel):
    """Public projection of an ``ai.audit_log`` row."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    actor_id: UUID | None
    action: str
    target_type: str
    target_id: UUID | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    created_at: datetime


class AuditLogListOut(PaginationMeta):
    items: list[AuditLogOut]


# ---------------------------------------------------------------------------
# MCP server types (catalog)
# ---------------------------------------------------------------------------


#: Slugs are URL-safe identifiers: lowercase alphanum + hyphens.  Locked
#: down so they can be used directly in route paths without escaping.
_SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$"

#: Supported MCP transports.  Mirrors :data:`gargantua.repo.mcp_server_types.VALID_MODES`.
_MODE_PATTERN = r"^(stdio|sse|streamable_http)$"


class ConfigSchemaField(BaseModel):
    """One row in the type's ``config_schema`` array.

    The admin UI renders this into a form input.  ``is_secret=True``
    fields land in ``MCPServer.env_vars`` (AES-encrypted at rest);
    non-secret fields stay in plaintext.
    """

    name: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=255)
    #: One of ``text``, ``password``, ``number``, ``select``, ``textarea`` —
    #: the admin UI maps these onto its form controls.  We don't enum-restrict
    #: here so future UI types can land without a backend release.
    type: str = Field(min_length=1, max_length=32)
    is_secret: bool = False
    required: bool = False
    default: Any = None


class MCPServerTypeOut(BaseModel):
    """Public projection of an ``ai.mcp_server_type`` row."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    name: str
    description: str | None
    mode: str
    default_command: str | None
    default_args: list[Any]
    config_schema: list[dict[str, Any]]
    default_env_vars: dict[str, Any]
    optional_env_vars: dict[str, Any]
    default_swagger_url: str | None
    supports_swagger_child: bool
    version: int
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MCPServerTypeCreateIn(BaseModel):
    slug: str = Field(pattern=_SLUG_PATTERN, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    mode: str = Field(pattern=_MODE_PATTERN)
    default_command: str | None = None
    default_args: list[Any] = Field(default_factory=list)
    config_schema: list[ConfigSchemaField] = Field(default_factory=list)
    default_env_vars: dict[str, Any] = Field(default_factory=dict)
    optional_env_vars: dict[str, Any] = Field(default_factory=dict)
    default_swagger_url: str | None = None
    supports_swagger_child: bool = False


class MCPServerTypeUpdateIn(BaseModel):
    """Partial update — every field is optional; ``None`` means *don't touch*."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    mode: str | None = Field(default=None, pattern=_MODE_PATTERN)
    default_command: str | None = None
    default_args: list[Any] | None = None
    config_schema: list[ConfigSchemaField] | None = None
    default_env_vars: dict[str, Any] | None = None
    optional_env_vars: dict[str, Any] | None = None
    default_swagger_url: str | None = None
    supports_swagger_child: bool | None = None


class MCPServerTypeListOut(PaginationMeta):
    items: list[MCPServerTypeOut]


# ---------------------------------------------------------------------------
# MCP servers (instances)
# ---------------------------------------------------------------------------


#: Env tags are short, lowercase identifiers (``prod``, ``dev``, ``stg``).
_ENV_TAG_PATTERN = r"^[a-z0-9][a-z0-9-]{0,30}[a-z0-9]$|^[a-z0-9]$"

#: Placeholder returned in :class:`MCPServerOut.env_vars` for secret
#: fields.  The admin UI uses this to render an "unchanged" hint and to
#: decide which inputs need re-entry on the edit form.
SECRET_PLACEHOLDER = "<redacted>"


class MCPServerOut(BaseModel):
    """Public projection of an MCP server instance.

    ``env_vars`` carries the **plaintext** view, with secret fields
    (per the parent type's ``config_schema``) replaced by
    :data:`SECRET_PLACEHOLDER`.  Unknown keys (drift from the schema)
    default to masked-for-safety.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type_id: UUID
    name: str
    env_tag: str
    command: str | None
    args: list[Any]
    env_vars: dict[str, Any]
    archived_at: datetime | None
    version: int
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime


class MCPServerCreateIn(BaseModel):
    type_id: UUID
    name: str = Field(min_length=1, max_length=255)
    env_tag: str = Field(pattern=_ENV_TAG_PATTERN, max_length=32)
    env_vars: dict[str, Any] = Field(default_factory=dict)
    command: str | None = None
    args: list[Any] = Field(default_factory=list)


class MCPServerUpdateIn(BaseModel):
    """Partial update.  ``env_vars`` is **replace-all** when present:
    pass the full desired map.  Pass ``{}`` to clear; omit / pass
    ``None`` to leave unchanged."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    env_tag: str | None = Field(
        default=None, pattern=_ENV_TAG_PATTERN, max_length=32
    )
    env_vars: dict[str, Any] | None = None
    command: str | None = None
    args: list[Any] | None = None


class MCPServerListOut(PaginationMeta):
    items: list[MCPServerOut]


# ---------------------------------------------------------------------------
# MCP server child resources
# ---------------------------------------------------------------------------


#: Currently only ``swagger``; mirrors :data:`gargantua.repo.mcp_child_resources.VALID_CHILD_TYPES`.
_CHILD_TYPE_PATTERN = r"^(swagger)$"


class MCPServerChildResourceOut(BaseModel):
    """Public projection of a child resource.

    ``headers`` is masked the same way ``env_vars`` is on
    :class:`MCPServerOut`: every key present is shown, but secret-bearing
    values are replaced with :data:`SECRET_PLACEHOLDER`.  All header
    values are treated as secret since they typically carry tokens.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    parent_mcp_server_id: UUID
    type: str
    name: str
    url: str
    headers: dict[str, Any]
    enabled: bool
    version: int
    created_at: datetime
    updated_at: datetime


class MCPServerChildResourceCreateIn(BaseModel):
    type: str = Field(pattern=_CHILD_TYPE_PATTERN)
    name: str = Field(min_length=1, max_length=255)
    url: str = Field(min_length=1, max_length=2048)
    headers: dict[str, Any] = Field(default_factory=dict)


class MCPServerChildResourceUpdateIn(BaseModel):
    """Partial update; ``type`` is intentionally not changeable in place."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    headers: dict[str, Any] | None = None


class MCPServerChildResourceListOut(PaginationMeta):
    items: list[MCPServerChildResourceOut]


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


class AgentOut(BaseModel):
    """Public projection of an ``ai.agent`` row.

    Reference fields (``mcp_server_ids`` and ``child_resource_ids``)
    are returned as-is — they're already opaque UUIDs.  The route layer
    is free to add an ``_expanded`` envelope later if the UI needs row
    metadata in one trip.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    model: str
    instructions: str
    tools_config: dict[str, Any]
    mcp_server_ids: list[UUID]
    child_resource_ids: list[UUID]
    agent_config: dict[str, Any]
    archived_at: datetime | None
    version: int
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime


class AgentCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    model: str = Field(min_length=1, max_length=255)
    instructions: str = Field(min_length=1)
    description: str | None = None
    tools_config: dict[str, Any] = Field(default_factory=dict)
    mcp_server_ids: list[UUID] = Field(default_factory=list)
    child_resource_ids: list[UUID] = Field(default_factory=list)
    agent_config: dict[str, Any] = Field(default_factory=dict)


class AgentUpdateIn(BaseModel):
    """Partial update — every field is optional; ``None`` means *don't touch*."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    model: str | None = Field(default=None, min_length=1, max_length=255)
    instructions: str | None = Field(default=None, min_length=1)
    description: str | None = None
    tools_config: dict[str, Any] | None = None
    mcp_server_ids: list[UUID] | None = None
    child_resource_ids: list[UUID] | None = None
    agent_config: dict[str, Any] | None = None


class AgentListOut(PaginationMeta):
    items: list[AgentOut]


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------


#: Mirrors :data:`gargantua.repo.teams.VALID_MODES` and the DB CHECK on
#: ``ai.team``.
_TEAM_MODE_PATTERN = r"^(route|coordinate|collaborate)$"


class TeamOut(BaseModel):
    """Public projection of an ``ai.team`` row."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    mode: str
    member_agent_ids: list[UUID]
    team_config: dict[str, Any]
    archived_at: datetime | None
    version: int
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime


class TeamCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    mode: str = Field(pattern=_TEAM_MODE_PATTERN)
    description: str | None = None
    member_agent_ids: list[UUID] = Field(default_factory=list)
    team_config: dict[str, Any] = Field(default_factory=dict)


class TeamUpdateIn(BaseModel):
    """Partial update — every field is optional; ``None`` means *don't touch*."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    mode: str | None = Field(default=None, pattern=_TEAM_MODE_PATTERN)
    description: str | None = None
    member_agent_ids: list[UUID] | None = None
    team_config: dict[str, Any] | None = None


class TeamListOut(PaginationMeta):
    items: list[TeamOut]


# ---------------------------------------------------------------------------
# MCP cache (operational introspection)
# ---------------------------------------------------------------------------


class MCPCacheEntryOut(BaseModel):
    """Public projection of one in-memory MCP cache entry.

    The cache holds warm tool handles in process memory; this is the
    operator view: which servers are warm, what version each was built
    against, who's still holding a lease, and when the entry was last
    touched.

    ``is_orphan`` is true for entries that were detached by a version
    bump (the row was edited) but kept alive because at least one
    caller still holds the old handle.  An orphan can be force-closed
    via the ``/evict`` endpoint if it's stuck.

    ``child_resource_ids`` is the (sorted) set of child resources this
    entry's tool handle is bound to.  Empty list = the entry serves
    agents that don't reference any children of this server; a
    non-empty list means a per-agent filter set is in force.  Two
    snapshots with the same ``server_id`` but different
    ``child_resource_ids`` are distinct cache entries.
    """

    server_id: UUID
    child_resource_ids: list[UUID]
    version: int
    ref_count: int = Field(ge=0)
    last_used: datetime
    is_orphan: bool


class MCPCacheListOut(BaseModel):
    """Container for :class:`MCPCacheEntryOut`.

    Not paginated — the cache is in-memory and tiny (bounded by the
    server catalog).  ``total`` is included for shape parity with the
    other admin list endpoints and easy assertion in tests.
    """

    items: list[MCPCacheEntryOut]
    total: int = Field(ge=0)


# ---------------------------------------------------------------------------
# /me — what the caller can run
# ---------------------------------------------------------------------------


class MeAgentOut(BaseModel):
    """Trimmed projection of an agent for the user-facing picker.

    Excludes admin-only fields (``tools_config``, ``agent_config``,
    ``child_resource_ids``, ``created_by``, timestamps) — those aren't
    relevant when the caller is just deciding which agent to chat with.
    ``mcp_server_ids`` is included so a chat UI can show "what tools
    this agent uses".
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    model: str
    mcp_server_ids: list[UUID]


class MeAgentListOut(BaseModel):
    """Container for :class:`MeAgentOut`.

    Not paginated — the catalog of agents a user can run is small
    enough to return in one shot.  If that ever changes we can add the
    same pagination envelope used by admin routes.
    """

    items: list[MeAgentOut]
    total: int = Field(ge=0)


class MeTeamOut(BaseModel):
    """Trimmed projection of a team for the user-facing picker."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    mode: str
    member_agent_ids: list[UUID]


class MeTeamListOut(BaseModel):
    items: list[MeTeamOut]
    total: int = Field(ge=0)


# ---------------------------------------------------------------------------
# Agent run request (POST /v1/agents/{id}/runs body)
# ---------------------------------------------------------------------------


class AgentRunRequest(BaseModel):
    """Body for ``POST /v1/agents/{agent_id}/runs``.

    ``input`` is required and may be a plain string (chat-style prompt)
    or a richer structure (list of messages); we accept anything Agno's
    ``arun`` accepts and forward verbatim.  The runtime route does not
    interpret it.

    ``user_id`` is **not** in the request body — it's derived from the
    JWT subject so a caller can't impersonate another user.
    """

    model_config = ConfigDict(extra="forbid")

    # ``Any`` because Agno's input type is a union of (str, list, dict,
    # Message, BaseModel, list[Message]).  Validating that here would
    # mean duplicating Agno's signature; we just forward.
    input: Any

    stream: bool = Field(
        default=False,
        description=(
            "When true, the response is an SSE stream of "
            "``data: <json>`` events terminated by ``data: [DONE]``."
        ),
    )
    session_id: str | None = Field(
        default=None,
        max_length=255,
        description=(
            "Conversation session ID; runs with the same id share "
            "history.  Optional — Agno auto-generates one if omitted."
        ),
    )
    session_state: dict[str, Any] | None = Field(
        default=None,
        description="Free-form state bag forwarded into the run context.",
    )
    metadata: dict[str, Any] | None = Field(
        default=None,
        description="Free-form metadata persisted alongside the run output.",
    )


# ---------------------------------------------------------------------------
# Agent templates (read-only seeds for the "New from template" UI flow)
# ---------------------------------------------------------------------------


class AgentTemplateOut(BaseModel):
    """One agent starter template.

    Templates are read-only seeds shipped with the package — the admin
    UI uses them to pre-fill the create-agent form.  The platform
    itself doesn't store template references on the resulting agent;
    once instantiated, the agent is an ordinary row that can diverge
    freely from its source template.

    ``suggested_mcp_server_type_slugs`` references entries in the
    catalog (``mcp_server_type.slug``).  The UI uses these to highlight
    which server types the operator should consider attaching.
    """

    slug: str
    name: str
    description: str | None
    model: str
    suggested_mcp_server_type_slugs: list[str]
    agent_config: dict[str, Any]
    instructions: str


class AgentTemplateListOut(BaseModel):
    """Container for :class:`AgentTemplateOut`.

    Not paginated — the template catalog is small (handful of seeds)
    and serializes in a single response.
    """

    items: list[AgentTemplateOut]
    total: int = Field(ge=0)

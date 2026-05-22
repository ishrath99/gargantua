"""SQLAlchemy 2.0 mapped models for every gargantua table.

Tables:

    gargantua_app.users                       — auth principals.
    gargantua_app.mcp_server_type             — catalog of MCP server templates.
    gargantua_app.mcp_server                  — instantiated MCP servers (env-tagged).
    gargantua_app.mcp_server_child_resource   — sub-resources of an MCP server (e.g. Swagger).
    gargantua_app.agent                       — per-row agent definitions (DB-first).
    gargantua_app.team                        — per-row team definitions referencing agents.
    gargantua_app.audit_log                   — append-only diff log keyed on (target_type, target_id).

The columns map 1:1 onto the Alembic initial migration in
``gargantua/db/migrations/versions/0001_initial_schema.py``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from gargantua.db.base import Base

#: Postgres native uuid_generate_v4 alternative; gen_random_uuid() lives in the
#: built-in ``pgcrypto`` extension on PG 13+, which is what we target.
_UUID_DEFAULT: Final = text("gen_random_uuid()")


# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------


class User(Base):
    """An authentication principal — admin or regular user."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("username", name="uq_users_username"),
        CheckConstraint("role IN ('admin', 'user')", name="role_in_known_set"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=_UUID_DEFAULT)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    #: When ``False`` the account is locked: login refuses authentication
    #: and the user cannot be used as an actor on any admin route.  Toggled
    #: via ``/admin/users/{id}/deactivate|activate``.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# mcp_server_type — catalog
# ---------------------------------------------------------------------------


class MCPServerType(Base):
    """A reusable template for an MCP server: declares mode, defaults, and the
    per-field schema (``config_schema``) the admin UI renders into a form."""

    __tablename__ = "mcp_server_type"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_mcp_server_type_slug"),
        CheckConstraint(
            "mode IN ('stdio', 'sse', 'streamable_http')",
            name="mode_in_known_set",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=_UUID_DEFAULT)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    default_command: Mapped[str | None] = mapped_column(Text)
    default_args: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    #: List of {name, label, type, is_secret, required, default} field metadata.
    config_schema: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    default_env_vars: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    optional_env_vars: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    default_swagger_url: Mapped[str | None] = mapped_column(Text)
    supports_swagger_child: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# mcp_server — instances
# ---------------------------------------------------------------------------


class MCPServer(Base):
    """An instantiated MCP server: type + env_tag + (encrypted) env_vars."""

    __tablename__ = "mcp_server"
    __table_args__ = (
        UniqueConstraint(
            "type_id", "name", "env_tag", name="uq_mcp_server_type_name_env"
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=_UUID_DEFAULT)
    type_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("mcp_server_type.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    env_tag: Mapped[str] = mapped_column(String(64), nullable=False)

    #: Overrides ``mcp_server_type.default_command`` when non-null.
    command: Mapped[str | None] = mapped_column(Text)
    args: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    #: AES-256-GCM-encrypted JSON object of secret env values.
    env_vars: Mapped[bytes | None] = mapped_column(LargeBinary)
    env_var_iv: Mapped[bytes | None] = mapped_column(LargeBinary)
    env_var_kek_id: Mapped[str | None] = mapped_column(String(64))

    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# mcp_server_child_resource — currently only Swagger
# ---------------------------------------------------------------------------


class MCPServerChildResource(Base):
    """A sub-resource attached to an MCP server (e.g. a Swagger doc URL)."""

    __tablename__ = "mcp_server_child_resource"
    __table_args__ = (
        UniqueConstraint(
            "parent_mcp_server_id", "name", name="uq_mcp_server_child_parent_name"
        ),
        CheckConstraint("type IN ('swagger')", name="type_in_known_set"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=_UUID_DEFAULT)
    parent_mcp_server_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("mcp_server.id", ondelete="CASCADE"),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)

    headers: Mapped[bytes | None] = mapped_column(LargeBinary)
    headers_iv: Mapped[bytes | None] = mapped_column(LargeBinary)
    headers_kek_id: Mapped[str | None] = mapped_column(String(64))

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# agent
# ---------------------------------------------------------------------------


class Agent(Base):
    """A DB-defined agent: model + instructions + tool/MCP wiring + config."""

    __tablename__ = "agent"
    __table_args__ = (UniqueConstraint("name", name="uq_agent_name"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=_UUID_DEFAULT)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)

    tools_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    mcp_server_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(PG_UUID(as_uuid=True)), nullable=False, server_default=text("'{}'::uuid[]")
    )
    child_resource_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(PG_UUID(as_uuid=True)), nullable=False, server_default=text("'{}'::uuid[]")
    )
    #: Free-form config bag for learning/compression/guardrails/etc.
    agent_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# team
# ---------------------------------------------------------------------------


class Team(Base):
    """A DB-defined team referencing agents in ``member_agent_ids``."""

    __tablename__ = "team"
    __table_args__ = (
        UniqueConstraint("name", name="uq_team_name"),
        CheckConstraint(
            "mode IN ('route', 'coordinate', 'collaborate')",
            name="mode_in_known_set",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=_UUID_DEFAULT)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)

    member_agent_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(PG_UUID(as_uuid=True)), nullable=False, server_default=text("'{}'::uuid[]")
    )
    team_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# audit_log
# ---------------------------------------------------------------------------


class AuditLog(Base):
    """Append-only diff log emitted on every admin-side mutation."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))

    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


Index("ix_audit_log_target", AuditLog.target_type, AuditLog.target_id)
Index("ix_audit_log_actor_id", AuditLog.actor_id)
Index("ix_audit_log_created_at", AuditLog.created_at.desc())

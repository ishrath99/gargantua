"""initial gargantua schema

Creates the full ``ai.*`` schema.  Any pre-existing tables in the ``ai``
schema (legacy from earlier deployments) are moved to ``ai_legacy.*`` first
so this migration is safe to apply on a database that already has rows.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-05-18
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------


# Names of tables we manage in this revision.  Any *other* table found in
# the ``ai`` schema on upgrade is assumed to be legacy and moved to
# ``ai_legacy``.  We never silently drop data.
_MANAGED_TABLES = {
    "users",
    "mcp_server_type",
    "mcp_server",
    "mcp_server_child_resource",
    "agent",
    "team",
    "audit_log",
    "alembic_version",
}


def _move_legacy_tables() -> None:
    """Move every table currently in ``ai.*`` that we don't manage into ``ai_legacy.*``.

    Idempotent: if ``ai_legacy.<name>`` already exists, we leave the source in
    place rather than colliding — operators can resolve the conflict manually.
    """
    bind = op.get_bind()
    op.execute("CREATE SCHEMA IF NOT EXISTS ai_legacy")

    rows = bind.execute(
        sa.text("SELECT tablename FROM pg_tables WHERE schemaname = 'ai'")
    ).fetchall()

    for (name,) in rows:
        if name in _MANAGED_TABLES:
            continue
        # Skip if a same-named table already lives in ai_legacy.
        exists = bind.execute(
            sa.text(
                "SELECT 1 FROM pg_tables "
                "WHERE schemaname = 'ai_legacy' AND tablename = :n"
            ),
            {"n": name},
        ).scalar()
        if exists:
            continue
        # Quote the identifier to handle any odd legacy names.
        op.execute(f'ALTER TABLE ai."{name}" SET SCHEMA ai_legacy')


def upgrade() -> None:
    # Ensure prerequisites.
    op.execute("CREATE SCHEMA IF NOT EXISTS ai")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # Move any unmanaged legacy tables out of the way.
    _move_legacy_tables()

    # ---------- users ----------
    op.create_table(
        "users",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("username", name="uq_users_username"),
        sa.CheckConstraint("role IN ('admin', 'user')", name="ck_users_role_in_known_set"),
        schema="ai",
    )

    # ---------- mcp_server_type ----------
    op.create_table(
        "mcp_server_type",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("default_command", sa.Text),
        sa.Column("default_args", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("config_schema", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("default_env_vars", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("optional_env_vars", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("default_swagger_url", sa.Text),
        sa.Column(
            "supports_swagger_child",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("version", sa.BigInteger, nullable=False, server_default=sa.text("1")),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("slug", name="uq_mcp_server_type_slug"),
        sa.CheckConstraint(
            "mode IN ('stdio', 'sse', 'streamable_http')",
            name="ck_mcp_server_type_mode_in_known_set",
        ),
        schema="ai",
    )

    # ---------- mcp_server ----------
    op.create_table(
        "mcp_server",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("type_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("env_tag", sa.String(64), nullable=False),
        sa.Column("command", sa.Text),
        sa.Column("args", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("env_vars", sa.LargeBinary),
        sa.Column("env_var_iv", sa.LargeBinary),
        sa.Column("env_var_kek_id", sa.String(64)),
        sa.Column("created_by", UUID(as_uuid=True)),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.BigInteger, nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["type_id"],
            ["ai.mcp_server_type.id"],
            name="fk_mcp_server_type_id_mcp_server_type",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["ai.users.id"],
            name="fk_mcp_server_created_by_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "type_id", "name", "env_tag", name="uq_mcp_server_type_id_name_env_tag"
        ),
        schema="ai",
    )

    # ---------- mcp_server_child_resource ----------
    op.create_table(
        "mcp_server_child_resource",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("parent_mcp_server_id", UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("headers", sa.LargeBinary),
        sa.Column("headers_iv", sa.LargeBinary),
        sa.Column("headers_kek_id", sa.String(64)),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("version", sa.BigInteger, nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["parent_mcp_server_id"],
            ["ai.mcp_server.id"],
            name="fk_mcp_server_child_resource_parent_mcp_server_id_mcp_server",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "parent_mcp_server_id",
            "name",
            name="uq_mcp_server_child_resource_parent_mcp_server_id_name",
        ),
        sa.CheckConstraint(
            "type IN ('swagger')",
            name="ck_mcp_server_child_resource_type_in_known_set",
        ),
        schema="ai",
    )

    # ---------- agent ----------
    op.create_table(
        "agent",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("instructions", sa.Text, nullable=False),
        sa.Column("tools_config", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "mcp_server_ids",
            ARRAY(UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("'{}'::uuid[]"),
        ),
        sa.Column(
            "child_resource_ids",
            ARRAY(UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("'{}'::uuid[]"),
        ),
        sa.Column("agent_config", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.BigInteger, nullable=False, server_default=sa.text("1")),
        sa.Column("created_by", UUID(as_uuid=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["ai.users.id"],
            name="fk_agent_created_by_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("name", name="uq_agent_name"),
        schema="ai",
    )

    # ---------- team ----------
    op.create_table(
        "team",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column(
            "member_agent_ids",
            ARRAY(UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("'{}'::uuid[]"),
        ),
        sa.Column("team_config", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.BigInteger, nullable=False, server_default=sa.text("1")),
        sa.Column("created_by", UUID(as_uuid=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["ai.users.id"],
            name="fk_team_created_by_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("name", name="uq_team_name"),
        sa.CheckConstraint(
            "mode IN ('route', 'coordinate', 'collaborate')",
            name="ck_team_mode_in_known_set",
        ),
        schema="ai",
    )

    # ---------- audit_log ----------
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("actor_id", UUID(as_uuid=True)),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=False),
        sa.Column("target_id", UUID(as_uuid=True)),
        sa.Column("before", JSONB),
        sa.Column("after", JSONB),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["ai.users.id"],
            name="fk_audit_log_actor_id_users",
            ondelete="SET NULL",
        ),
        schema="ai",
    )
    op.create_index(
        "ix_audit_log_target",
        "audit_log",
        ["target_type", "target_id"],
        schema="ai",
    )
    op.create_index(
        "ix_audit_log_actor_id",
        "audit_log",
        ["actor_id"],
        schema="ai",
    )
    op.create_index(
        "ix_audit_log_created_at",
        "audit_log",
        [sa.text("created_at DESC")],
        schema="ai",
    )


# ---------------------------------------------------------------------------
# downgrade — best-effort drop, used only in test/dev workflows.
# Production migrations are forward-only; downgrade exists so devs can iterate.
# ---------------------------------------------------------------------------


def downgrade() -> None:
    op.drop_index("ix_audit_log_created_at", table_name="audit_log", schema="ai")
    op.drop_index("ix_audit_log_actor_id", table_name="audit_log", schema="ai")
    op.drop_index("ix_audit_log_target", table_name="audit_log", schema="ai")
    op.drop_table("audit_log", schema="ai")
    op.drop_table("team", schema="ai")
    op.drop_table("agent", schema="ai")
    op.drop_table("mcp_server_child_resource", schema="ai")
    op.drop_table("mcp_server", schema="ai")
    op.drop_table("mcp_server_type", schema="ai")
    op.drop_table("users", schema="ai")

"""initial gargantua schema

Creates the full ``gargantua_app.*`` schema and every table in one shot.
This is a greenfield migration — there's no legacy data to migrate.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-05-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    # Ensure prerequisites.
    op.execute("CREATE SCHEMA IF NOT EXISTS gargantua_app")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

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
        schema="gargantua_app",
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
        sa.Column(
            "optional_env_vars", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
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
        schema="gargantua_app",
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
            ["gargantua_app.mcp_server_type.id"],
            name="fk_mcp_server_type_id_mcp_server_type",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["gargantua_app.users.id"],
            name="fk_mcp_server_created_by_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "type_id", "name", "env_tag", name="uq_mcp_server_type_id_name_env_tag"
        ),
        schema="gargantua_app",
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
            ["gargantua_app.mcp_server.id"],
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
        schema="gargantua_app",
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
            ["gargantua_app.users.id"],
            name="fk_agent_created_by_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("name", name="uq_agent_name"),
        schema="gargantua_app",
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
            ["gargantua_app.users.id"],
            name="fk_team_created_by_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("name", name="uq_team_name"),
        sa.CheckConstraint(
            "mode IN ('route', 'coordinate', 'collaborate')",
            name="ck_team_mode_in_known_set",
        ),
        schema="gargantua_app",
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
            ["gargantua_app.users.id"],
            name="fk_audit_log_actor_id_users",
            ondelete="SET NULL",
        ),
        schema="gargantua_app",
    )
    op.create_index(
        "ix_audit_log_target",
        "audit_log",
        ["target_type", "target_id"],
        schema="gargantua_app",
    )
    op.create_index(
        "ix_audit_log_actor_id",
        "audit_log",
        ["actor_id"],
        schema="gargantua_app",
    )
    op.create_index(
        "ix_audit_log_created_at",
        "audit_log",
        [sa.text("created_at DESC")],
        schema="gargantua_app",
    )


# ---------------------------------------------------------------------------
# downgrade — best-effort drop, used only in test/dev workflows.
# Production migrations are forward-only; downgrade exists so devs can iterate.
# ---------------------------------------------------------------------------


def downgrade() -> None:
    op.drop_index("ix_audit_log_created_at", table_name="audit_log", schema="gargantua_app")
    op.drop_index("ix_audit_log_actor_id", table_name="audit_log", schema="gargantua_app")
    op.drop_index("ix_audit_log_target", table_name="audit_log", schema="gargantua_app")
    op.drop_table("audit_log", schema="gargantua_app")
    op.drop_table("team", schema="gargantua_app")
    op.drop_table("agent", schema="gargantua_app")
    op.drop_table("mcp_server_child_resource", schema="gargantua_app")
    op.drop_table("mcp_server", schema="gargantua_app")
    op.drop_table("mcp_server_type", schema="gargantua_app")
    op.drop_table("users", schema="gargantua_app")

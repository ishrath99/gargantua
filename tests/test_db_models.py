"""SQLAlchemy 2.0 mapped models — class-shape only (no DB).

We verify that:
  * every table-bound class lives under schema ``ai``
  * the public columns exist with the expected nullability + types in spirit
  * unique constraints and foreign-key targets match the design

Migration application is in ``tests/integration/test_migration.py``.
"""

from __future__ import annotations

import pytest


def _column_names(model: type) -> set[str]:
    return {c.key for c in model.__table__.columns}


def test_base_uses_naming_convention_for_indexes_and_constraints() -> None:
    from gargantua.db.base import Base

    # PRs that rename indexes/constraints rely on a stable naming convention
    # so Alembic's autogenerate output is reproducible.  Confirm the
    # convention has the 5 required prefixes.
    nc = Base.metadata.naming_convention
    for key in ("ix", "uq", "ck", "fk", "pk"):
        assert key in nc, f"naming_convention is missing '{key}'"


def test_all_tables_live_under_ai_schema() -> None:
    from gargantua.db.base import Base

    for table in Base.metadata.tables.values():
        assert table.schema == "ai", f"{table.name} is not in schema 'ai'"


# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------


def test_user_model_has_required_columns() -> None:
    from gargantua.db.models import User

    cols = _column_names(User)
    assert {
        "id",
        "username",
        "password_hash",
        "role",
        "created_at",
        "updated_at",
    } <= cols
    assert User.__table__.name == "users"
    assert User.__table__.schema == "ai"


def test_user_username_is_unique() -> None:
    from gargantua.db.models import User

    username = User.__table__.c.username
    assert username.unique is True or any(
        "username" in {c.name for c in u.columns}
        for u in User.__table__.constraints
        if u.__class__.__name__ == "UniqueConstraint"
    )


@pytest.mark.parametrize("role", ["admin", "user"])
def test_user_role_check_constraint_allows_known_roles(role: str) -> None:
    from gargantua.db.models import User

    # Spot-check the CHECK constraint clause references "admin" and "user".
    check_text = " ".join(
        str(c.sqltext)
        for c in User.__table__.constraints
        if c.__class__.__name__ == "CheckConstraint"
    )
    assert role in check_text


def test_user_is_active_column_present_and_defaults_true() -> None:
    """``is_active`` is a non-null boolean with a server default of ``true``."""
    from gargantua.db.models import User

    assert "is_active" in _column_names(User)
    col = User.__table__.c.is_active
    assert col.nullable is False
    # server_default is a DefaultClause wrapping a TextClause.
    default_sql = str(col.server_default.arg) if col.server_default else ""
    assert "true" in default_sql.lower()


# ---------------------------------------------------------------------------
# mcp_server_type / mcp_server / mcp_server_child_resource
# ---------------------------------------------------------------------------


def test_mcp_server_type_columns() -> None:
    from gargantua.db.models import MCPServerType

    cols = _column_names(MCPServerType)
    assert {
        "id",
        "slug",
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
        "version",
        "archived_at",
        "created_at",
        "updated_at",
    } <= cols


def test_mcp_server_columns_and_fk() -> None:
    from gargantua.db.models import MCPServer

    cols = _column_names(MCPServer)
    assert {
        "id",
        "type_id",
        "name",
        "env_tag",
        "command",
        "args",
        "env_vars",
        "env_var_iv",
        "env_var_kek_id",
        "created_by",
        "archived_at",
        "version",
        "created_at",
        "updated_at",
    } <= cols

    # type_id references ai.mcp_server_type.id
    fks = [(fk.parent.key, fk.column.table.name) for fk in MCPServer.__table__.foreign_keys]
    assert ("type_id", "mcp_server_type") in fks


def test_mcp_server_child_resource_columns_and_fk() -> None:
    from gargantua.db.models import MCPServerChildResource

    cols = _column_names(MCPServerChildResource)
    assert {
        "id",
        "parent_mcp_server_id",
        "type",
        "name",
        "url",
        "headers",
        "headers_iv",
        "headers_kek_id",
        "enabled",
        "version",
        "created_at",
        "updated_at",
    } <= cols

    fks = [
        (fk.parent.key, fk.column.table.name)
        for fk in MCPServerChildResource.__table__.foreign_keys
    ]
    assert ("parent_mcp_server_id", "mcp_server") in fks


# ---------------------------------------------------------------------------
# agent / team
# ---------------------------------------------------------------------------


def test_agent_columns() -> None:
    from gargantua.db.models import Agent

    cols = _column_names(Agent)
    assert {
        "id",
        "name",
        "description",
        "model",
        "instructions",
        "tools_config",
        "mcp_server_ids",
        "child_resource_ids",
        "agent_config",
        "archived_at",
        "version",
        "created_by",
        "created_at",
        "updated_at",
    } <= cols


def test_team_columns_and_mode_check() -> None:
    from gargantua.db.models import Team

    cols = _column_names(Team)
    assert {
        "id",
        "name",
        "description",
        "mode",
        "member_agent_ids",
        "team_config",
        "archived_at",
        "version",
        "created_by",
        "created_at",
        "updated_at",
    } <= cols

    check_text = " ".join(
        str(c.sqltext)
        for c in Team.__table__.constraints
        if c.__class__.__name__ == "CheckConstraint"
    )
    for mode in ("route", "coordinate", "collaborate"):
        assert mode in check_text


# ---------------------------------------------------------------------------
# audit_log
# ---------------------------------------------------------------------------


def test_audit_log_columns() -> None:
    from gargantua.db.models import AuditLog

    cols = _column_names(AuditLog)
    assert {
        "id",
        "actor_id",
        "action",
        "target_type",
        "target_id",
        "before",
        "after",
        "created_at",
    } <= cols

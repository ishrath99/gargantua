"""users.is_active flag

Adds a non-null ``is_active`` boolean column to ``ai.users`` so an admin
can deactivate (lock out) an account without deleting the row.  Existing
rows are backfilled to ``true`` so the upgrade is a no-op for in-use
deployments.

The login route refuses authentication for any row with
``is_active = false`` (same generic "Invalid credentials" response so we
don't leak account existence).

Revision ID: 0002_user_is_active
Revises: 0001_initial_schema
Create Date: 2026-05-19
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0002_user_is_active"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Step 1: add the column with a server default so existing rows get
    # backfilled in the same DDL statement (no second UPDATE pass needed).
    op.add_column(
        "users",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        schema="ai",
    )

    # Step 2: helpful partial index — most admin queries filter to active
    # users, so an index on the active subset keeps the list endpoint cheap
    # even on large user tables.
    op.create_index(
        "ix_users_active_role",
        "users",
        ["role"],
        schema="ai",
        postgresql_where=sa.text("is_active = true"),
    )


def downgrade() -> None:
    op.drop_index("ix_users_active_role", table_name="users", schema="ai")
    op.drop_column("users", "is_active", schema="ai")

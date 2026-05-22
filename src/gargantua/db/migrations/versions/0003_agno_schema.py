"""Create the ``gargantua_agno`` schema for Agno-owned tables.

Agno's :class:`agno.db.postgres.PostgresDb` stores its own bookkeeping
(``agno_sessions``, ``agno_memories``, ``agno_runs``, …) in a
configurable schema.  We pin it to ``gargantua_agno`` so those tables stay
isolated from our application tables in ``gargantua_app`` — but we set
``create_schema=False`` on the PostgresDb so Agno doesn't race with
Alembic over schema management.  That contract requires us to create
the schema here.

The tables themselves are created lazily by Agno the first time they're
written to (e.g. on the first agent run that persists a session).  We
just need the namespace to exist.

Revision ID: 0003_agno_schema
Revises: 0002_user_is_active
Create Date: 2026-05-20
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0003_agno_schema"
down_revision: Union[str, None] = "0002_user_is_active"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent — safe to re-run on environments that pre-created the
    # schema manually (e.g. via psql) before this migration shipped.
    op.execute("CREATE SCHEMA IF NOT EXISTS gargantua_agno")


def downgrade() -> None:
    # ``CASCADE`` so dropping the schema also drops Agno's lazily-created
    # tables.  Down-migrating is a destructive operation for any
    # sessions / memory / run logs Agno has accumulated; operators are
    # expected to back those up first.
    op.execute("DROP SCHEMA IF EXISTS gargantua_agno CASCADE")

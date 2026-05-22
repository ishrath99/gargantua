"""Declarative base + metadata for every gargantua table.

Every model in :mod:`gargantua.db.models` lives under the ``ai`` Postgres
schema.  Setting that as the metadata default keeps the per-model
``__table_args__`` blocks small and means we can move ``ai`` to a different
name later by changing exactly one constant.

The naming convention is non-negotiable: Alembic's autogenerate uses it to
produce stable, reproducible migration files.  See
https://alembic.sqlalchemy.org/en/latest/naming.html
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

DB_SCHEMA = "gargantua_app"

_NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base shared by every gargantua model."""

    metadata = MetaData(naming_convention=_NAMING_CONVENTION, schema=DB_SCHEMA)

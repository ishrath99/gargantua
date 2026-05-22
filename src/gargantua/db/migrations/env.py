"""Alembic env.  Uses our :class:`Settings` for the DSN and our
:class:`Base.metadata` for autogenerate.

Online + offline modes are supported.  The ``alembic.ini`` shipped in the repo
keeps a placeholder ``sqlalchemy.url`` so a bare ``alembic`` invocation
doesn't crash, but gargantua resolves the real URL at runtime from
``gargantua.settings.Settings``.  Tests override the URL via
``cfg.set_main_option("sqlalchemy.url", ...)``.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make sure the application package is importable.
from gargantua.db.base import Base  # noqa: E402
import gargantua.db.models  # noqa: F401, E402  — register models on Base.metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _resolve_url() -> str:
    """Pick a DSN: explicit ``sqlalchemy.url`` option wins; otherwise Settings."""
    configured = config.get_main_option("sqlalchemy.url")
    placeholder = "postgresql+psycopg://gargantua:gargantua@localhost:5432/gargantua"
    if configured and configured != placeholder:
        return configured
    from gargantua.settings import get_settings

    return get_settings().database_url


target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting to a DB."""
    context.configure(
        url=_resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        version_table_schema="ai",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB."""
    cfg_section = config.get_section(config.config_ini_section) or {}
    cfg_section["sqlalchemy.url"] = _resolve_url()

    connectable = engine_from_config(
        cfg_section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            version_table_schema="ai",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

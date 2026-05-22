"""Build the AgentOS sub-application gated by our self-issued JWTs.

The parent app mounts the returned :class:`~fastapi.FastAPI` instance at
``/v1`` (see :func:`gargantua.main.create_app`).  Inside the mount:

* Agno's :class:`~agno.os.middleware.jwt.JWTMiddleware` enforces a Bearer
  token signed by our RS256 key (loaded from :class:`Settings`).
* The default Agno scope map drives RBAC — admin tokens (``agent_os:admin``)
  pass every route; user-only tokens (``agent_os:user``) are rejected on
  Agno routes since they hold no ``agents:*`` / ``teams:*`` scopes.
* Audience verification is on: ``aud`` must equal ``settings.jwt_audience``,
  so tokens minted for other services can't reach this surface.

Notes on lifecycle:

* ``auto_provision_dbs=False`` — Agno's tables (sessions, memory, traces, …)
  are created by Alembic, not auto-provisioned at startup.  This keeps unit
  tests that never reach a real Postgres working unchanged.
* Mounted sub-app lifespans are *not* propagated by Starlette today; the
  consequence is benign here (Agno's lifespan only does DB init + httpx
  client cleanup, both of which we either disabled or accept the minor
  leak on).
"""

from __future__ import annotations

from agno.db.postgres import PostgresDb
from agno.os import AgentOS
from agno.os.config import AuthorizationConfig
from fastapi import FastAPI

from gargantua import __version__
from gargantua.auth import SCOPE_ADMIN
from gargantua.settings import Settings


def build_agent_os_app(
    settings: Settings, *, agno_db: PostgresDb | None = None
) -> FastAPI:
    """Return a fully-wired AgentOS sub-application.

    The caller is expected to mount this at ``/v1`` on the parent app.

    ``agno_db`` is the shared :class:`PostgresDb` instance that should
    also be passed to transient :class:`agno.Agent` objects built by
    :func:`~gargantua.registry.build_agno_agent` (the run-route's path).
    Sharing one instance keeps both AgentOS-native routes and our run
    overrides writing to the same session / memory tables.  Callers
    that don't need that (legacy callers and unit tests) can omit it
    and we'll build a private one — but production should always pass
    one in.

    No agents, teams, or workflows are registered statically — the
    :class:`AgentOS` is given empty lists and the runtime
    ``/v1/agents/{id}/runs`` route (registered on the parent app before
    this mount) handles all real traffic.
    """
    sub_app = FastAPI(
        title="gargantua AgentOS",
        version=__version__,
        description="AgentOS routes (gated by RS256 JWTs).",
    )

    public_key = settings.jwt_public_key_path.read_bytes().decode()

    if agno_db is None:
        agno_db = PostgresDb(
            db_url=str(settings.database_url),
            db_schema="gargantua_agno",
            # Schema management is owned by our Alembic migration, not Agno.
            # Avoids racing two schema-creators on a single Postgres instance.
            create_schema=False,
        )

    agent_os = AgentOS(
        id="gargantua",
        name="gargantua",
        description="DB-first multi-agent SRE platform.",
        base_app=sub_app,
        db=agno_db,
        authorization=True,
        authorization_config=AuthorizationConfig(
            verification_keys=[public_key],
            algorithm="RS256",
            verify_audience=True,
            audience=settings.jwt_audience,
            admin_scope=SCOPE_ADMIN,
        ),
        cors_allowed_origins=settings.cors_origin_list or None,
        # Tables get created by Alembic; skip Agno's auto-provisioning
        # so we never have two systems trying to manage the same schema.
        auto_provision_dbs=False,
        telemetry=False,
    )

    return agent_os.get_app()

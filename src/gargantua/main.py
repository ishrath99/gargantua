"""FastAPI entry point.

Owns three things:

* The :class:`~fastapi.FastAPI` factory (:func:`create_app`).
* The lifespan: bootstrap-admin + MCP cache start on startup, cache
  stop + engine disposal on shutdown.
* Router + sub-app wire-up: ``/auth/*`` (our own JWT login/refresh/me),
  ``/admin/*`` (admin-only CRUD + audit, gated by ``require_admin``),
  ``/me/*`` (user-facing listings of runnable agents/teams), our run
  route at ``/v1/agents/{id}/runs`` (registered *before* the AgentOS
  mount so it wins), and ``/v1/*`` (Agno's ``AgentOS``).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from agno.db.postgres import PostgresDb
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from gargantua import __version__
from gargantua.api.admin import router as admin_router
from gargantua.api.agent_os import build_agent_os_app
from gargantua.api.auth import router as auth_router
from gargantua.api.me import router as me_router
from gargantua.api.runs import router as runs_router
from gargantua.bootstrap import bootstrap_admin_if_needed
from gargantua.db.session import dispose_engine, get_session_factory
from gargantua.mcp_cache import MCPCache, make_row_fetcher
from gargantua.mcp_tools import build_mcp_tools
from gargantua.settings import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup: bootstrap-admin + MCP cache.  Shutdown: cache + engine."""
    settings = get_settings()
    logger.info(
        "gargantua v%s starting (runtime_env=%s, port=%d, agno_debug=%s)",
        __version__,
        settings.runtime_env,
        settings.app_port,
        settings.agno_debug,
    )

    # Flip Agno's own loggers to DEBUG up-front when the operator
    # asked for verbose traces.  The per-Agent ``debug_mode=True`` we
    # forward through :mod:`gargantua.registry` already triggers
    # :func:`agno.agent._init.set_debug` during ``arun_dispatch`` —
    # but that only covers our :mod:`runs` route.  Calling
    # :func:`set_log_level_to_debug` once at startup additionally
    # covers any path that uses Agno without going through our
    # registry (e.g. Agno's bundled AgentOS sub-app under ``/agno``)
    # and surfaces logs from module-level imports too.
    if settings.agno_debug:
        from agno.utils.log import (
            set_log_level_to_debug,
        )

        set_log_level_to_debug()  # "agno" logger
        set_log_level_to_debug(source_type="team")  # "agno-team"
        logger.info(
            "agno: debug logging enabled (AGNO_DEBUG=true); expect verbose run traces on stderr"
        )

    # Bootstrap admin runs unconditionally; the helper itself is a no-op when
    # the BOOTSTRAP_ADMIN_* env vars are unset, so no DB connection is opened
    # in that case.  We swallow errors here so a transient DB blip during
    # startup doesn't permanently brick the container.
    try:
        factory = get_session_factory()
        async with factory() as session:
            await bootstrap_admin_if_needed(session)
    except Exception:
        logger.exception("bootstrap-admin failed; continuing startup")

    # MCP cache lives on ``app.state`` so admin routes and the runtime
    # routes can resolve it via ``request.app.state``.  First ``acquire``
    # of any server lazily spawns the actual MCP subprocess / HTTP
    # connection via :func:`build_mcp_tools`; ``/admin/mcp-cache`` exposes
    # the warm-handle inventory for inspection / forced eviction.
    cache = MCPCache(
        row_fetcher=make_row_fetcher(get_session_factory(), build_mcp_tools),
        idle_ttl=timedelta(seconds=settings.mcp_cache_idle_ttl_seconds),
        reap_interval=timedelta(seconds=settings.mcp_cache_reaper_interval_seconds),
    )
    await cache.start()
    app.state.mcp_cache = cache

    yield

    await cache.stop()
    await dispose_engine()
    logger.info("gargantua v%s shutting down", __version__)


def create_app() -> FastAPI:
    """Application factory."""
    settings = get_settings()
    app = FastAPI(
        title="gargantua",
        version=__version__,
        description="DB-first control plane for multi-agent systems and MCP servers.",
        lifespan=lifespan,
    )

    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Every JSON API lives under /api/* so it can't collide with UI page
    # routes served by the static mount below.  /health stays at the root
    # because load balancers and k8s probes look for it there by convention.
    app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
    app.include_router(admin_router, prefix="/api/admin", tags=["admin"])
    app.include_router(me_router, prefix="/api/me", tags=["me"])

    # AgentOS lives under /api/v1 — its own JWT middleware gates every
    # route, leaving /api/auth/* and /health unprotected on the parent app.
    #
    # If the JWT public key isn't on disk (typical for unit tests, also for
    # a container booted before the secret volume is mounted), skip the
    # mount.  /health stays useful for liveness checks; /api/auth/* returns
    # clearer errors at first use.
    if settings.jwt_public_key_path.exists():
        # Shared :class:`PostgresDb` so sessions / runs persisted by our
        # transient ``agno.Agent`` instances land in the same store as
        # AgentOS's own routes use.  Built once, attached to
        # ``app.state.agno_db`` so the run route can pick it up via
        # ``request.app.state``.
        agno_db = PostgresDb(
            db_url=str(settings.database_url),
            db_schema="gargantua_agno",
            create_schema=False,
        )
        app.state.agno_db = agno_db

        # Register our agent-run route override BEFORE mounting AgentOS at
        # ``/api/v1``.  Starlette checks routes in registration order, so
        # our specific ``/api/v1/agents/{agent_id}/runs`` matches before
        # the mount falls through to Agno's same-path route.
        app.include_router(runs_router, prefix="/api/v1", tags=["runs"])
        app.mount("/api/v1", build_agent_os_app(settings, agno_db=agno_db))
    else:
        logger.warning(
            "JWT public key not found at %s; /api/v1/* (AgentOS) routes not mounted. "
            "Set JWT_PUBLIC_KEY_PATH and restart to enable AgentOS.",
            settings.jwt_public_key_path,
        )

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
            "version": __version__,
            "runtime_env": settings.runtime_env,
        }

    # Static UI mount.  Must come AFTER every router include + sub-app
    # mount so the named API paths win route resolution; Starlette
    # checks routes in registration order and ``StaticFiles`` matches
    # everything under ``/``.  Conditional on the build artefact being
    # present so unit tests + dev (without ``pnpm build``) still boot
    # cleanly with no UI surface.
    if settings.ui_static_root.is_dir():
        # ``html=True`` makes a directory request resolve to its
        # ``index.html`` — necessary because the Next.js static export
        # uses ``trailingSlash: true`` and ships per-route ``index.html``
        # files (``out/admin/index.html``, ``out/chat/index.html``, …).
        app.mount(
            "/",
            StaticFiles(directory=settings.ui_static_root, html=True),
            name="ui",
        )
        logger.info(
            "ui: serving static export from %s at /",
            settings.ui_static_root,
        )
    else:
        logger.info(
            "ui: %s does not exist; skipping static mount "
            "(run ``pnpm --dir ui build`` to produce it)",
            settings.ui_static_root,
        )

    return app


# Uvicorn target: `uvicorn gargantua.main:app`.
app = create_app()

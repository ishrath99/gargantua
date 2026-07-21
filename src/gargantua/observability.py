"""Optional Arize Phoenix (OpenInference) tracing for Agno agents & teams.

Why this exists
---------------
Agents and teams are built *transiently* per request from DB rows
(:mod:`gargantua.registry`), so there's no long-lived object to attach a
tracer to.  Instead we lean on OpenInference's **auto-instrumentation**: a
single :func:`phoenix.otel.register` call at startup patches Agno's
``Agent`` / ``Team`` globally, so every transient object built afterwards
emits OpenTelemetry spans to the configured Phoenix collector — no
per-agent, per-route, or per-request wiring required.

Opt-in
------
Everything here is a no-op unless ``PHOENIX_COLLECTOR_ENDPOINT`` is set
(see :class:`gargantua.settings.Settings`).  That keeps the extra
dependencies dormant for deployments that don't want tracing, and means an
operator enables tracing purely by dropping the endpoint into ``.env`` and
restarting — no code change.

Import failures (packages not installed) and registration failures (bad
endpoint, network) are logged and swallowed: tracing must never take the
API down.
"""

from __future__ import annotations

import logging
import os

from gargantua.settings import Settings

logger = logging.getLogger(__name__)

# Guard against double-registration.  ``phoenix.otel.register`` installs a
# global tracer provider; calling it twice (e.g. reload, repeated lifespan
# in tests) stacks exporters and duplicates spans.
_TRACING_INITIALIZED = False


def setup_phoenix_tracing(settings: Settings) -> bool:
    """Register the Phoenix tracer + auto-instrument Agno, if configured.

    Returns ``True`` when tracing is active after this call, ``False`` when
    it's disabled or could not be enabled.  Safe to call more than once —
    subsequent calls are no-ops once tracing is live.
    """
    global _TRACING_INITIALIZED
    if _TRACING_INITIALIZED:
        return True

    endpoint = settings.phoenix_collector_endpoint.strip()
    if not endpoint:
        logger.info("phoenix: PHOENIX_COLLECTOR_ENDPOINT unset; agent tracing disabled")
        return False

    # phoenix.otel.register reads these from the environment.  We set them
    # explicitly from Settings so a value in ``.env`` (loaded by pydantic-
    # settings) reaches the tracer even if it wasn't exported to the shell.
    os.environ["PHOENIX_COLLECTOR_ENDPOINT"] = endpoint
    if settings.phoenix_api_key:
        os.environ["PHOENIX_API_KEY"] = settings.phoenix_api_key

    try:
        from phoenix.otel import register
    except ImportError:
        logger.warning(
            "phoenix: PHOENIX_COLLECTOR_ENDPOINT is set but the tracing packages "
            "are not installed; agent tracing disabled. Install with: "
            "pip install 'arize-phoenix-otel' 'openinference-instrumentation-agno'"
        )
        return False

    try:
        register(
            project_name=settings.phoenix_project_name,
            # Uses the installed openinference-instrumentation-agno to patch
            # Agno globally, so every transient Agent/Team we build is traced.
            auto_instrument=True,
        )
    except Exception:
        logger.exception(
            "phoenix: tracer registration failed (endpoint=%s); continuing without tracing",
            endpoint,
        )
        return False

    _TRACING_INITIALIZED = True
    logger.info(
        "phoenix: agent tracing enabled (endpoint=%s, project=%s)",
        endpoint,
        settings.phoenix_project_name,
    )
    return True

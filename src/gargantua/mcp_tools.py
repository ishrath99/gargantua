"""Production :type:`~gargantua.mcp_cache.ToolsBuilder` for the MCP cache.

The cache (:mod:`gargantua.mcp_cache`) is built around a callable that
turns ``(MCPServer row, plaintext env_vars, MCPServerType row,
child_resources)`` into a connected :class:`agno.tools.mcp.MCPTools`.
This module is that callable.

Why it lives outside the cache module
-------------------------------------

The cache itself is a pure concurrency primitive — it shouldn't know
anything about Agno or MCP semantics.  Keeping the builder in a
separate module:

* Makes the cache trivially testable with stub closeables.
* Makes the builder trivially testable with a recorded
  :class:`MCPTools` stub (see ``tests/test_mcp_tools.py``).
* Keeps the import graph one-directional: cache <- mcp_tools, never
  the other way around.

Transport mapping
-----------------

DB column ``mcp_server_type.mode`` controls how we build the tools:

* ``stdio`` — fork a subprocess.  Uses ``command``/``args`` from the
  server row (falling back to the type's defaults).  ``env_vars`` are
  passed as the subprocess environment, with values coerced to strings
  and ``None`` values filtered out.
* ``sse`` — open an SSE connection to a URL.  We translate to Agno's
  ``transport="sse"``.
* ``streamable_http`` — open a streamable-HTTP connection.  Our DB
  stores ``streamable_http`` (underscore, friendlier for Postgres
  enums); Agno's ``Literal`` expects ``streamable-http`` (hyphen).
  This module is the translation point.

URL discovery convention
------------------------

For SSE / streamable-HTTP transports the URL is admin-supplied as part
of ``env_vars``.  Today there's no explicit ``url_field`` column on
``mcp_server_type``, so we pick the value by name:

1. Exact key ``URL`` (any case).
2. Otherwise, any key ending in ``_URL`` (e.g. ``OPENSEARCH_URL``,
   ``BASE_URL``).  This matches the seed catalog in
   :mod:`gargantua.catalog_seed`.

If neither match exists the builder raises with a clear error so the
operator knows to fix the env_vars rather than getting a confusing
upstream connection error.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable

from agno.tools.mcp import MCPTools
from mcp.client.stdio import StdioServerParameters

from gargantua.db.models import MCPServer, MCPServerType
from gargantua.mcp_cache import ChildResourceData


#: Environment variable / HTTP header name used to convey child resources
#: (e.g. swagger docs) to the MCP server at startup.  See module
#: docstring for the JSON shape.
CHILD_RESOURCES_KEY = "CS_AGENTS_CHILD_RESOURCES"
CHILD_RESOURCES_HEADER = "X-CS-Child-Resources"


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# URL / env helpers
# ---------------------------------------------------------------------------


def _find_url(env: dict[str, Any]) -> str | None:
    """Pick the URL value out of an env_vars dict by name convention.

    See module docstring for the convention.  Returns ``None`` if no
    matching key is present.
    """
    # Exact match first so a literal "URL" wins over "BASE_URL" if both
    # are somehow present.
    for key, value in env.items():
        if key.upper() == "URL" and value is not None:
            return str(value)
    # Fall back to any *_URL suffix.
    for key, value in env.items():
        if key.upper().endswith("_URL") and value is not None:
            return str(value)
    return None


def _stringify_subprocess_env(env: dict[str, Any]) -> dict[str, str]:
    """Coerce env_vars to a ``str -> str`` mapping for ``subprocess.Popen``.

    JSONB lets admins store ints, bools, etc.; the subprocess env API
    only accepts strings.  ``None`` values are dropped entirely because
    passing ``None`` to ``Popen(env=...)`` is undefined across
    platforms.
    """
    return {k: str(v) for k, v in env.items() if v is not None}


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def _serialize_child_resources(
    children: Iterable[ChildResourceData],
) -> str:
    """Pack child resources into the JSON payload that gets handed to
    the MCP server at startup (stdio: env var; HTTP: header).

    Shape::

        [
            {
                "id": "<uuid>",
                "type": "swagger",
                "name": "...",
                "url": "...",
                "headers": {"Authorization": "..."}
            },
            ...
        ]

    Only enabled children are included; the cache's row-fetcher has
    already filtered out disabled / wrong-parent rows, so this is
    really just a contract reminder.
    """
    payload = [
        {
            "id": str(c.id),
            "type": c.type,
            "name": c.name,
            "url": c.url,
            "headers": c.headers,
        }
        for c in children
        if c.enabled
    ]
    return json.dumps(payload, default=str)


async def build_mcp_tools(
    server: MCPServer,
    env: dict[str, Any],
    type_row: MCPServerType | None,
    child_resources: list[ChildResourceData] | None = None,
) -> MCPTools:
    """Construct and connect an :class:`MCPTools` for a given server row.

    Parameters
    ----------
    server
        The :class:`MCPServer` row (already detached from any session
        by the caller).
    env
        Decrypted ``env_vars`` for this server.  For ``stdio`` these
        become the subprocess environment; for HTTP transports they
        carry the URL (see module docstring) and any extra
        configuration the type's ``config_schema`` declares.
    type_row
        The parent :class:`MCPServerType`.  May be ``None`` if the
        catalog row was deleted out from under the instance — that's
        treated as a fatal builder error.
    child_resources
        Already-decrypted child resource records to expose to this
        spawn.  Optional / defaults to ``[]`` for agents that don't
        attach any children to this server.  When non-empty:

        * **stdio**: a JSON payload is added under the
          :data:`CHILD_RESOURCES_KEY` env var so the subprocess can
          read it on startup.
        * **sse / streamable_http**: a JSON payload is added as the
          :data:`CHILD_RESOURCES_HEADER` HTTP header via Agno's
          ``header_provider`` callback.

    Returns
    -------
    A connected :class:`MCPTools`.  The cache will manage its lifecycle
    from here (close on idle / version-bump / shutdown).

    Raises
    ------
    RuntimeError
        On any builder-time misconfiguration: missing type row, missing
        command for stdio, missing URL for HTTP transports, or an
        unknown ``mode`` value.  These bubble up to the cache, which
        treats the build as failed; the runtime route maps that to a
        5xx so the operator sees the cause.
    """
    if type_row is None:
        raise RuntimeError(
            f"mcp_server {server.id}: parent type {server.type_id} not found "
            "(catalog likely out of sync — was the type deleted?)"
        )

    mode = type_row.mode
    children = list(child_resources or [])
    children_payload = (
        _serialize_child_resources(children) if children else None
    )

    if mode == "stdio":
        command = server.command or type_row.default_command
        if not command:
            raise RuntimeError(
                f"mcp_server {server.id}: stdio mode requires a command "
                "(neither server.command nor type.default_command is set)"
            )
        # server.args is a JSONB column with a "[]" default, so a
        # never-edited row carries an empty list — use the type
        # defaults in that case so admins don't have to copy them in
        # every time.
        args = server.args if server.args else (type_row.default_args or [])
        subprocess_env = _stringify_subprocess_env(env)

        # Stitch the child resources payload into the subprocess env
        # under a well-known key.  We pick this layer (not the env_vars
        # dict directly) so the admin's saved env_vars stay clean and
        # the platform-injected key never lands in the DB.
        if children_payload is not None:
            subprocess_env[CHILD_RESOURCES_KEY] = children_payload

        # Agno's MCPTools doesn't accept ``args=`` / ``env=`` directly
        # for stdio — those go inside a :class:`StdioServerParameters`
        # passed as ``server_params``.  Passing ``env`` only when
        # non-empty so :class:`StdioServerParameters` falls back to
        # the curated default (HOME, PATH, SHELL, etc. from the parent
        # process) when the admin hasn't set any env_vars.  ``env={}``
        # would otherwise mean "no environment at all" and break any
        # subprocess that relies on PATH for binary lookup.
        params_kwargs: dict[str, Any] = {
            "command": command,
            "args": list(args),
        }
        if subprocess_env:
            params_kwargs["env"] = subprocess_env
        tools = MCPTools(
            server_params=StdioServerParameters(**params_kwargs),
            transport="stdio",
        )

    elif mode in ("sse", "streamable_http"):
        url = _find_url(env)
        if not url:
            raise RuntimeError(
                f"mcp_server {server.id}: {mode} mode requires a URL in "
                "env_vars (expected a key named 'URL' or ending in '_URL')"
            )
        # DB uses underscore form; Agno expects the hyphenated literal.
        agno_transport = "sse" if mode == "sse" else "streamable-http"

        mcp_kwargs: dict[str, Any] = {"url": url, "transport": agno_transport}
        if children_payload is not None:
            # ``header_provider`` is called by Agno's MCPTools each
            # time it opens a new request — closing over the payload
            # is fine because the cache key includes the child resource
            # set, so different child sets get different MCPTools
            # instances (each with its own provider).
            def _make_header_provider(payload: str):
                def _provider() -> dict[str, Any]:
                    return {CHILD_RESOURCES_HEADER: payload}

                return _provider

            mcp_kwargs["header_provider"] = _make_header_provider(
                children_payload
            )

        tools = MCPTools(**mcp_kwargs)

    else:
        raise RuntimeError(
            f"mcp_server {server.id}: unknown transport mode {mode!r} "
            "(expected one of stdio / sse / streamable_http)"
        )

    # Connect outside the constructor so a failure here propagates as a
    # plain exception — the cache's BuildPlan factory will surface it
    # and treat the entry as never-built.
    await tools.connect()
    return tools

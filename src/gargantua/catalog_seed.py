"""Canonical MCP server-type catalog shipped with the platform.

This is intentionally Python (not YAML or JSON) so that:

* the data is type-checkable,
* IDEs surface every field on autocomplete,
* refactors stay safe — renaming a key fails at import, not at runtime.

``CANONICAL_TYPES`` is a list of dicts whose shape matches the
:func:`gargantua.repo.mcp_server_types.create` call signature:

    {
        "slug": ...,
        "name": ...,
        "description": ...,
        "mode": ...,
        "default_command": ...,
        "default_args": [...],
        "config_schema": [
            {"name", "label", "type", "is_secret", "required", "default"},
            ...
        ],
        "default_env_vars": {...},
        "optional_env_vars": {...},
        "default_swagger_url": ...,
        "supports_swagger_child": ...,
    }

Entries are seeded by ``gargantua-admin seed-catalog``.  Adding a new
canonical entry only requires appending to this list — no migration,
no deploy gating, no separate file to keep in sync.

Conservative entry set: a handful of well-understood types so the
operator surface is exercisable end-to-end.  Add more by appending to
:data:`CANONICAL_TYPES` and re-running ``gargantua-admin seed-catalog``.
"""

from __future__ import annotations

from typing import Any, Final


CANONICAL_TYPES: Final[list[dict[str, Any]]] = [
    # -----------------------------------------------------------------------
    # postgres — read-only SQL access to a Postgres database
    # -----------------------------------------------------------------------
    {
        "slug": "postgres",
        "name": "PostgreSQL",
        "description": (
            "Run read-only SQL queries against a Postgres database. "
            "The agent receives schema introspection plus a `query` tool."
        ),
        "mode": "stdio",
        "default_command": "uvx",
        "default_args": ["postgres-mcp"],
        "config_schema": [
            {
                "name": "DSN",
                "label": "Connection string (postgresql://...)",
                "type": "password",
                "is_secret": True,
                "required": True,
            },
            {
                "name": "READ_ONLY",
                "label": "Refuse writes (recommended)",
                "type": "select",
                "is_secret": False,
                "required": False,
                "default": "true",
            },
        ],
        "default_env_vars": {"READ_ONLY": "true"},
        "optional_env_vars": {},
        "default_swagger_url": None,
        "supports_swagger_child": False,
    },
    # -----------------------------------------------------------------------
    # opensearch — search + aggregations against an OpenSearch cluster
    # -----------------------------------------------------------------------
    {
        "slug": "opensearch",
        "name": "OpenSearch",
        "description": (
            "Run search and aggregation queries against an OpenSearch "
            "cluster.  Useful for log / metric exploration."
        ),
        "mode": "sse",
        "default_command": None,  # SSE servers are reached by URL, not spawned.
        "default_args": [],
        "config_schema": [
            {
                "name": "OPENSEARCH_URL",
                "label": "Cluster URL (https://...)",
                "type": "text",
                "is_secret": False,
                "required": True,
            },
            {
                "name": "OPENSEARCH_USERNAME",
                "label": "Username",
                "type": "text",
                "is_secret": False,
                "required": False,
            },
            {
                "name": "OPENSEARCH_PASSWORD",
                "label": "Password",
                "type": "password",
                "is_secret": True,
                "required": False,
            },
        ],
        "default_env_vars": {},
        "optional_env_vars": {},
        "default_swagger_url": None,
        "supports_swagger_child": False,
    },
    # -----------------------------------------------------------------------
    # swagger-mcp — turn any Swagger / OpenAPI doc into a tool surface
    # -----------------------------------------------------------------------
    {
        "slug": "swagger-mcp",
        "name": "Swagger / OpenAPI",
        "description": (
            "Adapter that exposes every operation in a Swagger / OpenAPI "
            "document as an MCP tool.  Configure one server instance and "
            "attach child resources for each API the agent should reach."
        ),
        "mode": "streamable_http",
        "default_command": None,
        "default_args": [],
        "config_schema": [
            {
                "name": "BASE_URL",
                "label": "Adapter base URL",
                "type": "text",
                "is_secret": False,
                "required": True,
            },
        ],
        "default_env_vars": {},
        "optional_env_vars": {},
        "default_swagger_url": None,
        "supports_swagger_child": True,
    },
]


__all__ = ["CANONICAL_TYPES"]

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
    # argocd-mcp — manage / query Argo CD applications via its REST API
    # -----------------------------------------------------------------------
    {
        "slug": "argocd-mcp",
        "name": "Argo CD",
        "description": (
            "Manage and query Argo CD applications, sync status, and "
            "deployment health via the Argo CD REST API."
        ),
        "mode": "stdio",
        "default_command": "npx",
        "default_args": ["argocd-mcp@latest", "stdio"],
        "config_schema": [
            {
                "name": "ARGOCD_BASE_URL",
                "label": "Argo CD base URL (https://...)",
                "type": "text",
                "is_secret": False,
                "required": True,
            },
            {
                "name": "ARGOCD_API_TOKEN",
                "label": "Argo CD API token",
                "type": "password",
                "is_secret": True,
                "required": True,
            },
            {
                "name": "ARGOCD_VERIFY_SSL",
                "label": "Verify SSL certificates",
                "type": "select",
                "is_secret": False,
                "required": False,
                "default": "false",
            },
            {
                "name": "NODE_TLS_REJECT_UNAUTHORIZED",
                "label": "Node TLS reject unauthorized (0 = disabled)",
                "type": "select",
                "is_secret": False,
                "required": False,
                "default": "0",
            },
        ],
        "default_env_vars": {
            "ARGOCD_VERIFY_SSL": "false",
            "NODE_TLS_REJECT_UNAUTHORIZED": "0",
        },
        "optional_env_vars": {},
        "default_swagger_url": None,
        "supports_swagger_child": False,
    },
    # -----------------------------------------------------------------------
    # zabbix-mcp — query hosts, items, triggers, and problems from Zabbix
    # -----------------------------------------------------------------------
    {
        "slug": "zabbix-mcp",
        "name": "Zabbix",
        "description": (
            "Query Zabbix hosts, items, triggers, and active problems. "
            "Useful for monitoring and alerting investigations."
        ),
        "mode": "stdio",
        "default_command": "python3",
        "default_args": ["./mcp-servers/zabbix/server.py"],
        "config_schema": [
            {
                "name": "ZABBIX_URL",
                "label": "Zabbix API URL (https://.../api_jsonrpc.php)",
                "type": "text",
                "is_secret": False,
                "required": True,
            },
            {
                "name": "ZABBIX_USER",
                "label": "Username",
                "type": "text",
                "is_secret": False,
                "required": True,
            },
            {
                "name": "ZABBIX_PASSWORD",
                "label": "Password",
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
    # kubernetes-mcp-server — inspect and operate Kubernetes clusters
    # -----------------------------------------------------------------------
    {
        "slug": "kubernetes-mcp-server",
        "name": "Kubernetes",
        "description": (
            "Inspect and operate Kubernetes clusters: list resources, read "
            "logs, and describe workloads. Uses the ambient kubeconfig."
        ),
        "mode": "stdio",
        "default_command": "npx",
        "default_args": ["-y", "kubernetes-mcp-server@latest"],
        "config_schema": [],
        "default_env_vars": {},
        "optional_env_vars": {},
        "default_swagger_url": None,
        "supports_swagger_child": False,
    },
    # -----------------------------------------------------------------------
    # dpa-mcp — query the DPA platform via its REST API
    # -----------------------------------------------------------------------
    {
        "slug": "dpa-mcp",
        "name": "DPA",
        "description": (
            "Query the DPA platform via its REST API using service "
            "credentials."
        ),
        "mode": "stdio",
        "default_command": "python3",
        "default_args": ["./mcp-servers/dpa/server.py"],
        "config_schema": [
            {
                "name": "DPA_BASE_URL",
                "label": "DPA base URL (https://...)",
                "type": "text",
                "is_secret": False,
                "required": True,
            },
            {
                "name": "DPA_USERNAME",
                "label": "Username",
                "type": "text",
                "is_secret": False,
                "required": True,
            },
            {
                "name": "DPA_PASSWORD",
                "label": "Password",
                "type": "password",
                "is_secret": True,
                "required": True,
            },
        ],
        "default_env_vars": {},
        "optional_env_vars": {},
        "default_swagger_url": None,
        "supports_swagger_child": False,
    },
]


__all__ = ["CANONICAL_TYPES"]

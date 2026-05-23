"""Unit tests for :mod:`gargantua.mcp_tools`.

The production :func:`build_mcp_tools` is what gets plugged into the
cache's ``ToolsBuilder`` slot — it's the only place in the codebase
that constructs a real ``agno.tools.mcp.MCPTools`` instance.

We can't safely *connect* an MCPTools in unit tests (stdio would fork
a subprocess; sse/streamable-http would open a network connection),
so these tests patch ``MCPTools`` with a stub that records its
constructor kwargs and confirms ``connect()`` was awaited.  The exact
keys we assert against come from Agno's published signature:

    MCPTools(server_params=..., url=..., transport=..., header_provider=...)

If Agno changes that signature in a future bump, these tests fail
loudly and we update the builder accordingly.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest

from gargantua.db.models import MCPServer, MCPServerType
from gargantua.mcp_cache import ChildResourceData
from gargantua.mcp_tools import (
    CHILD_RESOURCES_HEADER,
    CHILD_RESOURCES_KEY,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _type_row(**overrides: Any) -> MCPServerType:
    defaults: dict[str, Any] = dict(
        id=uuid4(),
        slug="test-type",
        name="Test type",
        description=None,
        mode="stdio",
        default_command="echo",
        default_args=[],
        config_schema=[],
        default_env_vars={},
        optional_env_vars={},
        default_swagger_url=None,
        supports_swagger_child=False,
    )
    defaults.update(overrides)
    return MCPServerType(**defaults)


def _server_row(**overrides: Any) -> MCPServer:
    defaults: dict[str, Any] = dict(
        id=uuid4(),
        type_id=uuid4(),
        name="prod-instance",
        env_tag="prod",
        command=None,
        args=[],
        env_vars=None,
        env_var_iv=None,
        env_var_kek_id=None,
    )
    defaults.update(overrides)
    return MCPServer(**defaults)


class _RecordingMCPTools:
    """Drop-in replacement for ``agno.tools.mcp.MCPTools``.

    Records constructor kwargs and provides an awaitable ``connect``
    so the builder's await-chain is exercised.
    """

    instances: list[_RecordingMCPTools] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        self.connected = False
        _RecordingMCPTools.instances.append(self)

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    @classmethod
    def reset(cls) -> None:
        cls.instances.clear()


@pytest.fixture(autouse=True)
def _patched_mcp_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace ``MCPTools`` symbol used by the builder for the duration
    of each test.  Auto-applied so every test gets a clean slate."""
    _RecordingMCPTools.reset()
    monkeypatch.setattr("gargantua.mcp_tools.MCPTools", _RecordingMCPTools, raising=True)


# ---------------------------------------------------------------------------
# stdio
# ---------------------------------------------------------------------------


async def test_stdio_uses_server_command_when_set() -> None:
    """When the server row overrides command/args, those win over the
    type's defaults.  The route packages everything into a
    :class:`StdioServerParameters` (Agno's MCPTools doesn't take
    ``args=`` / ``env=`` directly for stdio)."""
    from gargantua.mcp_tools import build_mcp_tools

    type_row = _type_row(mode="stdio", default_command="default-cmd", default_args=["a"])
    server = _server_row(command="server-cmd", args=["b", "c"])
    env = {"FOO": "bar", "API_KEY": "secret"}

    tools = await build_mcp_tools(server, env, type_row)

    assert isinstance(tools, _RecordingMCPTools)
    assert tools.connected is True
    assert tools.kwargs["transport"] == "stdio"
    sp = tools.kwargs["server_params"]
    assert sp.command == "server-cmd"
    assert sp.args == ["b", "c"]
    # env must be a str->str dict (subprocess env requirement).
    assert sp.env == {"FOO": "bar", "API_KEY": "secret"}


async def test_stdio_falls_back_to_type_defaults() -> None:
    """If server.command / server.args are not set, type defaults apply."""
    from gargantua.mcp_tools import build_mcp_tools

    type_row = _type_row(mode="stdio", default_command="uvx", default_args=["postgres-mcp"])
    server = _server_row(command=None, args=[])
    env: dict[str, Any] = {}

    tools = await build_mcp_tools(server, env, type_row)

    sp = tools.kwargs["server_params"]
    assert sp.command == "uvx"
    assert sp.args == ["postgres-mcp"]


async def test_stdio_coerces_env_values_to_strings() -> None:
    """Subprocess env vars must be strings — int/bool/None values from
    JSONB get stringified.  ``None`` is filtered out (subprocess won't
    accept None values)."""
    from gargantua.mcp_tools import build_mcp_tools

    type_row = _type_row(mode="stdio", default_command="x")
    server = _server_row()
    env = {"READ_ONLY": True, "PORT": 5432, "OPT": None}

    tools = await build_mcp_tools(server, env, type_row)

    sp = tools.kwargs["server_params"]
    assert sp.env["READ_ONLY"] == "True"
    assert sp.env["PORT"] == "5432"
    # ``None`` should NOT appear — passing None to a subprocess env is
    # undefined behaviour across platforms.
    assert "OPT" not in sp.env


async def test_stdio_with_no_env_omits_env_so_defaults_apply() -> None:
    """When the admin has set no env_vars, we must NOT pass an empty
    dict to StdioServerParameters — that'd give the subprocess zero
    environment (no PATH, no HOME).  We omit the kwarg so Agno's
    default-env logic kicks in."""
    from gargantua.mcp_tools import build_mcp_tools

    type_row = _type_row(mode="stdio", default_command="x")
    server = _server_row()
    tools = await build_mcp_tools(server, {}, type_row)

    sp = tools.kwargs["server_params"]
    # StdioServerParameters has a curated default env when none is set;
    # we don't pin the exact contents (Agno owns that), just that we
    # didn't force an empty one.
    assert sp.env is None or "PATH" in sp.env


async def test_stdio_without_command_raises() -> None:
    """Neither server.command nor type.default_command set: should be a
    clear builder-time error, not a confusing subprocess crash later."""
    from gargantua.mcp_tools import build_mcp_tools

    type_row = _type_row(mode="stdio", default_command=None)
    server = _server_row(command=None)

    with pytest.raises(RuntimeError, match="command"):
        await build_mcp_tools(server, {}, type_row)


# ---------------------------------------------------------------------------
# sse
# ---------------------------------------------------------------------------


async def test_sse_uses_url_from_env() -> None:
    """SSE servers don't have a command — the URL comes from env_vars.
    Convention: a key named exactly 'URL' or ending in '_URL'."""
    from gargantua.mcp_tools import build_mcp_tools

    type_row = _type_row(mode="sse", default_command=None)
    server = _server_row()
    env = {"OPENSEARCH_URL": "https://opensearch.example/sse"}

    tools = await build_mcp_tools(server, env, type_row)

    # Agno expects 'sse' literal as transport.
    assert tools.kwargs["transport"] == "sse"
    assert tools.kwargs["url"] == "https://opensearch.example/sse"
    # No command for HTTP-style transports.
    assert "command" not in tools.kwargs or tools.kwargs["command"] is None


async def test_sse_with_plain_url_key() -> None:
    """A field literally named ``URL`` (no prefix) should also work."""
    from gargantua.mcp_tools import build_mcp_tools

    type_row = _type_row(mode="sse", default_command=None)
    server = _server_row()
    tools = await build_mcp_tools(server, {"URL": "https://x.example/sse"}, type_row)
    assert tools.kwargs["url"] == "https://x.example/sse"


async def test_sse_without_url_raises() -> None:
    from gargantua.mcp_tools import build_mcp_tools

    type_row = _type_row(mode="sse", default_command=None)
    server = _server_row()

    with pytest.raises(RuntimeError, match="URL"):
        await build_mcp_tools(server, {"NOTAURL": "x"}, type_row)


# ---------------------------------------------------------------------------
# streamable_http
# ---------------------------------------------------------------------------


async def test_streamable_http_translates_transport_to_hyphenated_form() -> None:
    """Our DB stores ``streamable_http`` (underscore) for Postgres
    enum-friendliness; Agno's Literal expects ``streamable-http``
    (hyphen).  The builder is the translation point."""
    from gargantua.mcp_tools import build_mcp_tools

    type_row = _type_row(mode="streamable_http", default_command=None)
    server = _server_row()
    env = {"BASE_URL": "https://adapter.example/mcp"}

    tools = await build_mcp_tools(server, env, type_row)

    assert tools.kwargs["transport"] == "streamable-http"
    assert tools.kwargs["url"] == "https://adapter.example/mcp"


async def test_streamable_http_without_url_raises() -> None:
    from gargantua.mcp_tools import build_mcp_tools

    type_row = _type_row(mode="streamable_http", default_command=None)
    server = _server_row()

    with pytest.raises(RuntimeError, match="URL"):
        await build_mcp_tools(server, {"FOO": "bar"}, type_row)


# ---------------------------------------------------------------------------
# Error paths shared across transports
# ---------------------------------------------------------------------------


async def test_missing_type_row_raises() -> None:
    """``type_row=None`` means the catalog is out of sync with the
    instance — fail fast at builder time with an informative message."""
    from gargantua.mcp_tools import build_mcp_tools

    server = _server_row()

    with pytest.raises(RuntimeError, match="type"):
        await build_mcp_tools(server, {}, None)


async def test_unknown_mode_raises() -> None:
    """Defensive: schema CHECK constraints us to known modes today, but
    if the DB grows a new value the builder should refuse rather than
    silently passing garbage to Agno."""
    from gargantua.mcp_tools import build_mcp_tools

    type_row = _type_row(mode="websocket", default_command=None)
    server = _server_row()

    with pytest.raises(RuntimeError, match=r"websocket|unknown"):
        await build_mcp_tools(server, {}, type_row)


async def test_connect_failure_propagates() -> None:
    """If MCPTools.connect() raises (server unreachable, bad command,
    etc.), the exception must surface so the cache treats this build
    as failed and the route returns 5xx, not a half-built handle."""
    from gargantua.mcp_tools import build_mcp_tools

    type_row = _type_row(mode="stdio", default_command="x")
    server = _server_row()

    async def boom(self) -> None:
        raise OSError("could not spawn subprocess")

    with patch.object(_RecordingMCPTools, "connect", boom):
        with pytest.raises(OSError, match="subprocess"):
            await build_mcp_tools(server, {}, type_row)


# ---------------------------------------------------------------------------
# Child resources — payload propagation
# ---------------------------------------------------------------------------


def _child(
    *,
    parent_id: Any,
    type_: str = "swagger",
    name: str = "sw",
    url: str = "https://api.example/swagger.json",
    headers: dict[str, Any] | None = None,
    enabled: bool = True,
) -> ChildResourceData:
    return ChildResourceData(
        id=uuid4(),
        parent_mcp_server_id=parent_id,
        type=type_,
        name=name,
        url=url,
        headers=headers or {},
        enabled=enabled,
    )


async def test_stdio_with_no_children_omits_payload_env_var() -> None:
    """An agent that doesn't attach any child resources to this server
    must NOT have ``CS_AGENTS_CHILD_RESOURCES`` set — the MCP server
    has to be able to tell "no children" from "empty children" if it
    chooses (a single empty-list payload could be a valid filter)."""
    from gargantua.mcp_tools import build_mcp_tools

    type_row = _type_row(mode="stdio", default_command="x")
    server = _server_row()
    tools = await build_mcp_tools(server, {"X": "y"}, type_row, child_resources=[])

    sp = tools.kwargs["server_params"]
    assert CHILD_RESOURCES_KEY not in (sp.env or {})


async def test_stdio_with_children_sets_env_var_with_json_payload() -> None:
    """The payload's JSON should preserve everything the MCP server
    needs: id, type, name, url, and decrypted headers."""
    from gargantua.mcp_tools import build_mcp_tools

    parent_id = uuid4()
    type_row = _type_row(mode="stdio", default_command="x")
    server = _server_row(id=parent_id)
    children = [
        _child(
            parent_id=parent_id,
            name="petstore",
            url="https://petstore.example/swagger.json",
            headers={"Authorization": "Bearer abc"},
        ),
        _child(
            parent_id=parent_id,
            name="ohlc",
            url="https://ohlc.example/openapi.json",
        ),
    ]

    tools = await build_mcp_tools(server, {"X": "y"}, type_row, child_resources=children)
    sp = tools.kwargs["server_params"]
    raw = sp.env[CHILD_RESOURCES_KEY]
    payload = json.loads(raw)

    assert len(payload) == 2
    by_name = {entry["name"]: entry for entry in payload}
    assert by_name["petstore"]["url"] == "https://petstore.example/swagger.json"
    assert by_name["petstore"]["headers"] == {"Authorization": "Bearer abc"}
    assert by_name["ohlc"]["headers"] == {}
    # IDs in the payload are stringified UUIDs (JSON-safe).
    for entry in payload:
        assert isinstance(entry["id"], str)
        assert len(entry["id"]) == 36


async def test_stdio_disabled_children_are_filtered_from_payload() -> None:
    """Belt-and-braces: even if a disabled child slips into the
    builder's input, it must not appear in the payload.  The cache's
    fetcher already filters disabled rows out, but the builder is the
    last line of defence."""
    from gargantua.mcp_tools import build_mcp_tools

    parent_id = uuid4()
    type_row = _type_row(mode="stdio", default_command="x")
    server = _server_row(id=parent_id)
    children = [
        _child(parent_id=parent_id, name="live"),
        _child(parent_id=parent_id, name="retired", enabled=False),
    ]
    tools = await build_mcp_tools(server, {"X": "y"}, type_row, child_resources=children)
    payload = json.loads(tools.kwargs["server_params"].env[CHILD_RESOURCES_KEY])
    names = {entry["name"] for entry in payload}
    assert names == {"live"}


async def test_sse_with_children_attaches_header_provider() -> None:
    """For SSE transports the child resources travel as an HTTP
    header (we don't have a subprocess env to set).  Agno's
    ``header_provider`` callback returns the dict each request, so we
    can't pre-serialize it into the constructor args directly."""
    from gargantua.mcp_tools import build_mcp_tools

    parent_id = uuid4()
    type_row = _type_row(mode="sse", default_command=None)
    server = _server_row(id=parent_id)
    children = [_child(parent_id=parent_id, name="logs")]

    tools = await build_mcp_tools(
        server,
        {"URL": "https://sse.example"},
        type_row,
        child_resources=children,
    )

    provider = tools.kwargs["header_provider"]
    headers = provider()
    assert CHILD_RESOURCES_HEADER in headers
    payload = json.loads(headers[CHILD_RESOURCES_HEADER])
    assert len(payload) == 1
    assert payload[0]["name"] == "logs"


async def test_streamable_http_with_children_attaches_header_provider() -> None:
    from gargantua.mcp_tools import build_mcp_tools

    parent_id = uuid4()
    type_row = _type_row(mode="streamable_http", default_command=None)
    server = _server_row(id=parent_id)
    children = [_child(parent_id=parent_id, name="orders", headers={"X-API-Key": "k"})]

    tools = await build_mcp_tools(
        server,
        {"BASE_URL": "https://api.example/mcp"},
        type_row,
        child_resources=children,
    )

    assert tools.kwargs["transport"] == "streamable-http"
    provider = tools.kwargs["header_provider"]
    headers = provider()
    payload = json.loads(headers[CHILD_RESOURCES_HEADER])
    assert payload[0]["headers"] == {"X-API-Key": "k"}


async def test_sse_with_no_children_omits_header_provider() -> None:
    """Symmetric to the stdio case: no children means no header
    provider, so the SSE connection's normal request shape isn't
    affected."""
    from gargantua.mcp_tools import build_mcp_tools

    type_row = _type_row(mode="sse", default_command=None)
    server = _server_row()
    tools = await build_mcp_tools(
        server,
        {"URL": "https://x.example"},
        type_row,
        child_resources=[],
    )
    assert "header_provider" not in tools.kwargs

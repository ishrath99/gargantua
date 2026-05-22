"""End-to-end smoke test for the full backend stack.

What this test is for
---------------------

Every other test in the suite exercises one slice of the stack: the
repos, the schemas, a single route under a hand-built FastAPI app, the
cache primitive, the registry mapping, etc.  None of them catches the
class of bug that lives in the **seams** — lifespan-vs-route ordering,
``app.state`` plumbing, the AgentOS sub-app mount interacting with our
overrides, the bootstrap-admin path running against a real schema, the
runtime route picking up the cache built by lifespan, the OpenAPI
import graph of the whole package.

So this file spins up the **real** :func:`gargantua.main.create_app`
with the real lifespan, the real ``/v1`` AgentOS mount, the real JWT
flow, the real AES-GCM encryption of MCP env_vars, and walks through
the full admin-creates -> user-runs narrative.

What we still mock
------------------

Two things, kept deliberately narrow:

* :func:`gargantua.api.runs.build_agno_agent` — so the route doesn't
  try to resolve a real LLM (no API key in tests, no network).
* :func:`gargantua.mcp_tools.build_mcp_tools` — so the cache doesn't
  fork an actual MCP subprocess when a run leases its tools.

Everything else (DB, encryption, JWTs, route ordering, cache
acquire/release, audit log writes) runs against the real
implementation.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

# ---------------------------------------------------------------------------
# Narrow fakes — only the two we patch
# ---------------------------------------------------------------------------


class _FakeClosable:
    """Stand-in for what ``build_mcp_tools`` would have returned."""

    def __init__(self) -> None:
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


class _FakeRunOutput:
    """Stand-in for :class:`agno.run.agent.RunOutput`."""

    def __init__(self, **payload: Any) -> None:
        self._payload = payload

    def to_dict(self) -> dict[str, Any]:
        return dict(self._payload)


class _FakeEvent:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def to_dict(self) -> dict[str, Any]:
        return dict(self._payload)


class _FakeAgent:
    """Stand-in for what ``build_agno_agent`` would have returned.

    Records every ``arun`` call so the smoke test can assert that
    request inputs and JWT-derived ``user_id`` reached the agent
    intact.
    """

    def __init__(self) -> None:
        self.arun_calls: list[dict[str, Any]] = []
        self._result: Any = None

    def set_result(self, result: Any) -> None:
        self._result = result

    def arun(self, input: Any, **kwargs: Any) -> Any:
        # Real ``agno.agent.Agent.arun`` is a sync method that returns
        # either an AsyncIterator (stream=True) or a coroutine
        # resolving to a RunOutput (stream=False).
        self.arun_calls.append({"input": input, **kwargs})
        if kwargs.get("stream"):
            return self._result() if callable(self._result) else self._result
        return self._await_result()

    async def _await_result(self) -> Any:
        return self._result


# ---------------------------------------------------------------------------
# Fixture: configure env exactly the way production would
# ---------------------------------------------------------------------------


def _write_keypair(out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = out_dir / "jwt_private.pem"
    pub = out_dir / "jwt_public.pem"
    priv.write_bytes(
        private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    pub.write_bytes(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return priv, pub


def _reset_caches() -> None:
    from gargantua.auth import tokens
    from gargantua.db import session as session_module
    from gargantua.settings import get_settings

    get_settings.cache_clear()
    tokens.reset_keys_cache()
    session_module.get_engine.cache_clear()
    session_module.get_session_factory.cache_clear()


BOOTSTRAP_ADMIN_USER = "root"
BOOTSTRAP_ADMIN_PASS = "rootpw!1"


@pytest.fixture
def configured(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    truncate_db: Engine,
    _db_ready: str,
) -> Iterator[None]:
    """Configure every env var ``create_app`` reads, as production would.

    The ``truncate_db`` fixture wipes the schema before the test, which
    is critical: ``bootstrap_admin_if_needed`` only inserts when the
    ``users`` table is empty.  Without the truncate, a prior test's
    user rows would suppress bootstrap and the login step would fail
    with a confusing 401.
    """
    priv, pub = _write_keypair(tmp_path / "keys")

    monkeypatch.setenv("JWT_PRIVATE_KEY_PATH", str(priv))
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", str(pub))
    monkeypatch.setenv("JWT_ISSUER", "gargantua")
    monkeypatch.setenv("JWT_AUDIENCE", "gargantua")
    monkeypatch.setenv("JWT_ACCESS_TTL_SECONDS", "60")

    # Both URLs point at the same physical DB; the async variant feeds
    # request-handler sessions, the sync one feeds Alembic and Agno's
    # PostgresDb.
    monkeypatch.setenv("DATABASE_URL_ASYNC", _db_ready)
    monkeypatch.setenv("DATABASE_URL", _db_ready)

    # AES-256 KEK; pinned deterministic value so kek_id fingerprints are
    # repeatable across runs.
    monkeypatch.setenv("MASTER_KEY", base64.b64encode(b"\x42" * 32).decode("ascii"))

    # Both must be set for bootstrap_admin to fire.
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", BOOTSTRAP_ADMIN_USER)
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", BOOTSTRAP_ADMIN_PASS)

    # No CORS in tests; default would inject middleware we don't want
    # interfering with TestClient.
    monkeypatch.setenv("CORS_ORIGINS", "")

    _reset_caches()
    yield
    _reset_caches()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Helpers used by both lifecycle tests
# ---------------------------------------------------------------------------


def _login_admin(client: TestClient) -> str:
    """Exchange bootstrap-admin credentials for an access token."""
    r = client.post(
        "/auth/login",
        json={"username": BOOTSTRAP_ADMIN_USER, "password": BOOTSTRAP_ADMIN_PASS},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _create_type_server_agent(client: TestClient, token: str) -> tuple[str, str, str]:
    """Create a stdio MCP server type + instance + agent in one shot.

    Returns ``(type_id, server_id, agent_id)`` for use in subsequent
    assertions.
    """
    headers = _auth(token)

    # Catalog: a stdio type that would normally spawn `echo mcp` as its
    # subprocess.  build_mcp_tools is patched so nothing is actually
    # forked.
    r = client.post(
        "/admin/mcp-server-types",
        headers=headers,
        json={
            "slug": "echo-mcp",
            "name": "Echo MCP",
            "description": "Toy MCP server for smoke tests",
            "mode": "stdio",
            "default_command": "echo",
            "default_args": ["mcp"],
            "config_schema": [
                {
                    "name": "GREETING",
                    "label": "Greeting prefix",
                    "type": "text",
                    "is_secret": False,
                    "required": False,
                    "default": "hi",
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    type_id = r.json()["id"]

    # Instance: includes a secret env var so the AES encryption path
    # is exercised (and the response masking).
    r = client.post(
        "/admin/mcp-servers",
        headers=headers,
        json={
            "type_id": type_id,
            "name": "echo-prod",
            "env_tag": "prod",
            "env_vars": {"GREETING": "hello"},
        },
    )
    assert r.status_code == 201, r.text
    server_id = r.json()["id"]

    # Agent: references the server so the run-route's lease path runs.
    r = client.post(
        "/admin/agents",
        headers=headers,
        json={
            "name": "smoke-bot",
            "description": "End-to-end smoke test agent",
            "model": "openai:gpt-4o-mini",
            "instructions": "Echo whatever the user says.",
            "mcp_server_ids": [server_id],
        },
    )
    assert r.status_code == 201, r.text
    agent_id = r.json()["id"]

    return type_id, server_id, agent_id


# ---------------------------------------------------------------------------
# The big one
# ---------------------------------------------------------------------------


def test_full_lifecycle_nonstreaming(configured) -> None:
    """Login -> create catalog -> create server -> create agent ->
    list -> run.  Asserts at every step so a regression in any layer
    fails with a pinpointable message."""
    from gargantua.main import create_app

    fake_agent = _FakeAgent()
    fake_agent.set_result(_FakeRunOutput(run_id="run-smoke-1", content="echoed: hello"))

    # Patch targets:
    # * ``gargantua.api.runs.build_agno_agent`` — the run route imports
    #   it by name, so this is where the symbol is *looked up* at call
    #   time.
    # * ``gargantua.main.build_mcp_tools`` — main.py imports the symbol
    #   and binds it into ``make_row_fetcher``'s closure when the
    #   lifespan runs.  Patching ``gargantua.mcp_tools.build_mcp_tools``
    #   would be too late (the closure has already captured the old
    #   reference).
    # Match the ToolsBuilder signature exactly — ``child_resources`` is
    # the fourth positional arg; missing it would break the cache's
    # BuildPlan factory at runtime.
    async def _fake_tools_builder(server, env, type_row, child_resources):
        return _FakeClosable()

    with (
        patch("gargantua.api.runs.build_agno_agent", return_value=fake_agent),
        patch("gargantua.main.build_mcp_tools", _fake_tools_builder),
    ):
        app = create_app()
        # ``with TestClient(app) as ...`` triggers the FastAPI lifespan
        # (bootstrap-admin + cache.start()); we'd be testing a half-app
        # without it.
        with TestClient(app, raise_server_exceptions=False) as client:
            # 1. Login as bootstrap admin (must have been auto-created
            #    during lifespan startup).
            token = _login_admin(client)

            # 2. Verify /auth/me round-trips the token and resolves to
            #    a user row.
            r = client.get("/auth/me", headers=_auth(token))
            assert r.status_code == 200, r.text
            me = r.json()
            assert me["username"] == BOOTSTRAP_ADMIN_USER
            assert me["role"] == "admin"
            admin_user_id = me["id"]

            # 3. Create the full chain.
            _, server_id, agent_id = _create_type_server_agent(client, token)

            # 4. The agent must show up in /me/agents (no archived
            #    filter false-positives, no admin-only field leaks).
            r = client.get("/me/agents", headers=_auth(token))
            assert r.status_code == 200
            listing = r.json()
            assert listing["total"] == 1
            agent_item = listing["items"][0]
            assert agent_item["id"] == agent_id
            assert agent_item["name"] == "smoke-bot"
            assert agent_item["mcp_server_ids"] == [server_id]
            # Negative: instructions / agent_config must not leak.
            assert "instructions" not in agent_item
            assert "agent_config" not in agent_item

            # 5. Run the agent.  The route should:
            #    - 200 with the (mocked) RunOutput body
            #    - have leased the MCP server from the cache and
            #      released it (cache snapshot empty / ref_count = 0)
            #    - have forwarded the JWT-derived user_id into arun
            r = client.post(
                f"/v1/agents/{agent_id}/runs",
                headers=_auth(token),
                json={"input": "hello", "session_id": "smoke-sess"},
            )
            assert r.status_code == 200, r.text
            assert r.json() == {
                "run_id": "run-smoke-1",
                "content": "echoed: hello",
            }

            # Inspect the arun call that the (mocked) registry built.
            assert len(fake_agent.arun_calls) == 1
            call = fake_agent.arun_calls[0]
            assert call["input"] == "hello"
            assert call["session_id"] == "smoke-sess"
            assert call["user_id"] == admin_user_id
            assert call["stream"] is False

            # The MCP cache should now be quiescent — the lease was
            # released in the route's finally.
            cache = app.state.mcp_cache
            for snap in cache.inspect():
                assert snap.ref_count == 0, (
                    f"server {snap.server_id} still has ref_count {snap.ref_count} after the run"
                )

            # 6. /admin/audit should have entries for every mutation
            #    we just did (catalog, server, agent).
            r = client.get("/admin/audit", headers=_auth(token))
            assert r.status_code == 200
            audit_actions = {row["action"] for row in r.json()["items"]}
            assert audit_actions >= {
                "mcp_server_type.create",
                "mcp_server.create",
                "agent.create",
            }


# ---------------------------------------------------------------------------
# Streaming variant
# ---------------------------------------------------------------------------


def test_full_lifecycle_streaming(configured) -> None:
    """Same end-to-end shape but exercises the SSE response path.

    Confirms the streaming generator releases its lease after the
    last chunk is consumed — a path that's easy to break when the
    streaming code refactors (e.g. moving release out of finally)."""
    from gargantua.main import create_app

    events = [
        {"event": "start"},
        {"event": "delta", "content": "hello"},
        {"event": "complete"},
    ]

    async def gen():
        for payload in events:
            yield _FakeEvent(payload)

    fake_agent = _FakeAgent()
    fake_agent.set_result(gen)

    async def _fake_tools_builder(server, env, type_row, child_resources):
        return _FakeClosable()

    with (
        patch("gargantua.api.runs.build_agno_agent", return_value=fake_agent),
        patch("gargantua.main.build_mcp_tools", _fake_tools_builder),
    ):
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            token = _login_admin(client)
            _, _server_id, agent_id = _create_type_server_agent(client, token)

            r = client.post(
                f"/v1/agents/{agent_id}/runs",
                headers=_auth(token),
                json={"input": "hi", "stream": True},
            )
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/event-stream")

            # Parse the SSE payload: data: <json>\n\n x N, then
            # data: [DONE]\n\n.
            chunks = [line for line in r.text.split("\n\n") if line.startswith("data: ")]
            assert len(chunks) == len(events) + 1, r.text
            parsed = [json.loads(c.removeprefix("data: ")) for c in chunks[:-1]]
            assert parsed == events
            assert chunks[-1] == "data: [DONE]"

            # After the stream completes, the lease should have been
            # released by the generator's finally.
            cache = app.state.mcp_cache
            for snap in cache.inspect():
                assert snap.ref_count == 0

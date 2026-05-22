"""Integration tests for ``POST /v1/agents/{agent_id}/runs``.

This is the runtime route that overrides Agno's default and turns DB
agent rows into actual runs.  The flow is:

1. Look up the agent row.
2. Acquire MCP server leases from the in-memory cache.
3. Build a transient ``agno.Agent`` via the registry.
4. ``agent.arun(...)`` — JSON (``stream=False``, coroutine) or SSE
   (``stream=True``, sync-returned ``AsyncIterator``).
5. Release the leases (in the streaming case, from inside the
   response generator's ``finally``).

We mock the registry's ``build_agno_agent`` so tests don't have to
spawn real MCP servers or call real LLMs — but every other layer
(repo, cache leasing, JWT gating, SSE response shape) is exercised
against the real code.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from gargantua.db.models import (
    Agent,
    MCPServer,
    MCPServerChildResource,
    MCPServerType,
    User,
)
from gargantua.mcp_cache import BuildPlan, MCPCache


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeClosable:
    """A tool handle stand-in stashed in the cache by the test backend."""

    def __init__(self, label: str = "tool") -> None:
        self.label = label
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


class _FakeRunOutput:
    """Stand-in for :class:`agno.run.agent.RunOutput`.

    We don't construct a real RunOutput because that import drags in
    a ton of Agno surface and the only thing the route reads off it is
    ``to_dict()``.
    """

    def __init__(self, *, run_id: str, content: str, **extra: Any) -> None:
        self._payload = {"run_id": run_id, "content": content, **extra}

    def to_dict(self) -> dict[str, Any]:
        return dict(self._payload)


class _FakeEvent:
    """Stand-in for a single streamed event."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def to_dict(self) -> dict[str, Any]:
        return dict(self._payload)


class _FakeAgent:
    """Stand-in for ``agno.agent.Agent`` returned by ``build_agno_agent``.

    Mirrors the real :meth:`agno.agent.Agent.arun` shape, which is a
    **sync** method that returns either an :class:`AsyncIterator`
    (``stream=True``) or a coroutine resolving to a ``RunOutput``
    (``stream=False``).  Records every call so tests can assert the
    right input / session_id / user_id reached the agent.
    """

    def __init__(self) -> None:
        self.arun_calls: list[dict[str, Any]] = []
        self._result: Any = None

    def set_result(self, result: Any) -> None:
        self._result = result

    def arun(self, input: Any, **kwargs: Any) -> Any:  # noqa: A002
        self.arun_calls.append({"input": input, **kwargs})
        if kwargs.get("stream"):
            # Streaming: real Agno returns the async iterator directly.
            # The test sets _result to a callable that produces one.
            return self._result() if callable(self._result) else self._result
        # Non-streaming: real Agno returns a coroutine resolving to a
        # RunOutput.  Wrap _result in a coroutine to match.
        return self._await_result()

    async def _await_result(self) -> Any:
        return self._result


# ---------------------------------------------------------------------------
# Fixtures (mirror admin test pattern)
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


@pytest.fixture
def configured_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    truncate_db: Engine,  # noqa: ARG001
    _db_ready: str,
) -> Iterator[None]:
    priv, pub = _write_keypair(tmp_path / "keys")
    monkeypatch.setenv("JWT_PRIVATE_KEY_PATH", str(priv))
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", str(pub))
    monkeypatch.setenv("JWT_ISSUER", "gargantua")
    monkeypatch.setenv("JWT_AUDIENCE", "gargantua")
    monkeypatch.setenv("JWT_ACCESS_TTL_SECONDS", "60")
    monkeypatch.setenv("DATABASE_URL_ASYNC", _db_ready)
    _reset_caches()
    yield
    _reset_caches()


class _StubBackend:
    """Tiny row-fetcher used to populate the cache for tests.

    Map a server_id to a hard-coded version + label.  When the route's
    code calls ``cache.acquire(sid)``, the cache's BuildPlan factory
    creates a :class:`_FakeClosable` — never a real subprocess.
    """

    def __init__(self) -> None:
        self.servers: dict[UUID, tuple[int, str]] = {}

    def add(self, sid: UUID, *, label: str = "tool") -> None:
        self.servers[sid] = (1, label)

    async def fetch(
        self,
        sid: UUID,
        child_resource_ids: tuple[UUID, ...] = (),
    ) -> BuildPlan | None:
        # Child resources are ignored in this stub; tests that exercise
        # the child-resource path inject their own fetcher.
        del child_resource_ids
        if sid not in self.servers:
            return None
        version, label = self.servers[sid]

        async def factory() -> _FakeClosable:
            return _FakeClosable(label=label)

        return BuildPlan(version=version, factory=factory)


@pytest.fixture
def backend() -> _StubBackend:
    return _StubBackend()


@pytest.fixture
async def cache(backend: _StubBackend) -> AsyncIterator[MCPCache]:
    c = MCPCache(
        row_fetcher=backend.fetch,
        idle_ttl=timedelta(hours=1),
        reap_interval=timedelta(hours=1),
    )
    yield c
    await c.stop()


@pytest.fixture
def app(configured_env, cache: MCPCache) -> FastAPI:  # noqa: ARG001
    from gargantua.api.auth import router as auth_router
    from gargantua.api.runs import router as runs_router

    a = FastAPI()
    a.include_router(auth_router, prefix="/auth")
    a.include_router(runs_router, prefix="/v1")
    a.state.mcp_cache = cache
    a.state.agno_db = None  # routes accept None gracefully
    return a


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # raise_server_exceptions=False so unhandled errors surface as 500
    # the way they would in production (instead of being re-raised by
    # the test client and bypassing the assertion).
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def sync_session_maker(migrated_engine: Engine) -> sessionmaker:
    return sessionmaker(bind=migrated_engine, expire_on_commit=False, future=True)


@pytest.fixture
def seeded_user(sync_session_maker) -> tuple[UUID, str]:
    from gargantua.auth import SCOPE_USER, mint_access_token
    from gargantua.auth.password import hash_password

    with sync_session_maker() as s:
        u = User(username="alice", password_hash=hash_password("x"), role="user")
        s.add(u)
        s.commit()
        s.refresh(u)
        return u.id, mint_access_token(subject=str(u.id), scopes=[SCOPE_USER])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_agent(
    s: sessionmaker,
    *,
    name: str = "planner",
    archived: bool = False,
    mcp_server_ids: list[UUID] | None = None,
    child_resource_ids: list[UUID] | None = None,
) -> Agent:
    with s() as session:
        a = Agent(
            name=name,
            model="openai:gpt-4o-mini",
            instructions="Be helpful.",
            description=f"agent {name}",
            mcp_server_ids=mcp_server_ids or [],
            child_resource_ids=child_resource_ids or [],
        )
        if archived:
            a.archived_at = datetime.now(tz=timezone.utc)
        session.add(a)
        session.commit()
        session.refresh(a)
        return a


def _seed_mcp_server_with_child(
    s: sessionmaker, *, child_name: str
) -> tuple[UUID, UUID]:
    """Seed a complete type/server/child chain.  Returns ``(server_id, child_id)``.

    The runtime route's ``_resolve_lease_keys_for_agent`` queries the
    real DB via ``aget_parent_map``, so the child resource and its
    parent server need to be real persisted rows even when the cache
    backend is a stub.
    """
    with s() as session:
        type_row = MCPServerType(
            slug=f"swagger-{child_name}",
            name="Swagger Adapter",
            mode="streamable_http",
            default_command=None,
            default_args=[],
            config_schema=[],
            default_env_vars={},
            optional_env_vars={},
        )
        session.add(type_row)
        session.flush()

        server = MCPServer(
            type_id=type_row.id,
            name=f"server-{child_name}",
            env_tag="test",
            args=[],
        )
        session.add(server)
        session.flush()

        child = MCPServerChildResource(
            parent_mcp_server_id=server.id,
            type="swagger",
            name=child_name,
            url=f"https://api.example/{child_name}.json",
            enabled=True,
        )
        session.add(child)
        session.commit()
        session.refresh(server)
        session.refresh(child)
        return server.id, child.id


# ---------------------------------------------------------------------------
# Auth + lookup gating
# ---------------------------------------------------------------------------


def test_run_without_token_returns_401(client: TestClient) -> None:
    r = client.post(f"/v1/agents/{uuid4()}/runs", json={"input": "hi"})
    assert r.status_code == 401


def test_run_missing_agent_returns_404(
    client: TestClient, seeded_user: tuple[UUID, str]
) -> None:
    _, token = seeded_user
    r = client.post(
        f"/v1/agents/{uuid4()}/runs",
        json={"input": "hi"},
        headers=_auth(token),
    )
    assert r.status_code == 404


def test_run_archived_agent_returns_404(
    client: TestClient,
    seeded_user: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    """Archived agents are hidden from users — runs against them must
    404, not silently succeed against the stale config."""
    _, token = seeded_user
    a = _seed_agent(sync_session_maker, archived=True)
    r = client.post(
        f"/v1/agents/{a.id}/runs",
        json={"input": "hi"},
        headers=_auth(token),
    )
    assert r.status_code == 404


def test_run_rejects_unknown_body_fields(
    client: TestClient,
    seeded_user: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    """Schema has ``extra='forbid'`` so a misspelled field surfaces
    immediately instead of getting silently dropped."""
    _, token = seeded_user
    a = _seed_agent(sync_session_maker)
    r = client.post(
        f"/v1/agents/{a.id}/runs",
        json={"input": "hi", "strem": True},  # typo
        headers=_auth(token),
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Non-streaming happy path
# ---------------------------------------------------------------------------


def test_non_streaming_returns_run_output_dict(
    client: TestClient,
    seeded_user: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    user_id, token = seeded_user
    a = _seed_agent(sync_session_maker)

    fake = _FakeAgent()
    fake.set_result(_FakeRunOutput(run_id="run-1", content="hello!"))

    with patch("gargantua.api.runs.build_agno_agent", return_value=fake):
        r = client.post(
            f"/v1/agents/{a.id}/runs",
            json={"input": "say hi", "session_id": "sess-1"},
            headers=_auth(token),
        )

    assert r.status_code == 200, r.text
    assert r.json() == {"run_id": "run-1", "content": "hello!"}

    # Forwarded fields:
    call = fake.arun_calls[0]
    assert call["input"] == "say hi"
    assert call["session_id"] == "sess-1"
    # user_id derived from JWT, not from request body
    assert call["user_id"] == str(user_id)
    assert call["stream"] is False


def test_non_streaming_with_mcp_servers_acquires_and_releases_leases(
    client: TestClient,
    seeded_user: tuple[UUID, str],
    sync_session_maker: sessionmaker,
    backend: _StubBackend,
    cache: MCPCache,
) -> None:
    """When the agent has mcp_server_ids, the route should lease all of
    them, pass them into ``build_agno_agent``, and release them on the
    way out so ref_count returns to zero."""
    _, token = seeded_user
    sid_a, sid_b = uuid4(), uuid4()
    backend.add(sid_a, label="tool-a")
    backend.add(sid_b, label="tool-b")

    agent_row = _seed_agent(
        sync_session_maker, mcp_server_ids=[sid_a, sid_b]
    )

    fake = _FakeAgent()
    fake.set_result(_FakeRunOutput(run_id="r", content="ok"))

    captured_tools = []

    def _capture(*args, **kwargs):
        captured_tools.extend(kwargs.get("tools") or [])
        return fake

    with patch("gargantua.api.runs.build_agno_agent", side_effect=_capture):
        r = client.post(
            f"/v1/agents/{agent_row.id}/runs",
            json={"input": "x"},
            headers=_auth(token),
        )
    assert r.status_code == 200

    # Both servers were leased, both handles forwarded.
    labels = {t.label for t in captured_tools}
    assert labels == {"tool-a", "tool-b"}

    # And after the run, every cache entry's ref_count is back to zero.
    for snap in cache.inspect():
        assert snap.ref_count == 0, (
            f"server {snap.server_id} ref_count still {snap.ref_count}"
        )


def test_non_streaming_lease_failure_returns_503_and_no_leak(
    client: TestClient,
    seeded_user: tuple[UUID, str],
    sync_session_maker: sessionmaker,
    backend: _StubBackend,
    cache: MCPCache,
) -> None:
    """When an MCP server reference is invalid (server deleted,
    archived, KEK mismatch) the route should 503 and any leases that
    *did* get acquired must be released."""
    _, token = seeded_user

    sid_good = uuid4()
    sid_bad = uuid4()  # NOT added to backend -> fetch returns None
    backend.add(sid_good)

    agent_row = _seed_agent(
        sync_session_maker, mcp_server_ids=[sid_good, sid_bad]
    )

    fake = _FakeAgent()
    fake.set_result(_FakeRunOutput(run_id="r", content="x"))

    with patch("gargantua.api.runs.build_agno_agent", return_value=fake):
        r = client.post(
            f"/v1/agents/{agent_row.id}/runs",
            json={"input": "x"},
            headers=_auth(token),
        )

    assert r.status_code == 503
    # Even though sid_good was leased before sid_bad failed, ref_count
    # on it should be back to zero — no leak.
    snaps = {s.server_id: s for s in cache.inspect()}
    if sid_good in snaps:
        assert snaps[sid_good].ref_count == 0


def test_run_propagates_arun_exception_as_500(
    client: TestClient,
    seeded_user: tuple[UUID, str],
    sync_session_maker: sessionmaker,
    cache: MCPCache,
    backend: _StubBackend,
) -> None:
    """If the underlying ``arun`` raises (model returned garbage,
    transport blew up, ...), leases should still be released."""
    _, token = seeded_user
    sid = uuid4()
    backend.add(sid)
    a = _seed_agent(sync_session_maker, mcp_server_ids=[sid])

    class _Boom:
        def arun(self, *args, **kwargs):
            # Real Agno.arun is sync; mirror that so the raise reaches
            # the production code's try/except synchronously.
            raise RuntimeError("model exploded")

    with patch("gargantua.api.runs.build_agno_agent", return_value=_Boom()):
        r = client.post(
            f"/v1/agents/{a.id}/runs",
            json={"input": "x"},
            headers=_auth(token),
        )
    assert r.status_code == 500

    # Lease must be released even on exception.
    for snap in cache.inspect():
        assert snap.ref_count == 0


# ---------------------------------------------------------------------------
# Streaming happy path
# ---------------------------------------------------------------------------


def test_streaming_returns_sse_chunks_and_done_marker(
    client: TestClient,
    seeded_user: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    _, token = seeded_user
    a = _seed_agent(sync_session_maker)

    events = [
        {"event": "start"},
        {"event": "delta", "content": "hello"},
        {"event": "complete"},
    ]

    async def gen():
        for payload in events:
            yield _FakeEvent(payload)

    fake = _FakeAgent()
    fake.set_result(gen)  # callable -> async iterator

    with patch("gargantua.api.runs.build_agno_agent", return_value=fake):
        r = client.post(
            f"/v1/agents/{a.id}/runs",
            json={"input": "stream me", "stream": True},
            headers=_auth(token),
        )

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")

    # SSE chunks: ``data: <json>\n\n`` × N, then ``data: [DONE]\n\n``.
    chunks = [line for line in r.text.split("\n\n") if line.startswith("data: ")]
    assert len(chunks) == len(events) + 1, r.text

    parsed = []
    for chunk in chunks[:-1]:
        payload = chunk.removeprefix("data: ")
        parsed.append(json.loads(payload))
    assert parsed == events
    assert chunks[-1] == "data: [DONE]"

    # And arun was called with stream=True
    assert fake.arun_calls[0]["stream"] is True


def test_streaming_releases_leases_after_stream_completes(
    client: TestClient,
    seeded_user: tuple[UUID, str],
    sync_session_maker: sessionmaker,
    backend: _StubBackend,
    cache: MCPCache,
) -> None:
    """The cache's ref_count is incremented when the lease is acquired
    (before the response starts streaming) and decremented in the
    stream generator's ``finally``.  By the time the test client has
    read the full body, ref_count should be back to zero."""
    _, token = seeded_user
    sid = uuid4()
    backend.add(sid)
    a = _seed_agent(sync_session_maker, mcp_server_ids=[sid])

    async def gen():
        yield _FakeEvent({"x": 1})

    fake = _FakeAgent()
    fake.set_result(gen)

    with patch("gargantua.api.runs.build_agno_agent", return_value=fake):
        r = client.post(
            f"/v1/agents/{a.id}/runs",
            json={"input": "x", "stream": True},
            headers=_auth(token),
        )
        # Force the test client to read the whole body.
        assert r.status_code == 200
        _ = r.text

    for snap in cache.inspect():
        assert snap.ref_count == 0, (
            f"server {snap.server_id} ref_count still {snap.ref_count}"
        )


# ---------------------------------------------------------------------------
# child_resource_ids propagation
# ---------------------------------------------------------------------------


def test_run_with_child_resources_binds_cache_entry_to_child_set(
    client: TestClient,
    seeded_user: tuple[UUID, str],
    sync_session_maker: sessionmaker,
    backend: _StubBackend,
    cache: MCPCache,
) -> None:
    """An agent that references a child resource should produce a cache
    entry keyed by the (server_id, child_set) tuple — not the bare
    server.  This is the whole point of the child-set keying: distinct
    tool surfaces per child set."""
    _, token = seeded_user

    # Seed a real type + server + child so the route's
    # ``aget_parent_map`` returns the right parent.
    server_id, child_id = _seed_mcp_server_with_child(
        sync_session_maker, child_name="petstore"
    )
    backend.add(server_id, label="swagger")

    agent_row = _seed_agent(
        sync_session_maker,
        mcp_server_ids=[server_id],
        child_resource_ids=[child_id],
    )

    fake = _FakeAgent()
    fake.set_result(_FakeRunOutput(run_id="r", content="ok"))

    with patch("gargantua.api.runs.build_agno_agent", return_value=fake):
        r = client.post(
            f"/v1/agents/{agent_row.id}/runs",
            json={"input": "x"},
            headers=_auth(token),
        )
    assert r.status_code == 200, r.text

    # The lease has been released so ref_count is back to 0, but the
    # entry should still be in the snapshot (idle, not yet reaped).
    snaps = [s for s in cache.inspect() if s.server_id == server_id]
    assert len(snaps) == 1
    assert snaps[0].child_resource_ids == [child_id]
    assert snaps[0].ref_count == 0


def test_run_with_child_resources_distinct_from_bare_run(
    client: TestClient,
    seeded_user: tuple[UUID, str],
    sync_session_maker: sessionmaker,
    backend: _StubBackend,
    cache: MCPCache,
) -> None:
    """Two agents that share an MCP server but differ in their child
    set must NOT share a warm handle.  This test runs both back-to-back
    and confirms the cache holds two distinct entries (different child
    sets) for the same server_id."""
    _, token = seeded_user
    server_id, child_id = _seed_mcp_server_with_child(
        sync_session_maker, child_name="orders"
    )
    backend.add(server_id, label="swagger")

    bare_agent = _seed_agent(
        sync_session_maker, name="bare", mcp_server_ids=[server_id]
    )
    filtered_agent = _seed_agent(
        sync_session_maker,
        name="filtered",
        mcp_server_ids=[server_id],
        child_resource_ids=[child_id],
    )

    fake = _FakeAgent()
    fake.set_result(_FakeRunOutput(run_id="r", content="ok"))

    with patch("gargantua.api.runs.build_agno_agent", return_value=fake):
        for agent_row in (bare_agent, filtered_agent):
            r = client.post(
                f"/v1/agents/{agent_row.id}/runs",
                json={"input": "x"},
                headers=_auth(token),
            )
            assert r.status_code == 200, r.text

    snaps = [s for s in cache.inspect() if s.server_id == server_id]
    # Two cache entries for the same server, distinguished by child_set.
    assert len(snaps) == 2
    by_children = {tuple(s.child_resource_ids): s for s in snaps}
    assert () in by_children
    assert (child_id,) in by_children


def test_run_with_orphan_child_resource_silently_drops_it(
    client: TestClient,
    seeded_user: tuple[UUID, str],
    sync_session_maker: sessionmaker,
    backend: _StubBackend,
    cache: MCPCache,
) -> None:
    """If the agent's child_resource_ids contains an ID that doesn't
    exist in the DB (deleted out from under it), the route layer
    drops it with a warning log rather than failing.  The resulting
    cache entry is bare (no children), so the agent runs without the
    missing filter — better than a hard 500 in our judgement, because
    the agent's mcp_server_ids reference is still valid."""
    _, token = seeded_user
    server_id, _real_child = _seed_mcp_server_with_child(
        sync_session_maker, child_name="real"
    )
    backend.add(server_id, label="swagger")

    ghost_child = uuid4()  # not in the DB
    agent_row = _seed_agent(
        sync_session_maker,
        mcp_server_ids=[server_id],
        child_resource_ids=[ghost_child],
    )

    fake = _FakeAgent()
    fake.set_result(_FakeRunOutput(run_id="r", content="ok"))

    with patch("gargantua.api.runs.build_agno_agent", return_value=fake):
        r = client.post(
            f"/v1/agents/{agent_row.id}/runs",
            json={"input": "x"},
            headers=_auth(token),
        )
    assert r.status_code == 200, r.text

    # The ghost child must NOT appear on any cache entry.
    snaps = [s for s in cache.inspect() if s.server_id == server_id]
    assert len(snaps) == 1
    assert snaps[0].child_resource_ids == []

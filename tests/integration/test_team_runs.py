"""Integration tests for ``POST /v1/teams/{team_id}/runs``.

Team runs are agent runs with three extras the route has to get right:

* **Member resolution** — the team's ``member_agent_ids`` are loaded
  to real :class:`Agent` rows; any empty/missing/archived case must
  surface a structured 422 so the admin knows exactly which team is
  broken.
* **Lease deduplication** — multiple members can reference the same
  MCP server.  The route must acquire each unique ``server_id`` only
  once.  The cache's ref-count goes up by 1 (not by N where N is the
  member count), so the post-run snapshot still hits zero on release.
* **Per-member tools slicing** — each member agent gets only the tools
  for *its own* ``mcp_server_ids``, never the union.  We assert this
  by capturing the ``tools=`` kwarg the route passes into each
  ``build_agno_agent`` call.

We mock both ``build_agno_agent`` and ``build_agno_team`` so no real
Agno construction (and therefore no model resolution) happens.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
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

from gargantua.db.models import Agent, Team, User
from gargantua.mcp_cache import BuildPlan, MCPCache

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeClosable:
    """Tools handle stand-in shared between members in lease-dedup tests."""

    def __init__(self, label: str = "tool") -> None:
        self.label = label
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


class _FakeRunOutput:
    def __init__(self, *, run_id: str, content: str, **extra: Any) -> None:
        self._payload = {"run_id": run_id, "content": content, **extra}

    def to_dict(self) -> dict[str, Any]:
        return dict(self._payload)


class _FakeEvent:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def to_dict(self) -> dict[str, Any]:
        return dict(self._payload)


class _FakeRunnable:
    """Stand-in for both ``agno.Agent`` and ``agno.Team`` — both expose
    the same ``arun`` shape, so one stub fits both."""

    def __init__(self) -> None:
        self.arun_calls: list[dict[str, Any]] = []
        self._result: Any = None
        # populated by the patched builders so tests can correlate which
        # member got which tools / which db
        self.row_id: UUID | None = None
        self.tools_passed: list[Any] | None = None
        self.members_passed: list[Any] | None = None
        self.db_passed: Any = None

    def set_result(self, result: Any) -> None:
        self._result = result

    def arun(self, input: Any, **kwargs: Any) -> Any:
        # Real ``agno.agent.Agent.arun`` / ``agno.team.Team.arun`` are
        # sync methods returning either an AsyncIterator (stream=True)
        # or a coroutine resolving to a RunOutput (stream=False).
        self.arun_calls.append({"input": input, **kwargs})
        if kwargs.get("stream"):
            return self._result() if callable(self._result) else self._result
        return self._await_result()

    async def _await_result(self) -> Any:
        return self._result


# ---------------------------------------------------------------------------
# Fixtures (mirror test_agent_runs.py)
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
    truncate_db: Engine,
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
    """Tiny row-fetcher: server_id -> (version, shared closable handle)."""

    def __init__(self) -> None:
        self.servers: dict[UUID, tuple[int, _FakeClosable]] = {}

    def add(self, sid: UUID, *, label: str = "tool") -> _FakeClosable:
        handle = _FakeClosable(label=label)
        self.servers[sid] = (1, handle)
        return handle

    async def fetch(
        self,
        sid: UUID,
        child_resource_ids: tuple[UUID, ...] = (),
    ) -> BuildPlan | None:
        # Child resources ignored in this stub.
        del child_resource_ids
        if sid not in self.servers:
            return None
        version, handle = self.servers[sid]

        async def factory() -> _FakeClosable:
            return handle

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
def app(configured_env, cache: MCPCache) -> FastAPI:
    from gargantua.api.auth import router as auth_router
    from gargantua.api.runs import router as runs_router

    a = FastAPI()
    a.include_router(auth_router, prefix="/auth")
    a.include_router(runs_router, prefix="/v1")
    a.state.mcp_cache = cache
    a.state.agno_db = None
    return a


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
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
    name: str,
    archived: bool = False,
    mcp_server_ids: list[UUID] | None = None,
) -> Agent:
    with s() as session:
        a = Agent(
            name=name,
            model="openai:gpt-4o-mini",
            instructions="Be helpful.",
            description=f"agent {name}",
            mcp_server_ids=mcp_server_ids or [],
        )
        if archived:
            a.archived_at = datetime.now(tz=UTC)
        session.add(a)
        session.commit()
        session.refresh(a)
        return a


def _seed_team(
    s: sessionmaker,
    *,
    name: str,
    mode: str = "route",
    member_agent_ids: list[UUID] | None = None,
    archived: bool = False,
) -> Team:
    with s() as session:
        t = Team(
            name=name,
            mode=mode,
            description=f"team {name}",
            member_agent_ids=member_agent_ids or [],
        )
        if archived:
            t.archived_at = datetime.now(tz=UTC)
        session.add(t)
        session.commit()
        session.refresh(t)
        return t


# ---------------------------------------------------------------------------
# Builder patches — one set for the whole module, configurable per test
# ---------------------------------------------------------------------------


def _patched_builders(
    *,
    agent_factory=None,
    team_factory=None,
):
    """Patch context that captures the args passed into the registry
    builders for the duration of a test.

    ``agent_factory`` / ``team_factory`` are callables that produce a
    :class:`_FakeRunnable` for each construction.  The route's
    ``build_agno_agent(row, *, tools=, db=)`` and
    ``build_agno_team(row, *, members=, model=, db=)`` call signatures
    are normalized into the returned objects so tests can assert.
    """

    captured_agents: list[_FakeRunnable] = []
    captured_teams: list[_FakeRunnable] = []

    # NB. ``debug`` mirrors the kwarg the route now forwards from
    # ``Settings.agno_debug``.  Captured on the fake so tests can
    # assert it round-trips correctly when needed.
    def default_agent(row, *, tools=None, db=None, debug=False):
        a = agent_factory() if agent_factory else _FakeRunnable()
        a.row_id = row.id
        a.tools_passed = list(tools) if tools else []
        a.db_passed = db
        a.debug_passed = debug
        captured_agents.append(a)
        return a

    def default_team(row, *, members, model=None, db=None, debug=False):
        t = team_factory() if team_factory else _FakeRunnable()
        t.row_id = row.id
        t.members_passed = list(members)
        t.db_passed = db
        t.debug_passed = debug
        captured_teams.append(t)
        return t

    agent_patch = patch("gargantua.api.runs.build_agno_agent", side_effect=default_agent)
    team_patch = patch("gargantua.api.runs.build_agno_team", side_effect=default_team)
    return agent_patch, team_patch, captured_agents, captured_teams


# ---------------------------------------------------------------------------
# Auth + lookup + member-validation gating
# ---------------------------------------------------------------------------


def test_run_team_without_token_returns_401(client: TestClient) -> None:
    r = client.post(f"/v1/teams/{uuid4()}/runs", json={"input": "hi"})
    assert r.status_code == 401


def test_run_missing_team_returns_404(client: TestClient, seeded_user: tuple[UUID, str]) -> None:
    _, token = seeded_user
    r = client.post(
        f"/v1/teams/{uuid4()}/runs",
        json={"input": "hi"},
        headers=_auth(token),
    )
    assert r.status_code == 404


def test_run_archived_team_returns_404(
    client: TestClient,
    seeded_user: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    _, token = seeded_user
    t = _seed_team(sync_session_maker, name="dead", archived=True)
    r = client.post(
        f"/v1/teams/{t.id}/runs",
        json={"input": "hi"},
        headers=_auth(token),
    )
    assert r.status_code == 404


def test_run_team_with_no_members_returns_422(
    client: TestClient,
    seeded_user: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    """A team with zero members is technically allowed by the schema
    (the column defaults to ``[]``) but running it has no semantics.
    Surface 422 so the admin knows to add members."""
    _, token = seeded_user
    t = _seed_team(sync_session_maker, name="empty", member_agent_ids=[])
    r = client.post(
        f"/v1/teams/{t.id}/runs",
        json={"input": "hi"},
        headers=_auth(token),
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["reason"] == "team_has_no_members"


def test_run_team_with_missing_member_returns_422(
    client: TestClient,
    seeded_user: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    """The team references an agent id that doesn't exist (the agent
    row was deleted out from under it).  Should be 422 with the
    specific bad ids in the response so the admin can repair the team."""
    _, token = seeded_user
    real = _seed_agent(sync_session_maker, name="alive")
    ghost = uuid4()
    t = _seed_team(sync_session_maker, name="t", member_agent_ids=[real.id, ghost])
    r = client.post(
        f"/v1/teams/{t.id}/runs",
        json={"input": "hi"},
        headers=_auth(token),
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["reason"] == "members_invalid"
    assert str(ghost) in detail["missing"]
    assert str(real.id) not in detail.get("missing", [])


def test_run_team_with_archived_member_returns_422(
    client: TestClient,
    seeded_user: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    """Members that are archived count as invalid for the run.
    Otherwise the team would silently degrade behaviour."""
    _, token = seeded_user
    healthy = _seed_agent(sync_session_maker, name="ok")
    retired = _seed_agent(sync_session_maker, name="retired", archived=True)
    t = _seed_team(
        sync_session_maker,
        name="t",
        member_agent_ids=[healthy.id, retired.id],
    )
    r = client.post(
        f"/v1/teams/{t.id}/runs",
        json={"input": "hi"},
        headers=_auth(token),
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["reason"] == "members_invalid"
    assert str(retired.id) in detail["archived"]


def test_run_rejects_unknown_body_fields_team(
    client: TestClient,
    seeded_user: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    _, token = seeded_user
    m = _seed_agent(sync_session_maker, name="m")
    t = _seed_team(sync_session_maker, name="t", member_agent_ids=[m.id])
    r = client.post(
        f"/v1/teams/{t.id}/runs",
        json={"input": "hi", "strem": True},  # typo
        headers=_auth(token),
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Non-streaming happy path + tools slicing
# ---------------------------------------------------------------------------


def test_non_streaming_team_returns_run_output_dict(
    client: TestClient,
    seeded_user: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    user_id, token = seeded_user
    m1 = _seed_agent(sync_session_maker, name="m1")
    m2 = _seed_agent(sync_session_maker, name="m2")
    t = _seed_team(sync_session_maker, name="t", mode="coordinate", member_agent_ids=[m1.id, m2.id])

    def _team_with_result():
        team = _FakeRunnable()
        team.set_result(_FakeRunOutput(run_id="r1", content="team output"))
        return team

    agent_patch, team_patch, captured_agents, captured_teams = _patched_builders(
        team_factory=_team_with_result
    )
    with agent_patch, team_patch:
        r = client.post(
            f"/v1/teams/{t.id}/runs",
            json={"input": "go", "session_id": "sess-team"},
            headers=_auth(token),
        )

    assert r.status_code == 200, r.text
    assert r.json() == {"run_id": "r1", "content": "team output"}

    # Exactly one Team built; two Agents built (one per member).
    assert len(captured_teams) == 1
    assert len(captured_agents) == 2

    # The Team's arun was called with the user_id derived from JWT.
    call = captured_teams[0].arun_calls[0]
    assert call["input"] == "go"
    assert call["session_id"] == "sess-team"
    assert call["user_id"] == str(user_id)
    assert call["stream"] is False

    # Member order matches the order in member_agent_ids.
    captured_ids = [a.row_id for a in captured_agents]
    assert captured_ids == [m1.id, m2.id]


def test_team_tools_are_sliced_per_member(
    client: TestClient,
    seeded_user: tuple[UUID, str],
    sync_session_maker: sessionmaker,
    backend: _StubBackend,
) -> None:
    """Each member agent must receive ONLY its own MCP tools, never the
    union across the whole team."""
    _, token = seeded_user
    sid_a, sid_b, sid_c = uuid4(), uuid4(), uuid4()
    handle_a = backend.add(sid_a, label="A")
    handle_b = backend.add(sid_b, label="B")
    handle_c = backend.add(sid_c, label="C")

    m_alpha = _seed_agent(sync_session_maker, name="alpha", mcp_server_ids=[sid_a, sid_b])
    m_beta = _seed_agent(sync_session_maker, name="beta", mcp_server_ids=[sid_c])
    t = _seed_team(
        sync_session_maker,
        name="t",
        member_agent_ids=[m_alpha.id, m_beta.id],
    )

    def _team_with_result():
        team = _FakeRunnable()
        team.set_result(_FakeRunOutput(run_id="r", content="ok"))
        return team

    agent_patch, team_patch, captured_agents, _ = _patched_builders(team_factory=_team_with_result)
    with agent_patch, team_patch:
        r = client.post(
            f"/v1/teams/{t.id}/runs",
            json={"input": "x"},
            headers=_auth(token),
        )
    assert r.status_code == 200

    by_id = {a.row_id: a for a in captured_agents}
    alpha_labels = {t.label for t in by_id[m_alpha.id].tools_passed}
    beta_labels = {t.label for t in by_id[m_beta.id].tools_passed}

    assert alpha_labels == {"A", "B"}
    assert beta_labels == {"C"}
    # Negative: alpha must NOT have got beta's tool, etc.
    assert handle_c not in by_id[m_alpha.id].tools_passed
    assert handle_a not in by_id[m_beta.id].tools_passed
    assert handle_b not in by_id[m_beta.id].tools_passed


def test_team_lease_dedup_across_members(
    client: TestClient,
    seeded_user: tuple[UUID, str],
    sync_session_maker: sessionmaker,
    backend: _StubBackend,
    cache: MCPCache,
) -> None:
    """If two members share an MCP server, the cache is acquired ONCE
    (ref_count goes from 0 -> 1 -> 0, not 0 -> 2 -> 0).  Both members
    receive the same warm handle in their tools list."""
    _, token = seeded_user
    sid_shared = uuid4()
    handle = backend.add(sid_shared, label="shared")

    m1 = _seed_agent(sync_session_maker, name="m1", mcp_server_ids=[sid_shared])
    m2 = _seed_agent(sync_session_maker, name="m2", mcp_server_ids=[sid_shared])
    t = _seed_team(sync_session_maker, name="t", member_agent_ids=[m1.id, m2.id])

    def _team_with_result():
        team = _FakeRunnable()
        team.set_result(_FakeRunOutput(run_id="r", content="ok"))
        return team

    agent_patch, team_patch, captured_agents, _ = _patched_builders(team_factory=_team_with_result)
    with agent_patch, team_patch:
        r = client.post(
            f"/v1/teams/{t.id}/runs",
            json={"input": "x"},
            headers=_auth(token),
        )
    assert r.status_code == 200, r.text

    # Both members got the SAME handle (the dedup is the whole point).
    assert captured_agents[0].tools_passed == [handle]
    assert captured_agents[1].tools_passed == [handle]

    # Cache ref_count is back to zero — only one lease was taken out,
    # only one release was needed.
    for snap in cache.inspect():
        assert snap.ref_count == 0

    # And the underlying handle hasn't been closed (still warm).
    assert handle.close_calls == 0


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_team_lease_failure_returns_503_no_leak(
    client: TestClient,
    seeded_user: tuple[UUID, str],
    sync_session_maker: sessionmaker,
    backend: _StubBackend,
    cache: MCPCache,
) -> None:
    """One of the members references an MCP server that isn't in the
    catalog (deleted out from under us).  The route should 503 and any
    leases it *did* acquire should be released."""
    _, token = seeded_user
    sid_ok, sid_bad = uuid4(), uuid4()
    backend.add(sid_ok)

    m1 = _seed_agent(sync_session_maker, name="m1", mcp_server_ids=[sid_ok])
    m2 = _seed_agent(sync_session_maker, name="m2", mcp_server_ids=[sid_bad])
    t = _seed_team(sync_session_maker, name="t", member_agent_ids=[m1.id, m2.id])

    agent_patch, team_patch, _, _ = _patched_builders()
    with agent_patch, team_patch:
        r = client.post(
            f"/v1/teams/{t.id}/runs",
            json={"input": "x"},
            headers=_auth(token),
        )
    assert r.status_code == 503

    # ref_count must be 0 on every entry that exists.
    for snap in cache.inspect():
        assert snap.ref_count == 0


def test_team_arun_exception_propagates_as_500_with_release(
    client: TestClient,
    seeded_user: tuple[UUID, str],
    sync_session_maker: sessionmaker,
    backend: _StubBackend,
    cache: MCPCache,
) -> None:
    _, token = seeded_user
    sid = uuid4()
    backend.add(sid)
    m = _seed_agent(sync_session_maker, name="m", mcp_server_ids=[sid])
    t = _seed_team(sync_session_maker, name="t", member_agent_ids=[m.id])

    class _BoomTeam(_FakeRunnable):
        def arun(self, *args, **kwargs):  # type: ignore[override]
            # Real Agno.arun is sync; mirror that so the raise reaches
            # the production code's try/except synchronously.
            raise RuntimeError("coordinator exploded")

    agent_patch, team_patch, _, _ = _patched_builders(
        team_factory=_BoomTeam,
    )
    with agent_patch, team_patch:
        r = client.post(
            f"/v1/teams/{t.id}/runs",
            json={"input": "x"},
            headers=_auth(token),
        )
    assert r.status_code == 500
    for snap in cache.inspect():
        assert snap.ref_count == 0


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


def test_streaming_team_returns_sse_chunks_and_done_marker(
    client: TestClient,
    seeded_user: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    _, token = seeded_user
    m = _seed_agent(sync_session_maker, name="m")
    t = _seed_team(sync_session_maker, name="t", member_agent_ids=[m.id])

    events = [
        {"event": "team-start"},
        {"event": "delegate", "to": "m"},
        {"event": "complete"},
    ]

    async def gen():
        for payload in events:
            yield _FakeEvent(payload)

    def _team_streaming():
        team = _FakeRunnable()
        team.set_result(gen)
        return team

    agent_patch, team_patch, _, _ = _patched_builders(team_factory=_team_streaming)
    with agent_patch, team_patch:
        r = client.post(
            f"/v1/teams/{t.id}/runs",
            json={"input": "x", "stream": True},
            headers=_auth(token),
        )

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")

    chunks = [line for line in r.text.split("\n\n") if line.startswith("data: ")]
    assert len(chunks) == len(events) + 1
    parsed = [json.loads(c.removeprefix("data: ")) for c in chunks[:-1]]
    assert parsed == events
    assert chunks[-1] == "data: [DONE]"


def test_streaming_team_releases_leases_after_stream_completes(
    client: TestClient,
    seeded_user: tuple[UUID, str],
    sync_session_maker: sessionmaker,
    backend: _StubBackend,
    cache: MCPCache,
) -> None:
    _, token = seeded_user
    sid = uuid4()
    backend.add(sid)
    m = _seed_agent(sync_session_maker, name="m", mcp_server_ids=[sid])
    t = _seed_team(sync_session_maker, name="t", member_agent_ids=[m.id])

    async def gen():
        yield _FakeEvent({"x": 1})

    def _team_streaming():
        team = _FakeRunnable()
        team.set_result(gen)
        return team

    agent_patch, team_patch, _, _ = _patched_builders(team_factory=_team_streaming)
    with agent_patch, team_patch:
        r = client.post(
            f"/v1/teams/{t.id}/runs",
            json={"input": "x", "stream": True},
            headers=_auth(token),
        )
        assert r.status_code == 200
        _ = r.text

    for snap in cache.inspect():
        assert snap.ref_count == 0

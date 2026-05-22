"""Integration tests for ``/admin/mcp-cache``.

The cache is a process-wide singleton attached to ``app.state.mcp_cache``
during lifespan startup.  These tests build a minimal app, inject a
test-double cache pre-seeded with known entries, and exercise the
admin surface end-to-end:

* Auth gating (401 / 403 / 200).
* ``GET /admin/mcp-cache`` reflects the cache's inspect snapshot,
  including orphans.
* ``POST /admin/mcp-cache/{server_id}/evict`` calls into the cache,
  writes an audit row, and 404s when the server isn't cached.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from gargantua.db.models import AuditLog, User
from gargantua.mcp_cache import BuildPlan, MCPCache

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeTools:
    """Minimal Closeable used to populate the cache from the test side."""

    def __init__(self) -> None:
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


class _StubBackend:
    """Fake row fetcher we use to drive cache state.

    Acts like a tiny in-memory catalog: tests call ``add`` to register
    a server, then hit ``acquire`` via the admin route's underlying
    cache (or directly in the fixture setup).
    """

    def __init__(self) -> None:
        self._rows: dict[UUID, tuple[int, _FakeTools]] = {}

    def add(self, server_id: UUID, *, version: int = 1) -> _FakeTools:
        tools = _FakeTools()
        self._rows[server_id] = (version, tools)
        return tools

    async def fetch(
        self,
        server_id: UUID,
        child_resource_ids: tuple[UUID, ...] = (),
    ) -> BuildPlan | None:
        # Child resources are ignored in this stub — the admin cache
        # routes don't care which child set an entry is bound to,
        # only that the snapshot surfaces it.
        del child_resource_ids
        if server_id not in self._rows:
            return None
        version, tools = self._rows[server_id]

        async def factory() -> _FakeTools:
            return tools

        return BuildPlan(version=version, factory=factory)


# ---------------------------------------------------------------------------
# Fixtures (mirror other admin route tests)
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


@pytest.fixture
def backend() -> _StubBackend:
    return _StubBackend()


@pytest.fixture
async def cache(backend: _StubBackend) -> AsyncIterator[MCPCache]:
    """A cache instance with a generous TTL so the reaper doesn't touch
    our seeded entries mid-test."""
    c = MCPCache(
        row_fetcher=backend.fetch,
        idle_ttl=timedelta(hours=1),
        reap_interval=timedelta(hours=1),
    )
    yield c
    await c.stop()


@pytest.fixture
def app(configured_env, cache: MCPCache) -> FastAPI:
    from gargantua.api.admin import router as admin_router
    from gargantua.api.auth import router as auth_router

    a = FastAPI()
    a.include_router(auth_router, prefix="/auth")
    a.include_router(admin_router, prefix="/admin")
    # Routes resolve the cache via ``request.app.state.mcp_cache``; we
    # inject the test double here so we don't need a full lifespan.
    a.state.mcp_cache = cache
    return a


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def sync_session_maker(migrated_engine: Engine) -> sessionmaker:
    return sessionmaker(bind=migrated_engine, expire_on_commit=False, future=True)


@pytest.fixture
def seeded_admin(sync_session_maker) -> tuple[UUID, str]:
    from gargantua.auth import SCOPE_ADMIN, SCOPE_USER, mint_access_token
    from gargantua.auth.password import hash_password

    with sync_session_maker() as s:
        u = User(
            username="root",
            password_hash=hash_password("rootpw!1"),
            role="admin",
        )
        s.add(u)
        s.commit()
        s.refresh(u)
        return u.id, mint_access_token(subject=str(u.id), scopes=[SCOPE_ADMIN, SCOPE_USER])


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


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------


def test_get_cache_without_token_returns_401(client: TestClient) -> None:
    r = client.get("/admin/mcp-cache")
    assert r.status_code == 401


def test_get_cache_with_user_token_returns_403(
    client: TestClient, seeded_user: tuple[UUID, str]
) -> None:
    _, token = seeded_user
    r = client.get("/admin/mcp-cache", headers=_auth(token))
    assert r.status_code == 403


def test_get_cache_with_admin_token_returns_empty(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    r = client.get("/admin/mcp-cache", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body == {"items": [], "total": 0}


# ---------------------------------------------------------------------------
# GET /admin/mcp-cache
# ---------------------------------------------------------------------------


async def test_get_cache_reflects_inspect(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    cache: MCPCache,
    backend: _StubBackend,
) -> None:
    _, token = seeded_admin
    sid = uuid4()
    backend.add(sid, version=3)

    lease = await cache.acquire(sid)
    try:
        r = client.get("/admin/mcp-cache", headers=_auth(token))
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["server_id"] == str(sid)
        assert item["version"] == 3
        assert item["ref_count"] == 1
        assert item["is_orphan"] is False
        # last_used should be ISO-8601.
        datetime.fromisoformat(item["last_used"])
    finally:
        await lease.release()


async def test_get_cache_includes_orphans(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    cache: MCPCache,
    backend: _StubBackend,
) -> None:
    """An in-flight lease that survives a version bump should be visible
    as an orphan in the admin view."""
    _, token = seeded_admin
    sid = uuid4()
    backend.add(sid, version=1)

    lease_old = await cache.acquire(sid)
    backend._rows[sid] = (2, backend.add(sid, version=2))  # bump
    lease_new = await cache.acquire(sid)
    try:
        body = client.get("/admin/mcp-cache", headers=_auth(token)).json()
        assert body["total"] == 2
        by_version = {item["version"]: item for item in body["items"]}
        assert by_version[1]["is_orphan"] is True
        assert by_version[2]["is_orphan"] is False
    finally:
        await lease_old.release()
        await lease_new.release()


# ---------------------------------------------------------------------------
# POST /admin/mcp-cache/{server_id}/evict
# ---------------------------------------------------------------------------


async def test_evict_closes_entry_and_audits(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    sync_session_maker: sessionmaker,
    cache: MCPCache,
    backend: _StubBackend,
) -> None:
    admin_id, token = seeded_admin
    sid = uuid4()
    tools = backend.add(sid)
    lease = await cache.acquire(sid)
    await lease.release()

    r = client.post(f"/admin/mcp-cache/{sid}/evict", headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["evicted"] is True

    # Entry gone from cache; handle closed exactly once.
    assert cache.inspect() == []
    assert tools.close_calls == 1

    # Audit row written.
    with sync_session_maker() as s:
        audit = s.execute(
            select(AuditLog)
            .where(AuditLog.action == "mcp_cache.evict")
            .where(AuditLog.target_id == sid)
        ).scalar_one()
    assert audit.actor_id == admin_id
    assert audit.target_type == "mcp_server"


async def test_evict_returns_404_when_not_cached(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    """Eviction of a server that isn't warm in the cache is a 404 — the
    operator should know nothing happened (and no audit row should
    appear, to avoid noise)."""
    _, token = seeded_admin
    r = client.post(f"/admin/mcp-cache/{uuid4()}/evict", headers=_auth(token))
    assert r.status_code == 404

    with sync_session_maker() as s:
        rows = s.execute(select(AuditLog).where(AuditLog.action == "mcp_cache.evict")).all()
    assert rows == []


def test_evict_without_token_returns_401(client: TestClient) -> None:
    r = client.post(f"/admin/mcp-cache/{uuid4()}/evict")
    assert r.status_code == 401


def test_evict_with_user_token_returns_403(
    client: TestClient, seeded_user: tuple[UUID, str]
) -> None:
    _, token = seeded_user
    r = client.post(f"/admin/mcp-cache/{uuid4()}/evict", headers=_auth(token))
    assert r.status_code == 403

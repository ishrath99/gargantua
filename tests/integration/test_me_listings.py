"""Integration tests for ``/me/agents`` and ``/me/teams``.

These are the user-facing "what can I run" listings — gated by
``SCOPE_USER`` (which admins also have).  The contract:

* Returns only **non-archived** rows; archived agents/teams are
  invisible to users.
* Excludes admin-only fields (``tools_config``, ``agent_config``,
  ``created_by``, timestamps); the response shape is
  :class:`MeAgentOut` / :class:`MeTeamOut`.
* Not paginated — the catalog is small enough to return in one shot.

The tests follow the same fixture pattern as ``test_admin_agents.py``
(minimal app + seeded admin/user + truncated DB).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from gargantua.db.models import Agent, Team, User

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
def app(configured_env) -> FastAPI:
    from gargantua.api.auth import router as auth_router
    from gargantua.api.me import router as me_router

    a = FastAPI()
    a.include_router(auth_router, prefix="/api/auth")
    a.include_router(me_router, prefix="/api/me")
    return a


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
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


@pytest.fixture
def seeded_admin(sync_session_maker) -> tuple[UUID, str]:
    from gargantua.auth import SCOPE_ADMIN, SCOPE_USER, mint_access_token
    from gargantua.auth.password import hash_password

    with sync_session_maker() as s:
        u = User(username="root", password_hash=hash_password("rootpw!1"), role="admin")
        s.add(u)
        s.commit()
        s.refresh(u)
        return u.id, mint_access_token(subject=str(u.id), scopes=[SCOPE_ADMIN, SCOPE_USER])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_agent(
    s: sessionmaker, *, name: str, archived: bool = False, mcp_server_ids: list[UUID] | None = None
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
# Auth gating
# ---------------------------------------------------------------------------


def test_me_agents_without_token_returns_401(client: TestClient) -> None:
    r = client.get("/api/me/agents")
    assert r.status_code == 401


def test_me_teams_without_token_returns_401(client: TestClient) -> None:
    r = client.get("/api/me/teams")
    assert r.status_code == 401


def test_me_agents_with_user_token_returns_200(
    client: TestClient, seeded_user: tuple[UUID, str]
) -> None:
    _, token = seeded_user
    r = client.get("/api/me/agents", headers=_auth(token))
    assert r.status_code == 200
    assert r.json() == {"items": [], "total": 0}


def test_me_agents_with_admin_token_also_returns_200(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    """Admins can hit the user-facing endpoint too (admin scope implies
    user access)."""
    _, token = seeded_admin
    r = client.get("/api/me/agents", headers=_auth(token))
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# GET /me/agents — content
# ---------------------------------------------------------------------------


def test_me_agents_returns_only_non_archived(
    client: TestClient,
    seeded_user: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    """Archived agents are invisible to users — admins archive them
    precisely to hide them from runners."""
    _, token = seeded_user
    active = _seed_agent(sync_session_maker, name="active")
    _seed_agent(sync_session_maker, name="archived", archived=True)

    r = client.get("/api/me/agents", headers=_auth(token))
    body = r.json()
    assert body["total"] == 1
    ids = [item["id"] for item in body["items"]]
    assert ids == [str(active.id)]


def test_me_agents_response_shape_excludes_admin_fields(
    client: TestClient,
    seeded_user: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    """The user-facing projection must omit admin-only fields so the
    chat UI can't accidentally surface ``tools_config`` /
    ``agent_config`` / ``created_by`` etc."""
    _, token = seeded_user
    server_ids = [uuid4(), uuid4()]
    _seed_agent(sync_session_maker, name="planner", mcp_server_ids=server_ids)

    r = client.get("/api/me/agents", headers=_auth(token))
    item = r.json()["items"][0]

    # Present:
    assert item["name"] == "planner"
    assert item["description"] == "agent planner"
    assert item["model"] == "openai:gpt-4o-mini"
    assert set(item["mcp_server_ids"]) == {str(sid) for sid in server_ids}

    # Absent (these are admin-only):
    for forbidden in (
        "tools_config",
        "agent_config",
        "child_resource_ids",
        "created_by",
        "created_at",
        "updated_at",
        "archived_at",
        "instructions",  # too much surface for a picker view
    ):
        assert forbidden not in item, f"{forbidden!r} leaked into MeAgentOut"


def test_me_agents_returns_multiple_in_stable_order(
    client: TestClient,
    seeded_user: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    """Several agents -> list of N entries; order should be stable
    across calls (the repo's list_agents already orders by name)."""
    _, token = seeded_user
    for name in ("charlie", "alpha", "bravo"):
        _seed_agent(sync_session_maker, name=name)

    r1 = client.get("/api/me/agents", headers=_auth(token))
    r2 = client.get("/api/me/agents", headers=_auth(token))
    names1 = [item["name"] for item in r1.json()["items"]]
    names2 = [item["name"] for item in r2.json()["items"]]
    assert names1 == names2
    assert sorted(names1) == names1
    assert r1.json()["total"] == 3


# ---------------------------------------------------------------------------
# GET /me/teams — content
# ---------------------------------------------------------------------------


def test_me_teams_returns_only_non_archived(
    client: TestClient,
    seeded_user: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    _, token = seeded_user
    member = _seed_agent(sync_session_maker, name="m1")
    active = _seed_team(sync_session_maker, name="active-team", member_agent_ids=[member.id])
    _seed_team(sync_session_maker, name="archived-team", archived=True)

    r = client.get("/api/me/teams", headers=_auth(token))
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == str(active.id)


def test_me_teams_response_shape_excludes_admin_fields(
    client: TestClient,
    seeded_user: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    _, token = seeded_user
    member = _seed_agent(sync_session_maker, name="m1")
    _seed_team(
        sync_session_maker,
        name="ops",
        mode="coordinate",
        member_agent_ids=[member.id],
    )

    r = client.get("/api/me/teams", headers=_auth(token))
    item = r.json()["items"][0]

    assert item["name"] == "ops"
    assert item["mode"] == "coordinate"
    assert item["description"] == "team ops"
    assert item["member_agent_ids"] == [str(member.id)]

    for forbidden in (
        "team_config",
        "created_by",
        "created_at",
        "updated_at",
        "archived_at",
    ):
        assert forbidden not in item, f"{forbidden!r} leaked into MeTeamOut"


def test_me_teams_supports_all_three_modes(
    client: TestClient,
    seeded_user: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    """A team in any of the three modes should be listable."""
    _, token = seeded_user
    for mode in ("route", "coordinate", "collaborate"):
        _seed_team(sync_session_maker, name=f"t-{mode}", mode=mode)

    body = client.get("/api/me/teams", headers=_auth(token)).json()
    modes = {item["mode"] for item in body["items"]}
    assert modes == {"route", "coordinate", "collaborate"}

"""Integration tests for ``/admin/teams/*``.

Mirrors :mod:`test_admin_agents`: a minimal app, an admin token, and
end-to-end coverage of every route's auth gate, happy path, sad path,
and audit-row side effects.

Reference validation is narrower than agents — teams only point at
``agent`` rows — but mode validation and the mode-filter on list are
exercised here in addition to the CRUD surface.
"""

from __future__ import annotations

from collections.abc import Iterator
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

from gargantua.db.models import Agent, AuditLog, User

# ---------------------------------------------------------------------------
# Fixtures (mirror test_admin_agents.py)
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
    from gargantua.api.admin import router as admin_router
    from gargantua.api.auth import router as auth_router

    a = FastAPI()
    a.include_router(auth_router, prefix="/api/auth")
    a.include_router(admin_router, prefix="/api/admin")
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


@pytest.fixture
def seeded_agents(sync_session_maker) -> dict[str, UUID]:
    """Seed two agents so member-validation tests have real IDs."""
    with sync_session_maker() as s:
        a1 = Agent(name="researcher", model="gpt-5", instructions="i")
        a2 = Agent(name="planner", model="gpt-5", instructions="i")
        s.add_all([a1, a2])
        s.commit()
        s.refresh(a1)
        s.refresh(a2)
        return {"researcher": a1.id, "planner": a2.id}


def _minimal_create_body(name: str = "ops", mode: str = "route") -> dict:
    return {"name": name, "mode": mode}


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------


def test_list_teams_without_token_returns_401(client: TestClient) -> None:
    r = client.get("/api/admin/teams")
    assert r.status_code == 401


def test_list_teams_with_user_token_returns_403(
    client: TestClient, seeded_user: tuple[UUID, str]
) -> None:
    _, token = seeded_user
    r = client.get("/api/admin/teams", headers=_auth(token))
    assert r.status_code == 403


def test_list_teams_with_admin_token_returns_200(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    r = client.get("/api/admin/teams", headers=_auth(token))
    assert r.status_code == 200
    assert r.json() == {"items": [], "total": 0, "page": 1, "page_size": 50}


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_team_201_and_audit_logged(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    admin_id, token = seeded_admin
    r = client.post(
        "/api/admin/teams",
        json=_minimal_create_body(),
        headers=_auth(token),
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["name"] == "ops"
    assert created["mode"] == "route"
    assert created["member_agent_ids"] == []
    assert created["version"] == 1

    with sync_session_maker() as s:
        audit = s.execute(
            select(AuditLog)
            .where(AuditLog.action == "team.create")
            .where(AuditLog.target_id == UUID(created["id"]))
        ).scalar_one()
    assert audit.actor_id == admin_id
    assert audit.before is None
    assert audit.after["name"] == "ops"


def test_create_team_with_members(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    seeded_agents: dict[str, UUID],
) -> None:
    _, token = seeded_admin
    body = _minimal_create_body(mode="coordinate")
    body["member_agent_ids"] = [
        str(seeded_agents["researcher"]),
        str(seeded_agents["planner"]),
    ]
    r = client.post("/api/admin/teams", json=body, headers=_auth(token))
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["member_agent_ids"] == body["member_agent_ids"]


def test_create_team_rejects_duplicate_name(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    body = _minimal_create_body()
    r1 = client.post("/api/admin/teams", json=body, headers=_auth(token))
    assert r1.status_code == 201
    r2 = client.post("/api/admin/teams", json=body, headers=_auth(token))
    assert r2.status_code == 409


def test_create_team_rejects_unknown_mode(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    r = client.post(
        "/api/admin/teams",
        json={"name": "ops", "mode": "freeform"},
        headers=_auth(token),
    )
    assert r.status_code == 422


def test_create_team_rejects_missing_member(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    body = _minimal_create_body()
    body["member_agent_ids"] = [str(uuid4())]
    r = client.post("/api/admin/teams", json=body, headers=_auth(token))
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["missing_agent_ids"] == body["member_agent_ids"]


# ---------------------------------------------------------------------------
# List + get
# ---------------------------------------------------------------------------


def _seed_three_teams(client: TestClient, token: str) -> None:
    for name, mode in [
        ("alpha", "route"),
        ("beta", "coordinate"),
        ("betatron", "route"),
    ]:
        client.post(
            "/api/admin/teams",
            json={"name": name, "mode": mode},
            headers=_auth(token),
        )


def test_list_teams_paginates(client: TestClient, seeded_admin: tuple[UUID, str]) -> None:
    _, token = seeded_admin
    _seed_three_teams(client, token)
    r = client.get("/api/admin/teams?page=1&page_size=2", headers=_auth(token))
    body = r.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2


def test_list_teams_search_matches_substring(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    _seed_three_teams(client, token)
    r = client.get("/api/admin/teams?search=beta", headers=_auth(token))
    body = r.json()
    assert body["total"] == 2
    assert {item["name"] for item in body["items"]} == {"beta", "betatron"}


def test_list_teams_mode_filter(client: TestClient, seeded_admin: tuple[UUID, str]) -> None:
    _, token = seeded_admin
    _seed_three_teams(client, token)
    r = client.get("/api/admin/teams?mode=route", headers=_auth(token))
    body = r.json()
    assert body["total"] == 2
    assert {item["name"] for item in body["items"]} == {"alpha", "betatron"}


def test_list_teams_unknown_mode_filter_422(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    r = client.get("/api/admin/teams?mode=freeform", headers=_auth(token))
    assert r.status_code == 422


def test_get_team_by_id(client: TestClient, seeded_admin: tuple[UUID, str]) -> None:
    _, token = seeded_admin
    created = client.post(
        "/api/admin/teams",
        json=_minimal_create_body(),
        headers=_auth(token),
    ).json()

    r = client.get(f"/api/admin/teams/{created['id']}", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["name"] == "ops"


def test_get_team_404_when_missing(client: TestClient, seeded_admin: tuple[UUID, str]) -> None:
    _, token = seeded_admin
    r = client.get(f"/api/admin/teams/{uuid4()}", headers=_auth(token))
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


def test_update_team_partial(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    admin_id, token = seeded_admin
    created = client.post(
        "/api/admin/teams",
        json=_minimal_create_body(),
        headers=_auth(token),
    ).json()

    r = client.patch(
        f"/api/admin/teams/{created['id']}",
        json={"description": "service desk ops"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    updated = r.json()
    assert updated["description"] == "service desk ops"
    assert updated["mode"] == "route"
    assert updated["version"] == 2

    with sync_session_maker() as s:
        audit = s.execute(
            select(AuditLog)
            .where(AuditLog.action == "team.update")
            .where(AuditLog.target_id == UUID(created["id"]))
        ).scalar_one()
    assert audit.actor_id == admin_id


def test_update_team_no_op_no_audit(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    _, token = seeded_admin
    created = client.post(
        "/api/admin/teams",
        json=_minimal_create_body(),
        headers=_auth(token),
    ).json()

    r = client.patch(
        f"/api/admin/teams/{created['id']}",
        json={},
        headers=_auth(token),
    )
    assert r.status_code == 200

    with sync_session_maker() as s:
        rows = s.execute(select(AuditLog).where(AuditLog.action == "team.update")).all()
    assert rows == []


def test_update_team_404_when_missing(client: TestClient, seeded_admin: tuple[UUID, str]) -> None:
    _, token = seeded_admin
    r = client.patch(
        f"/api/admin/teams/{uuid4()}",
        json={"name": "ghost"},
        headers=_auth(token),
    )
    assert r.status_code == 404


def test_update_team_rejects_duplicate_name(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    client.post(
        "/api/admin/teams",
        json=_minimal_create_body("alpha"),
        headers=_auth(token),
    )
    b = client.post(
        "/api/admin/teams",
        json=_minimal_create_body("beta"),
        headers=_auth(token),
    ).json()

    r = client.patch(
        f"/api/admin/teams/{b['id']}",
        json={"name": "alpha"},
        headers=_auth(token),
    )
    assert r.status_code == 409


def test_update_team_rejects_unknown_mode(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    created = client.post(
        "/api/admin/teams",
        json=_minimal_create_body(),
        headers=_auth(token),
    ).json()

    r = client.patch(
        f"/api/admin/teams/{created['id']}",
        json={"mode": "freeform"},
        headers=_auth(token),
    )
    assert r.status_code == 422


def test_update_team_rejects_invalid_members(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    seeded_agents: dict[str, UUID],
) -> None:
    _, token = seeded_admin
    created = client.post(
        "/api/admin/teams",
        json={
            **_minimal_create_body(),
            "member_agent_ids": [str(seeded_agents["researcher"])],
        },
        headers=_auth(token),
    ).json()

    r = client.patch(
        f"/api/admin/teams/{created['id']}",
        json={"member_agent_ids": [str(uuid4())]},
        headers=_auth(token),
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Archive / unarchive
# ---------------------------------------------------------------------------


def test_archive_team_hides_from_default_list(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    admin_id, token = seeded_admin
    created = client.post(
        "/api/admin/teams",
        json=_minimal_create_body(),
        headers=_auth(token),
    ).json()

    r = client.post(f"/api/admin/teams/{created['id']}/archive", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["archived_at"] is not None

    body = client.get("/api/admin/teams", headers=_auth(token)).json()
    assert body["total"] == 0

    body = client.get("/api/admin/teams?include_archived=true", headers=_auth(token)).json()
    assert body["total"] == 1

    with sync_session_maker() as s:
        audit = s.execute(
            select(AuditLog)
            .where(AuditLog.action == "team.archive")
            .where(AuditLog.target_id == UUID(created["id"]))
        ).scalar_one()
    assert audit.actor_id == admin_id


def test_archive_then_unarchive_restores(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    created = client.post(
        "/api/admin/teams",
        json=_minimal_create_body(),
        headers=_auth(token),
    ).json()

    client.post(f"/api/admin/teams/{created['id']}/archive", headers=_auth(token))
    r = client.post(f"/api/admin/teams/{created['id']}/unarchive", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["archived_at"] is None


def test_archive_already_archived_is_noop(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    _, token = seeded_admin
    created = client.post(
        "/api/admin/teams",
        json=_minimal_create_body(),
        headers=_auth(token),
    ).json()

    client.post(f"/api/admin/teams/{created['id']}/archive", headers=_auth(token))
    r = client.post(f"/api/admin/teams/{created['id']}/archive", headers=_auth(token))
    assert r.status_code == 200

    with sync_session_maker() as s:
        rows = s.execute(select(AuditLog).where(AuditLog.action == "team.archive")).all()
    assert len(rows) == 1


def test_archive_404_when_missing(client: TestClient, seeded_admin: tuple[UUID, str]) -> None:
    _, token = seeded_admin
    r = client.post(f"/api/admin/teams/{uuid4()}/archive", headers=_auth(token))
    assert r.status_code == 404

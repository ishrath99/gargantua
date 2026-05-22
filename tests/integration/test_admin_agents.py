"""Integration tests for ``/admin/agents/*``.

Mirrors the structure of :mod:`test_admin_mcp_types`: a minimal app
mounts the admin + auth routers, an admin token is seeded, every route
is verified against its auth gate and its happy / sad paths, and audit
rows are asserted on every mutation.

The bulk of the surface here is reference validation:
``mcp_server_ids`` / ``child_resource_ids`` are uuid-arrays Postgres
can't FK into, so the repo (and therefore the route) checks them in
code.  Each "invalid refs" path is exercised end-to-end.
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

from gargantua.db.models import (
    AuditLog,
    MCPServer,
    MCPServerChildResource,
    MCPServerType,
    User,
)

# ---------------------------------------------------------------------------
# Fixtures (mirror test_admin_mcp_types.py)
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
    a.include_router(auth_router, prefix="/auth")
    a.include_router(admin_router, prefix="/admin")
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
def mcp_refs(sync_session_maker) -> dict[str, UUID]:
    """Seed a parent server + a child resource so reference-validation
    tests have real IDs to attach to.  Returns a dict of ids."""
    with sync_session_maker() as s:
        t = MCPServerType(
            slug="swagger-mcp",
            name="Swagger",
            mode="streamable_http",
            supports_swagger_child=True,
        )
        s.add(t)
        s.flush()
        srv = MCPServer(type_id=t.id, name="api-gw", env_tag="prod")
        s.add(srv)
        s.flush()
        child = MCPServerChildResource(
            parent_mcp_server_id=srv.id,
            type="swagger",
            name="orders",
            url="https://example.com/swagger.json",
            enabled=True,
        )
        s.add(child)
        s.commit()
        return {"type_id": t.id, "server_id": srv.id, "child_id": child.id}


def _minimal_create_body(name: str = "researcher") -> dict:
    return {
        "name": name,
        "model": "gpt-5",
        "instructions": "Be terse.",
    }


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------


def test_list_agents_without_token_returns_401(client: TestClient) -> None:
    r = client.get("/admin/agents")
    assert r.status_code == 401


def test_list_agents_with_user_token_returns_403(
    client: TestClient, seeded_user: tuple[UUID, str]
) -> None:
    _, token = seeded_user
    r = client.get("/admin/agents", headers=_auth(token))
    assert r.status_code == 403


def test_list_agents_with_admin_token_returns_200(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    r = client.get("/admin/agents", headers=_auth(token))
    assert r.status_code == 200
    assert r.json() == {"items": [], "total": 0, "page": 1, "page_size": 50}


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_agent_201_and_audit_logged(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    admin_id, token = seeded_admin
    r = client.post(
        "/admin/agents",
        json=_minimal_create_body(),
        headers=_auth(token),
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["name"] == "researcher"
    assert created["model"] == "gpt-5"
    assert created["instructions"] == "Be terse."
    assert created["mcp_server_ids"] == []
    assert created["child_resource_ids"] == []
    assert created["version"] == 1

    with sync_session_maker() as s:
        audit = s.execute(
            select(AuditLog)
            .where(AuditLog.action == "agent.create")
            .where(AuditLog.target_id == UUID(created["id"]))
        ).scalar_one()
    assert audit.actor_id == admin_id
    assert audit.before is None
    assert audit.after["name"] == "researcher"


def test_create_agent_with_refs(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    mcp_refs: dict[str, UUID],
) -> None:
    _, token = seeded_admin
    body = _minimal_create_body()
    body["mcp_server_ids"] = [str(mcp_refs["server_id"])]
    body["child_resource_ids"] = [str(mcp_refs["child_id"])]
    r = client.post("/admin/agents", json=body, headers=_auth(token))
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["mcp_server_ids"] == [str(mcp_refs["server_id"])]
    assert created["child_resource_ids"] == [str(mcp_refs["child_id"])]


def test_create_agent_rejects_duplicate_name(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    body = _minimal_create_body()
    r1 = client.post("/admin/agents", json=body, headers=_auth(token))
    assert r1.status_code == 201
    r2 = client.post("/admin/agents", json=body, headers=_auth(token))
    assert r2.status_code == 409


def test_create_agent_rejects_missing_mcp_server(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    body = _minimal_create_body()
    body["mcp_server_ids"] = [str(uuid4())]
    r = client.post("/admin/agents", json=body, headers=_auth(token))
    assert r.status_code == 422
    detail = r.json()["detail"]
    # Detail is structured so the UI can point at the offending IDs.
    assert detail["missing_mcp_server_ids"] == body["mcp_server_ids"]


def test_create_agent_rejects_orphan_child(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    mcp_refs: dict[str, UUID],
) -> None:
    """Child whose parent isn't in mcp_server_ids is invalid."""
    _, token = seeded_admin
    body = _minimal_create_body()
    body["mcp_server_ids"] = []  # parent intentionally omitted
    body["child_resource_ids"] = [str(mcp_refs["child_id"])]
    r = client.post("/admin/agents", json=body, headers=_auth(token))
    assert r.status_code == 422


def test_create_agent_rejects_invalid_payload(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    # Missing required `instructions` -> Pydantic 422.
    r = client.post(
        "/admin/agents",
        json={"name": "x", "model": "m"},
        headers=_auth(token),
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# List + get
# ---------------------------------------------------------------------------


def _seed_three_agents(client: TestClient, token: str) -> None:
    for name, model in [
        ("alpha", "gpt-5"),
        ("beta", "gpt-5-mini"),
        ("betatron", "gpt-5"),
    ]:
        client.post(
            "/admin/agents",
            json={
                "name": name,
                "model": model,
                "instructions": "i",
            },
            headers=_auth(token),
        )


def test_list_agents_paginates(client: TestClient, seeded_admin: tuple[UUID, str]) -> None:
    _, token = seeded_admin
    _seed_three_agents(client, token)
    r = client.get("/admin/agents?page=1&page_size=2", headers=_auth(token))
    body = r.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2


def test_list_agents_search_matches_substring(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    _seed_three_agents(client, token)
    r = client.get("/admin/agents?search=beta", headers=_auth(token))
    body = r.json()
    assert body["total"] == 2
    assert {item["name"] for item in body["items"]} == {"beta", "betatron"}


def test_list_agents_model_filter(client: TestClient, seeded_admin: tuple[UUID, str]) -> None:
    _, token = seeded_admin
    _seed_three_agents(client, token)
    r = client.get("/admin/agents?model=gpt-5", headers=_auth(token))
    body = r.json()
    assert body["total"] == 2
    assert {item["name"] for item in body["items"]} == {"alpha", "betatron"}


def test_get_agent_by_id(client: TestClient, seeded_admin: tuple[UUID, str]) -> None:
    _, token = seeded_admin
    created = client.post(
        "/admin/agents",
        json=_minimal_create_body(),
        headers=_auth(token),
    ).json()

    r = client.get(f"/admin/agents/{created['id']}", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["name"] == "researcher"


def test_get_agent_404_when_missing(client: TestClient, seeded_admin: tuple[UUID, str]) -> None:
    _, token = seeded_admin
    r = client.get(f"/admin/agents/{uuid4()}", headers=_auth(token))
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


def test_update_agent_partial(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    admin_id, token = seeded_admin
    created = client.post(
        "/admin/agents",
        json=_minimal_create_body(),
        headers=_auth(token),
    ).json()

    r = client.patch(
        f"/admin/agents/{created['id']}",
        json={"instructions": "be helpful"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    updated = r.json()
    assert updated["instructions"] == "be helpful"
    # Unspecified fields preserved.
    assert updated["model"] == "gpt-5"
    assert updated["name"] == "researcher"
    assert updated["version"] == 2

    with sync_session_maker() as s:
        audit = s.execute(
            select(AuditLog)
            .where(AuditLog.action == "agent.update")
            .where(AuditLog.target_id == UUID(created["id"]))
        ).scalar_one()
    assert audit.actor_id == admin_id
    assert audit.before["instructions"] == "Be terse."
    assert audit.after["instructions"] == "be helpful"


def test_update_agent_no_op_no_audit(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    _, token = seeded_admin
    created = client.post(
        "/admin/agents",
        json=_minimal_create_body(),
        headers=_auth(token),
    ).json()

    r = client.patch(
        f"/admin/agents/{created['id']}",
        json={},
        headers=_auth(token),
    )
    assert r.status_code == 200

    with sync_session_maker() as s:
        rows = s.execute(select(AuditLog).where(AuditLog.action == "agent.update")).all()
    assert rows == []


def test_update_agent_404_when_missing(client: TestClient, seeded_admin: tuple[UUID, str]) -> None:
    _, token = seeded_admin
    r = client.patch(
        f"/admin/agents/{uuid4()}",
        json={"name": "ghost"},
        headers=_auth(token),
    )
    assert r.status_code == 404


def test_update_agent_rejects_duplicate_name(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    client.post(
        "/admin/agents",
        json=_minimal_create_body("alpha"),
        headers=_auth(token),
    )
    b = client.post(
        "/admin/agents",
        json=_minimal_create_body("beta"),
        headers=_auth(token),
    ).json()

    r = client.patch(
        f"/admin/agents/{b['id']}",
        json={"name": "alpha"},
        headers=_auth(token),
    )
    assert r.status_code == 409


def test_update_agent_rejects_invalid_refs(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    mcp_refs: dict[str, UUID],
) -> None:
    """Clearing mcp_server_ids while leaving an orphan child must 422."""
    _, token = seeded_admin
    created = client.post(
        "/admin/agents",
        json={
            **_minimal_create_body(),
            "mcp_server_ids": [str(mcp_refs["server_id"])],
            "child_resource_ids": [str(mcp_refs["child_id"])],
        },
        headers=_auth(token),
    ).json()

    r = client.patch(
        f"/admin/agents/{created['id']}",
        json={"mcp_server_ids": []},
        headers=_auth(token),
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Archive / unarchive
# ---------------------------------------------------------------------------


def test_archive_agent_hides_from_default_list(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    admin_id, token = seeded_admin
    created = client.post(
        "/admin/agents",
        json=_minimal_create_body(),
        headers=_auth(token),
    ).json()

    r = client.post(f"/admin/agents/{created['id']}/archive", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["archived_at"] is not None

    body = client.get("/admin/agents", headers=_auth(token)).json()
    assert body["total"] == 0

    body = client.get("/admin/agents?include_archived=true", headers=_auth(token)).json()
    assert body["total"] == 1

    with sync_session_maker() as s:
        audit = s.execute(
            select(AuditLog)
            .where(AuditLog.action == "agent.archive")
            .where(AuditLog.target_id == UUID(created["id"]))
        ).scalar_one()
    assert audit.actor_id == admin_id


def test_archive_then_unarchive_restores(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    created = client.post(
        "/admin/agents",
        json=_minimal_create_body(),
        headers=_auth(token),
    ).json()

    client.post(f"/admin/agents/{created['id']}/archive", headers=_auth(token))
    r = client.post(f"/admin/agents/{created['id']}/unarchive", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["archived_at"] is None


def test_archive_already_archived_is_noop(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    _, token = seeded_admin
    created = client.post(
        "/admin/agents",
        json=_minimal_create_body(),
        headers=_auth(token),
    ).json()

    client.post(f"/admin/agents/{created['id']}/archive", headers=_auth(token))
    r = client.post(f"/admin/agents/{created['id']}/archive", headers=_auth(token))
    assert r.status_code == 200

    with sync_session_maker() as s:
        rows = s.execute(select(AuditLog).where(AuditLog.action == "agent.archive")).all()
    assert len(rows) == 1


def test_archive_404_when_missing(client: TestClient, seeded_admin: tuple[UUID, str]) -> None:
    _, token = seeded_admin
    r = client.post(f"/admin/agents/{uuid4()}/archive", headers=_auth(token))
    assert r.status_code == 404

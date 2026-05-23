"""Integration tests for ``/admin/mcp-server-types/*``.

Mirrors the structure of ``test_admin_users``: a minimal app mounts the
admin + auth routers, an admin token is seeded, every route is verified
against its auth gate and its happy / sad paths, audit rows are asserted
on every mutation.
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

from gargantua.db.models import AuditLog, MCPServerType, User

# ---------------------------------------------------------------------------
# Fixtures (mirror test_admin_users.py)
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


def _minimal_create_body(slug: str = "postgres") -> dict:
    return {
        "slug": slug,
        "name": "PostgreSQL MCP",
        "mode": "stdio",
        "description": "Run SQL queries against a Postgres database.",
        "default_command": "uvx",
        "default_args": ["postgres-mcp"],
        "config_schema": [
            {
                "name": "DSN",
                "label": "Connection string",
                "type": "password",
                "is_secret": True,
                "required": True,
            }
        ],
    }


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------


def test_list_types_without_token_returns_401(client: TestClient) -> None:
    r = client.get("/api/admin/mcp-server-types")
    assert r.status_code == 401


def test_list_types_with_user_token_returns_403(
    client: TestClient, seeded_user: tuple[UUID, str]
) -> None:
    _, token = seeded_user
    r = client.get("/api/admin/mcp-server-types", headers=_auth(token))
    assert r.status_code == 403


def test_list_types_with_admin_token_returns_200(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    r = client.get("/api/admin/mcp-server-types", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body == {"items": [], "total": 0, "page": 1, "page_size": 50}


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_type_201_and_audit_logged(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    admin_id, token = seeded_admin
    r = client.post(
        "/api/admin/mcp-server-types",
        json=_minimal_create_body(),
        headers=_auth(token),
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["slug"] == "postgres"
    assert created["mode"] == "stdio"
    assert created["version"] == 1
    assert created["archived_at"] is None
    assert created["config_schema"][0]["name"] == "DSN"

    with sync_session_maker() as s:
        row = s.execute(select(MCPServerType).where(MCPServerType.slug == "postgres")).scalar_one()
        audit = s.execute(
            select(AuditLog)
            .where(AuditLog.action == "mcp_server_type.create")
            .where(AuditLog.target_id == row.id)
        ).scalar_one()
    assert audit.actor_id == admin_id
    assert audit.before is None
    assert audit.after["slug"] == "postgres"


def test_create_type_rejects_duplicate_slug(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    body = _minimal_create_body()
    r1 = client.post("/api/admin/mcp-server-types", json=body, headers=_auth(token))
    assert r1.status_code == 201
    r2 = client.post("/api/admin/mcp-server-types", json=body, headers=_auth(token))
    assert r2.status_code == 409


def test_create_type_rejects_invalid_mode(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    body = _minimal_create_body()
    body["mode"] = "websocket"
    r = client.post("/api/admin/mcp-server-types", json=body, headers=_auth(token))
    assert r.status_code == 422


def test_create_type_rejects_invalid_slug(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    body = _minimal_create_body()
    body["slug"] = "Postgres With Spaces"
    r = client.post("/api/admin/mcp-server-types", json=body, headers=_auth(token))
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# List + get
# ---------------------------------------------------------------------------


def _seed_three(client: TestClient, token: str) -> None:
    for slug, mode in [
        ("postgres", "stdio"),
        ("opensearch", "sse"),
        ("swagger", "streamable_http"),
    ]:
        client.post(
            "/api/admin/mcp-server-types",
            json={"slug": slug, "name": slug.title(), "mode": mode},
            headers=_auth(token),
        )


def test_list_types_paginates_and_filters_by_mode(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    _seed_three(client, token)

    r = client.get("/api/admin/mcp-server-types?mode=stdio", headers=_auth(token))
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["slug"] == "postgres"

    r = client.get("/api/admin/mcp-server-types?page=1&page_size=2", headers=_auth(token))
    assert r.json()["total"] == 3
    assert len(r.json()["items"]) == 2


def test_list_types_search_matches_substring(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    _seed_three(client, token)

    r = client.get("/api/admin/mcp-server-types?search=search", headers=_auth(token))
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["slug"] == "opensearch"


def test_get_type_by_id(client: TestClient, seeded_admin: tuple[UUID, str]) -> None:
    _, token = seeded_admin
    created = client.post(
        "/api/admin/mcp-server-types",
        json=_minimal_create_body(),
        headers=_auth(token),
    ).json()

    r = client.get(f"/api/admin/mcp-server-types/{created['id']}", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["slug"] == "postgres"


def test_get_type_404_when_missing(client: TestClient, seeded_admin: tuple[UUID, str]) -> None:
    _, token = seeded_admin
    r = client.get(f"/api/admin/mcp-server-types/{uuid4()}", headers=_auth(token))
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


def test_update_type_partial(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    admin_id, token = seeded_admin
    created = client.post(
        "/api/admin/mcp-server-types",
        json=_minimal_create_body(),
        headers=_auth(token),
    ).json()

    r = client.patch(
        f"/api/admin/mcp-server-types/{created['id']}",
        json={"name": "Postgres v2", "description": "now even better"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    updated = r.json()
    assert updated["name"] == "Postgres v2"
    assert updated["description"] == "now even better"
    # Unspecified fields preserved.
    assert updated["mode"] == "stdio"
    assert updated["default_command"] == "uvx"
    # Version bumped.
    assert updated["version"] == 2

    with sync_session_maker() as s:
        audit = s.execute(
            select(AuditLog)
            .where(AuditLog.action == "mcp_server_type.update")
            .where(AuditLog.target_id == UUID(created["id"]))
        ).scalar_one()
    assert audit.actor_id == admin_id
    assert audit.before["name"] == "PostgreSQL MCP"
    assert audit.after["name"] == "Postgres v2"


def test_update_type_no_op_does_not_write_audit(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    """Empty body / no real changes should not produce a noisy audit row."""
    _, token = seeded_admin
    created = client.post(
        "/api/admin/mcp-server-types",
        json=_minimal_create_body(),
        headers=_auth(token),
    ).json()

    r = client.patch(
        f"/api/admin/mcp-server-types/{created['id']}",
        json={},
        headers=_auth(token),
    )
    assert r.status_code == 200

    with sync_session_maker() as s:
        rows = s.execute(select(AuditLog).where(AuditLog.action == "mcp_server_type.update")).all()
    assert rows == []


def test_update_type_404_when_missing(client: TestClient, seeded_admin: tuple[UUID, str]) -> None:
    _, token = seeded_admin
    r = client.patch(
        f"/api/admin/mcp-server-types/{uuid4()}",
        json={"name": "ghost"},
        headers=_auth(token),
    )
    assert r.status_code == 404


def test_update_type_rejects_invalid_mode(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    created = client.post(
        "/api/admin/mcp-server-types",
        json=_minimal_create_body(),
        headers=_auth(token),
    ).json()

    r = client.patch(
        f"/api/admin/mcp-server-types/{created['id']}",
        json={"mode": "websocket"},
        headers=_auth(token),
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Archive / unarchive
# ---------------------------------------------------------------------------


def test_archive_type_hides_from_default_list(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    admin_id, token = seeded_admin
    created = client.post(
        "/api/admin/mcp-server-types",
        json=_minimal_create_body(),
        headers=_auth(token),
    ).json()

    r = client.post(
        f"/api/admin/mcp-server-types/{created['id']}/archive",
        headers=_auth(token),
    )
    assert r.status_code == 200
    assert r.json()["archived_at"] is not None

    # Default listing excludes archived.
    body = client.get("/api/admin/mcp-server-types", headers=_auth(token)).json()
    assert body["total"] == 0

    # include_archived surfaces it.
    body = client.get("/api/admin/mcp-server-types?include_archived=true", headers=_auth(token)).json()
    assert body["total"] == 1

    with sync_session_maker() as s:
        audit = s.execute(
            select(AuditLog)
            .where(AuditLog.action == "mcp_server_type.archive")
            .where(AuditLog.target_id == UUID(created["id"]))
        ).scalar_one()
    assert audit.actor_id == admin_id


def test_archive_then_unarchive_restores(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    created = client.post(
        "/api/admin/mcp-server-types",
        json=_minimal_create_body(),
        headers=_auth(token),
    ).json()

    client.post(
        f"/api/admin/mcp-server-types/{created['id']}/archive",
        headers=_auth(token),
    )
    r = client.post(
        f"/api/admin/mcp-server-types/{created['id']}/unarchive",
        headers=_auth(token),
    )
    assert r.status_code == 200
    assert r.json()["archived_at"] is None


def test_archive_already_archived_is_noop(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    _, token = seeded_admin
    created = client.post(
        "/api/admin/mcp-server-types",
        json=_minimal_create_body(),
        headers=_auth(token),
    ).json()

    client.post(f"/api/admin/mcp-server-types/{created['id']}/archive", headers=_auth(token))
    r = client.post(f"/api/admin/mcp-server-types/{created['id']}/archive", headers=_auth(token))
    assert r.status_code == 200

    with sync_session_maker() as s:
        rows = s.execute(select(AuditLog).where(AuditLog.action == "mcp_server_type.archive")).all()
    # Only one audit row, not two — second archive is a no-op.
    assert len(rows) == 1


def test_archive_404_when_missing(client: TestClient, seeded_admin: tuple[UUID, str]) -> None:
    _, token = seeded_admin
    r = client.post(f"/api/admin/mcp-server-types/{uuid4()}/archive", headers=_auth(token))
    assert r.status_code == 404

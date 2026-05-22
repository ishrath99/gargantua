"""Integration tests for ``/admin/mcp-servers/{server_id}/child-resources/*``.

Mirrors the server-routes test fixtures, with one key extra: every
request takes the parent server id from the URL so we can prove
parent-scoping and the "this parent's type doesn't support children"
gate.
"""

from __future__ import annotations

import base64
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

from gargantua.api.schemas import SECRET_PLACEHOLDER
from gargantua.db.models import (
    AuditLog,
    MCPServer,
    MCPServerType,
    User,
)

# ---------------------------------------------------------------------------
# Fixtures (shared shape with test_admin_mcp_servers.py)
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
    monkeypatch.setenv("MASTER_KEY", base64.b64encode(b"\x77" * 32).decode("ascii"))
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
def seeded_swagger_parent(sync_session_maker) -> UUID:
    """A server whose type supports_swagger_child=True."""
    with sync_session_maker() as s:
        t = MCPServerType(
            slug="swagger-mcp",
            name="Swagger",
            mode="streamable_http",
            supports_swagger_child=True,
        )
        s.add(t)
        s.flush()
        p = MCPServer(type_id=t.id, name="api-gw", env_tag="prod")
        s.add(p)
        s.commit()
        s.refresh(p)
        return p.id


@pytest.fixture
def seeded_non_swagger_parent(sync_session_maker) -> UUID:
    """A server whose type does NOT support children."""
    with sync_session_maker() as s:
        t = MCPServerType(
            slug="postgres",
            name="Postgres",
            mode="stdio",
            supports_swagger_child=False,
        )
        s.add(t)
        s.flush()
        p = MCPServer(type_id=t.id, name="db", env_tag="prod")
        s.add(p)
        s.commit()
        s.refresh(p)
        return p.id


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _child_body(**overrides) -> dict:
    body = {
        "type": "swagger",
        "name": "orders-api",
        "url": "https://example.com/swagger.json",
        "headers": {"Authorization": "Bearer secret-token"},
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------


def test_list_children_401(client: TestClient) -> None:
    assert client.get(f"/admin/mcp-servers/{uuid4()}/child-resources").status_code == 401


def test_list_children_404_when_parent_missing(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    r = client.get(f"/admin/mcp-servers/{uuid4()}/child-resources", headers=_auth(token))
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_child_masks_headers_and_audits(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    seeded_swagger_parent: UUID,
    sync_session_maker: sessionmaker,
) -> None:
    admin_id, token = seeded_admin
    r = client.post(
        f"/admin/mcp-servers/{seeded_swagger_parent}/child-resources",
        json=_child_body(),
        headers=_auth(token),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    # Headers always masked.
    assert body["headers"]["Authorization"] == SECRET_PLACEHOLDER
    assert "secret-token" not in r.text

    with sync_session_maker() as s:
        audit = s.execute(
            select(AuditLog)
            .where(AuditLog.action == "mcp_server_child_resource.create")
            .where(AuditLog.target_id == UUID(body["id"]))
        ).scalar_one()
    assert audit.actor_id == admin_id
    assert "secret-token" not in str(audit.after)


def test_create_child_rejects_parent_without_swagger_support(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    seeded_non_swagger_parent: UUID,
) -> None:
    _, token = seeded_admin
    r = client.post(
        f"/admin/mcp-servers/{seeded_non_swagger_parent}/child-resources",
        json=_child_body(),
        headers=_auth(token),
    )
    assert r.status_code == 422
    assert "child" in r.text.lower()


def test_create_child_rejects_invalid_type(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    seeded_swagger_parent: UUID,
) -> None:
    _, token = seeded_admin
    r = client.post(
        f"/admin/mcp-servers/{seeded_swagger_parent}/child-resources",
        json=_child_body(type="postman"),
        headers=_auth(token),
    )
    assert r.status_code == 422


def test_create_child_duplicate_name(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    seeded_swagger_parent: UUID,
) -> None:
    _, token = seeded_admin
    body = _child_body()
    a = client.post(
        f"/admin/mcp-servers/{seeded_swagger_parent}/child-resources",
        json=body,
        headers=_auth(token),
    )
    assert a.status_code == 201
    b = client.post(
        f"/admin/mcp-servers/{seeded_swagger_parent}/child-resources",
        json=body,
        headers=_auth(token),
    )
    assert b.status_code == 409


# ---------------------------------------------------------------------------
# List + get
# ---------------------------------------------------------------------------


def test_list_scoped_to_parent_and_excludes_disabled(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    seeded_swagger_parent: UUID,
) -> None:
    _, token = seeded_admin
    a = client.post(
        f"/admin/mcp-servers/{seeded_swagger_parent}/child-resources",
        json=_child_body(name="alpha"),
        headers=_auth(token),
    ).json()
    client.post(
        f"/admin/mcp-servers/{seeded_swagger_parent}/child-resources",
        json=_child_body(name="beta"),
        headers=_auth(token),
    )
    client.post(
        f"/admin/mcp-servers/{seeded_swagger_parent}/child-resources/{a['id']}/disable",
        headers=_auth(token),
    )

    r = client.get(
        f"/admin/mcp-servers/{seeded_swagger_parent}/child-resources",
        headers=_auth(token),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "beta"

    r = client.get(
        f"/admin/mcp-servers/{seeded_swagger_parent}/child-resources?include_disabled=true",
        headers=_auth(token),
    )
    assert r.json()["total"] == 2


def test_get_child_404_when_not_under_this_parent(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    seeded_swagger_parent: UUID,
    sync_session_maker: sessionmaker,
) -> None:
    """A child id from another parent is 404 here, never silently surfaced."""
    _, token = seeded_admin

    # Make a second parent + child under it.
    with sync_session_maker() as s:
        t2 = MCPServerType(
            slug="swagger-mcp-2",
            name="Swagger 2",
            mode="streamable_http",
            supports_swagger_child=True,
        )
        s.add(t2)
        s.flush()
        p2 = MCPServer(type_id=t2.id, name="api-gw-2", env_tag="prod")
        s.add(p2)
        s.commit()
        s.refresh(p2)
        p2id = p2.id

    other = client.post(
        f"/admin/mcp-servers/{p2id}/child-resources",
        json=_child_body(),
        headers=_auth(token),
    ).json()

    # Querying the wrong parent — should 404.
    r = client.get(
        f"/admin/mcp-servers/{seeded_swagger_parent}/child-resources/{other['id']}",
        headers=_auth(token),
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


def test_update_partial_preserves_headers(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    seeded_swagger_parent: UUID,
) -> None:
    _, token = seeded_admin
    created = client.post(
        f"/admin/mcp-servers/{seeded_swagger_parent}/child-resources",
        json=_child_body(),
        headers=_auth(token),
    ).json()

    r = client.patch(
        f"/admin/mcp-servers/{seeded_swagger_parent}/child-resources/{created['id']}",
        json={"url": "https://example.com/new.json"},
        headers=_auth(token),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["url"] == "https://example.com/new.json"
    assert body["headers"]["Authorization"] == SECRET_PLACEHOLDER


def test_update_headers_audit_excludes_plaintext(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    seeded_swagger_parent: UUID,
    sync_session_maker: sessionmaker,
) -> None:
    _, token = seeded_admin
    created = client.post(
        f"/admin/mcp-servers/{seeded_swagger_parent}/child-resources",
        json=_child_body(),
        headers=_auth(token),
    ).json()

    r = client.patch(
        f"/admin/mcp-servers/{seeded_swagger_parent}/child-resources/{created['id']}",
        json={"headers": {"Authorization": "Bearer rotated-token"}},
        headers=_auth(token),
    )
    assert r.status_code == 200
    assert "rotated-token" not in r.text

    with sync_session_maker() as s:
        audit = s.execute(
            select(AuditLog)
            .where(AuditLog.action == "mcp_server_child_resource.update")
            .where(AuditLog.target_id == UUID(created["id"]))
        ).scalar_one()
    assert "rotated-token" not in str(audit.after)
    assert "secret-token" not in str(audit.before)


# ---------------------------------------------------------------------------
# Enable / disable
# ---------------------------------------------------------------------------


def test_enable_disable_round_trip(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    seeded_swagger_parent: UUID,
) -> None:
    _, token = seeded_admin
    created = client.post(
        f"/admin/mcp-servers/{seeded_swagger_parent}/child-resources",
        json=_child_body(),
        headers=_auth(token),
    ).json()

    r = client.post(
        f"/admin/mcp-servers/{seeded_swagger_parent}/child-resources/{created['id']}/disable",
        headers=_auth(token),
    )
    assert r.status_code == 200
    assert r.json()["enabled"] is False

    r = client.post(
        f"/admin/mcp-servers/{seeded_swagger_parent}/child-resources/{created['id']}/enable",
        headers=_auth(token),
    )
    assert r.status_code == 200
    assert r.json()["enabled"] is True


def test_disable_idempotent_writes_one_audit(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    seeded_swagger_parent: UUID,
    sync_session_maker: sessionmaker,
) -> None:
    _, token = seeded_admin
    created = client.post(
        f"/admin/mcp-servers/{seeded_swagger_parent}/child-resources",
        json=_child_body(),
        headers=_auth(token),
    ).json()
    client.post(
        f"/admin/mcp-servers/{seeded_swagger_parent}/child-resources/{created['id']}/disable",
        headers=_auth(token),
    )
    client.post(
        f"/admin/mcp-servers/{seeded_swagger_parent}/child-resources/{created['id']}/disable",
        headers=_auth(token),
    )
    with sync_session_maker() as s:
        rows = (
            s.execute(
                select(AuditLog).where(AuditLog.action == "mcp_server_child_resource.disable")
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1

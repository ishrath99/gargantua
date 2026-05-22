"""Integration tests for ``/admin/mcp-servers/*``.

Mirrors the existing ``test_admin_mcp_types`` shape: a minimal app
mounts auth + admin routers, seeds an admin token, then drives every
route through its happy + sad paths.  Two additional concerns specific
to this PR:

1. **Secret masking on read** — fields declared ``is_secret=true`` on
   the parent type's ``config_schema`` come back as ``"<redacted>"``;
   plain fields come back as-is.
2. **Audit hygiene** — ``before/after`` payloads on audit rows must
   carry the *masked* projection, never raw secret values.
"""

from __future__ import annotations

import base64
import os
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
# Fixtures
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
    monkeypatch.setenv(
        "MASTER_KEY", base64.b64encode(b"\x55" * 32).decode("ascii")
    )
    _reset_caches()
    yield
    _reset_caches()


@pytest.fixture
def app(configured_env) -> FastAPI:  # noqa: ARG001
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
        return u.id, mint_access_token(
            subject=str(u.id), scopes=[SCOPE_ADMIN, SCOPE_USER]
        )


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
def seeded_type(sync_session_maker) -> UUID:
    """A type with one secret + one plain field in its config_schema."""
    with sync_session_maker() as s:
        t = MCPServerType(
            slug="postgres",
            name="Postgres",
            mode="stdio",
            config_schema=[
                {"name": "DSN", "label": "DSN", "type": "password", "is_secret": True, "required": True},
                {"name": "READ_ONLY", "label": "Read only", "type": "select", "is_secret": False, "required": False},
            ],
        )
        s.add(t)
        s.commit()
        s.refresh(t)
        return t.id


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _server_body(type_id: UUID, **overrides) -> dict:
    body = {
        "type_id": str(type_id),
        "name": "db-prod",
        "env_tag": "prod",
        "env_vars": {"DSN": "postgres://hidden", "READ_ONLY": "true"},
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------


def test_list_servers_401_without_token(client: TestClient) -> None:
    assert client.get("/admin/mcp-servers").status_code == 401


def test_list_servers_403_for_user_token(
    client: TestClient, seeded_user: tuple[UUID, str]
) -> None:
    _, token = seeded_user
    assert client.get("/admin/mcp-servers", headers=_auth(token)).status_code == 403


def test_list_servers_200_for_admin(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    r = client.get("/admin/mcp-servers", headers=_auth(token))
    assert r.status_code == 200
    assert r.json() == {"items": [], "total": 0, "page": 1, "page_size": 50}


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_server_masks_secrets_in_response(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    seeded_type: UUID,
    sync_session_maker: sessionmaker,
) -> None:
    admin_id, token = seeded_admin
    r = client.post(
        "/admin/mcp-servers", json=_server_body(seeded_type), headers=_auth(token)
    )
    assert r.status_code == 201, r.text
    body = r.json()

    # DSN is is_secret=True -> masked.  READ_ONLY is plain -> visible.
    assert body["env_vars"]["DSN"] == SECRET_PLACEHOLDER
    assert body["env_vars"]["READ_ONLY"] == "true"

    # Raw DSN never appears anywhere in the response.
    assert "postgres://hidden" not in r.text

    # Audit row has the masked projection, not the raw secret.
    with sync_session_maker() as s:
        audit = s.execute(
            select(AuditLog)
            .where(AuditLog.action == "mcp_server.create")
            .where(AuditLog.target_id == UUID(body["id"]))
        ).scalar_one()
    assert audit.actor_id == admin_id
    assert audit.after["env_vars"]["DSN"] == SECRET_PLACEHOLDER
    assert "postgres://hidden" not in str(audit.after)


def test_create_server_rejects_unknown_type(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    r = client.post(
        "/admin/mcp-servers",
        json=_server_body(uuid4()),
        headers=_auth(token),
    )
    # Type doesn't exist -> 422 (caller-supplied bad reference, not server error).
    assert r.status_code == 422


def test_create_server_rejects_archived_type(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    _, token = seeded_admin
    with sync_session_maker() as s:
        from datetime import datetime, timezone

        t = MCPServerType(slug="x", name="X", mode="stdio")
        t.archived_at = datetime.now(tz=timezone.utc)
        s.add(t)
        s.commit()
        s.refresh(t)
        tid = t.id

    r = client.post(
        "/admin/mcp-servers", json=_server_body(tid), headers=_auth(token)
    )
    assert r.status_code == 422
    assert "archived" in r.text.lower()


def test_create_server_rejects_duplicate(
    client: TestClient, seeded_admin: tuple[UUID, str], seeded_type: UUID
) -> None:
    _, token = seeded_admin
    body = _server_body(seeded_type)
    assert client.post("/admin/mcp-servers", json=body, headers=_auth(token)).status_code == 201
    assert client.post("/admin/mcp-servers", json=body, headers=_auth(token)).status_code == 409


def test_create_server_invalid_env_tag(
    client: TestClient, seeded_admin: tuple[UUID, str], seeded_type: UUID
) -> None:
    _, token = seeded_admin
    r = client.post(
        "/admin/mcp-servers",
        json=_server_body(seeded_type, env_tag="UPPER CASE"),
        headers=_auth(token),
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# List + get
# ---------------------------------------------------------------------------


def test_list_filters_by_type_and_env_tag(
    client: TestClient, seeded_admin: tuple[UUID, str], seeded_type: UUID
) -> None:
    _, token = seeded_admin
    client.post(
        "/admin/mcp-servers",
        json=_server_body(seeded_type, name="a", env_tag="prod"),
        headers=_auth(token),
    )
    client.post(
        "/admin/mcp-servers",
        json=_server_body(seeded_type, name="a", env_tag="dev"),
        headers=_auth(token),
    )

    r = client.get(
        f"/admin/mcp-servers?type_id={seeded_type}&env_tag=prod",
        headers=_auth(token),
    )
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["env_tag"] == "prod"
    # Even in list view, DSN is masked.
    assert body["items"][0]["env_vars"]["DSN"] == SECRET_PLACEHOLDER


def test_get_server_404(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    r = client.get(f"/admin/mcp-servers/{uuid4()}", headers=_auth(token))
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


def test_update_partial_preserves_env_vars(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    seeded_type: UUID,
    sync_session_maker: sessionmaker,
) -> None:
    _, token = seeded_admin
    created = client.post(
        "/admin/mcp-servers", json=_server_body(seeded_type), headers=_auth(token)
    ).json()

    r = client.patch(
        f"/admin/mcp-servers/{created['id']}",
        json={"name": "db-prod-v2"},
        headers=_auth(token),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "db-prod-v2"
    # env_vars untouched -> DSN still masked but key remains.
    assert body["env_vars"]["DSN"] == SECRET_PLACEHOLDER
    assert body["env_vars"]["READ_ONLY"] == "true"
    assert body["version"] == 2


def test_update_env_vars_replaces_all_and_audits_masked(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    seeded_type: UUID,
    sync_session_maker: sessionmaker,
) -> None:
    _, token = seeded_admin
    created = client.post(
        "/admin/mcp-servers", json=_server_body(seeded_type), headers=_auth(token)
    ).json()

    r = client.patch(
        f"/admin/mcp-servers/{created['id']}",
        json={"env_vars": {"DSN": "postgres://new", "READ_ONLY": "false"}},
        headers=_auth(token),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["env_vars"]["DSN"] == SECRET_PLACEHOLDER
    assert body["env_vars"]["READ_ONLY"] == "false"
    assert "postgres://new" not in r.text

    with sync_session_maker() as s:
        audit = s.execute(
            select(AuditLog)
            .where(AuditLog.action == "mcp_server.update")
            .where(AuditLog.target_id == UUID(created["id"]))
        ).scalar_one()
    # Neither before nor after carries the raw DSN value.
    assert "postgres://new" not in str(audit.after)
    assert "postgres://hidden" not in str(audit.before)
    # But the change in the non-secret field is visible.
    assert audit.before["env_vars"]["READ_ONLY"] == "true"
    assert audit.after["env_vars"]["READ_ONLY"] == "false"


def test_update_noop_writes_no_audit(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    seeded_type: UUID,
    sync_session_maker: sessionmaker,
) -> None:
    _, token = seeded_admin
    created = client.post(
        "/admin/mcp-servers", json=_server_body(seeded_type), headers=_auth(token)
    ).json()

    r = client.patch(
        f"/admin/mcp-servers/{created['id']}", json={}, headers=_auth(token)
    )
    assert r.status_code == 200

    with sync_session_maker() as s:
        rows = s.execute(
            select(AuditLog).where(AuditLog.action == "mcp_server.update")
        ).all()
    assert rows == []


# ---------------------------------------------------------------------------
# Archive / unarchive
# ---------------------------------------------------------------------------


def test_archive_hides_from_default_list(
    client: TestClient, seeded_admin: tuple[UUID, str], seeded_type: UUID
) -> None:
    _, token = seeded_admin
    created = client.post(
        "/admin/mcp-servers", json=_server_body(seeded_type), headers=_auth(token)
    ).json()

    r = client.post(
        f"/admin/mcp-servers/{created['id']}/archive", headers=_auth(token)
    )
    assert r.status_code == 200
    assert r.json()["archived_at"] is not None

    body = client.get("/admin/mcp-servers", headers=_auth(token)).json()
    assert body["total"] == 0

    body = client.get(
        "/admin/mcp-servers?include_archived=true", headers=_auth(token)
    ).json()
    assert body["total"] == 1


def test_archive_idempotent_writes_one_audit(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    seeded_type: UUID,
    sync_session_maker: sessionmaker,
) -> None:
    _, token = seeded_admin
    created = client.post(
        "/admin/mcp-servers", json=_server_body(seeded_type), headers=_auth(token)
    ).json()
    client.post(f"/admin/mcp-servers/{created['id']}/archive", headers=_auth(token))
    client.post(f"/admin/mcp-servers/{created['id']}/archive", headers=_auth(token))

    with sync_session_maker() as s:
        rows = (
            s.execute(
                select(AuditLog).where(AuditLog.action == "mcp_server.archive")
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# KEK mismatch -> 503
# ---------------------------------------------------------------------------


def test_get_under_mismatched_kek_returns_503(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    seeded_type: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reading a server whose env_vars are encrypted under a different
    KEK than the active MASTER_KEY surfaces as 503 with a rotation
    hint — never as a 500."""
    _, token = seeded_admin
    created = client.post(
        "/admin/mcp-servers", json=_server_body(seeded_type), headers=_auth(token)
    ).json()

    # Swap MASTER_KEY without rotating.
    monkeypatch.setenv(
        "MASTER_KEY", base64.b64encode(b"\x66" * 32).decode("ascii")
    )
    from gargantua.settings import get_settings

    get_settings.cache_clear()

    r = client.get(
        f"/admin/mcp-servers/{created['id']}", headers=_auth(token)
    )
    assert r.status_code == 503
    assert "rotate" in r.json()["detail"].lower()

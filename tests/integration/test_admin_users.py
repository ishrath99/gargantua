"""Integration tests for the ``/admin/users/*`` routes.

Boots a minimal FastAPI app containing only the admin + auth routers, so
the JWT path and the DB path are real but the rest of the surface (Agno
AgentOS, CORS, etc.) is out of scope.

For each mutating route we verify:

* auth gates (no token → 401, user-only token → 403, admin token → 200).
* the route does what it says (CRUD effect persists in the DB).
* an audit log entry is written in the same transaction.
* domain errors map to the right HTTP status (404, 409, 422).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from gargantua.db.models import AuditLog, User


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
    truncate_db: Engine,  # noqa: ARG001 — wipe schema before each test
    _db_ready: str,
) -> Iterator[tuple[Path, Path]]:
    priv, pub = _write_keypair(tmp_path / "keys")
    monkeypatch.setenv("JWT_PRIVATE_KEY_PATH", str(priv))
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", str(pub))
    monkeypatch.setenv("JWT_ISSUER", "gargantua")
    monkeypatch.setenv("JWT_AUDIENCE", "gargantua")
    monkeypatch.setenv("JWT_ACCESS_TTL_SECONDS", "60")
    monkeypatch.setenv("DATABASE_URL_ASYNC", _db_ready)
    _reset_caches()
    yield priv, pub
    _reset_caches()


@pytest.fixture
def app(configured_env) -> FastAPI:
    """Minimal app mounting just the routers under test."""
    from gargantua.api.admin import router as admin_router
    from gargantua.api.auth import router as auth_router

    app = FastAPI()
    app.include_router(auth_router, prefix="/auth")
    app.include_router(admin_router, prefix="/admin")
    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def sync_session_maker(migrated_engine: Engine) -> sessionmaker:
    return sessionmaker(bind=migrated_engine, expire_on_commit=False, future=True)


@pytest.fixture
def seeded_admin(sync_session_maker) -> tuple[UUID, str]:
    """Seed an admin row and return ``(user_id, access_token)``."""
    from gargantua.auth import SCOPE_ADMIN, SCOPE_USER, mint_access_token
    from gargantua.auth.password import hash_password

    with sync_session_maker() as s:
        user = User(
            username="root",
            password_hash=hash_password("rootpw!1"),
            role="admin",
        )
        s.add(user)
        s.commit()
        s.refresh(user)
        token = mint_access_token(subject=str(user.id), scopes=[SCOPE_ADMIN, SCOPE_USER])
        return user.id, token


@pytest.fixture
def seeded_user(sync_session_maker) -> tuple[UUID, str]:
    """Seed a regular user and return ``(user_id, access_token)``."""
    from gargantua.auth import SCOPE_USER, mint_access_token
    from gargantua.auth.password import hash_password

    with sync_session_maker() as s:
        user = User(
            username="alice",
            password_hash=hash_password("alicepw1"),
            role="user",
        )
        s.add(user)
        s.commit()
        s.refresh(user)
        token = mint_access_token(subject=str(user.id), scopes=[SCOPE_USER])
        return user.id, token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Auth gates
# ---------------------------------------------------------------------------


def test_list_users_without_token_returns_401(client: TestClient) -> None:
    r = client.get("/admin/users")
    assert r.status_code == 401


def test_list_users_with_user_token_returns_403(
    client: TestClient, seeded_user: tuple[UUID, str]
) -> None:
    _, token = seeded_user
    r = client.get("/admin/users", headers=_auth(token))
    assert r.status_code == 403


def test_list_users_with_admin_token_returns_200(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    r = client.get("/admin/users", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["username"] == "root"
    # Password hash must never leak in API output.
    assert "password_hash" not in body["items"][0]


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def test_list_users_paginates_and_filters(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    from gargantua.auth.password import hash_password

    with sync_session_maker() as s:
        for i in range(5):
            s.add(
                User(
                    username=f"u{i}",
                    password_hash=hash_password("x"),
                    role="user",
                )
            )
        s.commit()

    _, token = seeded_admin

    r = client.get("/admin/users?role=user&page=1&page_size=2", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2

    r = client.get("/admin/users?search=u3", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["username"] == "u3"


def test_list_users_excludes_inactive_by_default(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    from gargantua.auth.password import hash_password

    with sync_session_maker() as s:
        s.add(
            User(
                username="dormant",
                password_hash=hash_password("x"),
                role="user",
                is_active=False,
            )
        )
        s.commit()

    _, token = seeded_admin
    r = client.get("/admin/users", headers=_auth(token))
    assert r.status_code == 200
    usernames = {u["username"] for u in r.json()["items"]}
    assert "dormant" not in usernames

    r = client.get("/admin/users?include_inactive=true", headers=_auth(token))
    usernames = {u["username"] for u in r.json()["items"]}
    assert "dormant" in usernames


# ---------------------------------------------------------------------------
# Get one
# ---------------------------------------------------------------------------


def test_get_user_returns_row(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    admin_id, token = seeded_admin
    r = client.get(f"/admin/users/{admin_id}", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == str(admin_id)
    assert body["username"] == "root"


def test_get_user_404_when_missing(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    from uuid import uuid4

    _, token = seeded_admin
    r = client.get(f"/admin/users/{uuid4()}", headers=_auth(token))
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_user_201_and_audit_logged(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    admin_id, token = seeded_admin
    body = {"username": "newbie", "password": "longpassword1", "role": "user"}
    r = client.post("/admin/users", json=body, headers=_auth(token))
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["username"] == "newbie"
    assert created["role"] == "user"
    assert created["is_active"] is True

    # Verify the DB has the user *and* the audit row.
    with sync_session_maker() as s:
        user = s.execute(
            select(User).where(User.username == "newbie")
        ).scalar_one()
        audit_rows = (
            s.execute(
                select(AuditLog)
                .where(AuditLog.target_id == user.id)
                .where(AuditLog.action == "user.create")
            )
            .scalars()
            .all()
        )
    assert len(audit_rows) == 1
    audit = audit_rows[0]
    assert audit.actor_id == admin_id
    assert audit.before is None
    assert audit.after["username"] == "newbie"
    assert "password_hash" not in audit.after  # never leak hashes into audit


def test_create_user_rejects_duplicate_username(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    body = {"username": "alice", "password": "longpassword1", "role": "user"}
    r1 = client.post("/admin/users", json=body, headers=_auth(token))
    assert r1.status_code == 201

    r2 = client.post("/admin/users", json=body, headers=_auth(token))
    assert r2.status_code == 409


def test_create_user_rejects_invalid_role(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    r = client.post(
        "/admin/users",
        json={"username": "bob", "password": "longpassword1", "role": "superhacker"},
        headers=_auth(token),
    )
    assert r.status_code == 422


def test_create_user_rejects_short_password(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    r = client.post(
        "/admin/users",
        json={"username": "bob", "password": "short", "role": "user"},
        headers=_auth(token),
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Update role
# ---------------------------------------------------------------------------


def test_update_role_changes_role_and_logs(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    from gargantua.auth.password import hash_password

    with sync_session_maker() as s:
        target = User(
            username="promote-me",
            password_hash=hash_password("x"),
            role="user",
        )
        s.add(target)
        s.commit()
        s.refresh(target)

    admin_id, token = seeded_admin
    r = client.patch(
        f"/admin/users/{target.id}/role",
        json={"role": "admin"},
        headers=_auth(token),
    )
    assert r.status_code == 200
    assert r.json()["role"] == "admin"

    with sync_session_maker() as s:
        audit = (
            s.execute(
                select(AuditLog)
                .where(AuditLog.action == "user.role_update")
                .where(AuditLog.target_id == target.id)
            )
            .scalars()
            .one()
        )
    assert audit.actor_id == admin_id
    assert audit.before["role"] == "user"
    assert audit.after["role"] == "admin"


def test_update_role_blocks_last_admin_demotion(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
) -> None:
    admin_id, token = seeded_admin
    r = client.patch(
        f"/admin/users/{admin_id}/role",
        json={"role": "user"},
        headers=_auth(token),
    )
    assert r.status_code == 409


def test_update_role_no_op_does_not_write_audit(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    """Setting the role to its current value writes no audit entry."""
    admin_id, token = seeded_admin
    r = client.patch(
        f"/admin/users/{admin_id}/role",
        json={"role": "admin"},
        headers=_auth(token),
    )
    assert r.status_code == 200

    with sync_session_maker() as s:
        count = s.execute(
            select(AuditLog).where(AuditLog.action == "user.role_update")
        ).all()
    assert count == []


# ---------------------------------------------------------------------------
# Deactivate / activate
# ---------------------------------------------------------------------------


def test_deactivate_user_blocks_login_and_logs(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    from gargantua.auth.password import hash_password

    with sync_session_maker() as s:
        victim = User(
            username="victim",
            password_hash=hash_password("victimpw1"),
            role="user",
        )
        s.add(victim)
        s.commit()
        s.refresh(victim)

    admin_id, token = seeded_admin
    r = client.post(
        f"/admin/users/{victim.id}/deactivate", headers=_auth(token)
    )
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is False

    # Login is now blocked.
    r = client.post(
        "/auth/login", json={"username": "victim", "password": "victimpw1"}
    )
    assert r.status_code == 401

    # Audit row was written.
    with sync_session_maker() as s:
        audit = (
            s.execute(
                select(AuditLog)
                .where(AuditLog.action == "user.deactivate")
                .where(AuditLog.target_id == victim.id)
            )
            .scalars()
            .one()
        )
    assert audit.actor_id == admin_id
    assert audit.before["is_active"] is True
    assert audit.after["is_active"] is False


def test_activate_user_restores_login(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    from gargantua.auth.password import hash_password

    with sync_session_maker() as s:
        victim = User(
            username="victim",
            password_hash=hash_password("victimpw1"),
            role="user",
            is_active=False,
        )
        s.add(victim)
        s.commit()
        s.refresh(victim)

    _, token = seeded_admin
    r = client.post(
        f"/admin/users/{victim.id}/activate", headers=_auth(token)
    )
    assert r.status_code == 200
    assert r.json()["is_active"] is True

    r = client.post(
        "/auth/login", json={"username": "victim", "password": "victimpw1"}
    )
    assert r.status_code == 200


def test_deactivate_last_admin_blocked(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    admin_id, token = seeded_admin
    r = client.post(
        f"/admin/users/{admin_id}/deactivate", headers=_auth(token)
    )
    assert r.status_code == 409


def test_deactivate_already_inactive_user_is_noop(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    """Deactivating an already-inactive user returns 200 with no audit write."""
    from gargantua.auth.password import hash_password

    with sync_session_maker() as s:
        u = User(
            username="dead",
            password_hash=hash_password("x"),
            role="user",
            is_active=False,
        )
        s.add(u)
        s.commit()
        s.refresh(u)

    _, token = seeded_admin
    r = client.post(f"/admin/users/{u.id}/deactivate", headers=_auth(token))
    assert r.status_code == 200

    with sync_session_maker() as s:
        audit_rows = (
            s.execute(
                select(AuditLog).where(AuditLog.action == "user.deactivate")
            )
            .scalars()
            .all()
        )
    assert audit_rows == []


def test_deactivate_unknown_user_returns_404(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    from uuid import uuid4

    _, token = seeded_admin
    r = client.post(f"/admin/users/{uuid4()}/deactivate", headers=_auth(token))
    assert r.status_code == 404

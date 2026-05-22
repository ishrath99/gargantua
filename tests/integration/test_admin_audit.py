"""Integration tests for ``/admin/audit/*`` — auth gates + filters + GET-one.

Most of the write paths are exercised indirectly via ``test_admin_users``
(every mutation there inserts an audit row).  This file focuses on the
*read* surface and the gating contract.
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
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from gargantua.db.models import User


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


@pytest.fixture
def app(configured_env) -> FastAPI:  # noqa: ARG001
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
        token = mint_access_token(subject=str(u.id), scopes=[SCOPE_ADMIN, SCOPE_USER])
        return u.id, token


@pytest.fixture
def seeded_user(sync_session_maker) -> tuple[UUID, str]:
    from gargantua.auth import SCOPE_USER, mint_access_token
    from gargantua.auth.password import hash_password

    with sync_session_maker() as s:
        u = User(
            username="alice",
            password_hash=hash_password("alicepw1"),
            role="user",
        )
        s.add(u)
        s.commit()
        s.refresh(u)
        token = mint_access_token(subject=str(u.id), scopes=[SCOPE_USER])
        return u.id, token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_audit_rows(
    client: TestClient, token: str
) -> tuple[UUID, UUID]:
    """Create two users via the API so audit rows exist for testing.

    Returns ``(first_user_id, second_user_id)``.
    """
    r1 = client.post(
        "/admin/users",
        json={"username": "u1", "password": "longpassword1", "role": "user"},
        headers=_auth(token),
    )
    r2 = client.post(
        "/admin/users",
        json={"username": "u2", "password": "longpassword2", "role": "user"},
        headers=_auth(token),
    )
    assert r1.status_code == 201 and r2.status_code == 201
    return UUID(r1.json()["id"]), UUID(r2.json()["id"])


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------


def test_list_audit_without_token_returns_401(client: TestClient) -> None:
    r = client.get("/admin/audit")
    assert r.status_code == 401


def test_list_audit_with_user_token_returns_403(
    client: TestClient, seeded_user: tuple[UUID, str]
) -> None:
    _, token = seeded_user
    r = client.get("/admin/audit", headers=_auth(token))
    assert r.status_code == 403


def test_list_audit_with_admin_token_returns_200(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    r = client.get("/admin/audit", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["items"] == []


# ---------------------------------------------------------------------------
# Listing + filters
# ---------------------------------------------------------------------------


def test_list_audit_newest_first(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    first, second = _seed_audit_rows(client, token)

    r = client.get("/admin/audit", headers=_auth(token))
    body = r.json()
    assert body["total"] == 2
    # Most recent (the second create) shows up first.
    assert body["items"][0]["target_id"] == str(second)
    assert body["items"][1]["target_id"] == str(first)


def test_list_audit_filters_by_action(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    first, _ = _seed_audit_rows(client, token)

    # Demote first user to admin then back to user so we get a role_update entry.
    client.patch(
        f"/admin/users/{first}/role", json={"role": "user"}, headers=_auth(token)
    )

    r = client.get("/admin/audit?action=user.create", headers=_auth(token))
    body = r.json()
    assert body["total"] == 2
    assert all(item["action"] == "user.create" for item in body["items"])


def test_list_audit_filters_by_target(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    first, _ = _seed_audit_rows(client, token)

    # Promote first user — gives that target_id a second audit entry.
    client.patch(
        f"/admin/users/{first}/role", json={"role": "admin"}, headers=_auth(token)
    )

    r = client.get(
        f"/admin/audit?target_type=user&target_id={first}", headers=_auth(token)
    )
    body = r.json()
    assert body["total"] == 2
    assert all(item["target_id"] == str(first) for item in body["items"])


def test_list_audit_filters_by_actor(
    client: TestClient,
    seeded_admin: tuple[UUID, str],
    sync_session_maker: sessionmaker,
) -> None:
    """Filter by actor_id should narrow the result set to that admin's actions."""
    from gargantua.auth import SCOPE_ADMIN, SCOPE_USER, mint_access_token
    from gargantua.auth.password import hash_password

    admin_id, token = seeded_admin
    _seed_audit_rows(client, token)  # 2 entries by `seeded_admin`

    # Bring up a second admin and have *them* create a user.
    with sync_session_maker() as s:
        other = User(
            username="other-admin",
            password_hash=hash_password("x"),
            role="admin",
        )
        s.add(other)
        s.commit()
        s.refresh(other)
        other_token = mint_access_token(
            subject=str(other.id), scopes=[SCOPE_ADMIN, SCOPE_USER]
        )
        other_id = other.id

    client.post(
        "/admin/users",
        json={"username": "u3", "password": "longpassword3", "role": "user"},
        headers=_auth(other_token),
    )

    r = client.get(f"/admin/audit?actor_id={admin_id}", headers=_auth(token))
    assert r.json()["total"] == 2

    r = client.get(f"/admin/audit?actor_id={other_id}", headers=_auth(token))
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["actor_id"] == str(other_id)


def test_list_audit_paginates(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    # Create 5 users → 5 audit rows.
    for i in range(5):
        client.post(
            "/admin/users",
            json={
                "username": f"u{i}",
                "password": "longpassword1",
                "role": "user",
            },
            headers=_auth(token),
        )

    r = client.get("/admin/audit?page=1&page_size=2", headers=_auth(token))
    body = r.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2

    r = client.get("/admin/audit?page=3&page_size=2", headers=_auth(token))
    body = r.json()
    assert body["total"] == 5
    assert len(body["items"]) == 1


# ---------------------------------------------------------------------------
# GET one
# ---------------------------------------------------------------------------


def test_get_audit_entry_returns_full_diff(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    first, _ = _seed_audit_rows(client, token)

    # Find the audit row id via the list endpoint.
    list_body = client.get("/admin/audit", headers=_auth(token)).json()
    entry_id = next(
        row["id"] for row in list_body["items"] if row["target_id"] == str(first)
    )

    r = client.get(f"/admin/audit/{entry_id}", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["action"] == "user.create"
    assert body["target_type"] == "user"
    assert body["target_id"] == str(first)
    assert body["before"] is None
    assert body["after"]["username"] == "u1"
    # Audit payloads must never include the password hash.
    assert "password_hash" not in body["after"]


def test_get_audit_entry_404_when_missing(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    r = client.get("/admin/audit/999999", headers=_auth(token))
    assert r.status_code == 404


def test_get_audit_entry_requires_admin(
    client: TestClient, seeded_user: tuple[UUID, str]
) -> None:
    _, token = seeded_user
    r = client.get("/admin/audit/1", headers=_auth(token))
    # 403 (forbidden) takes precedence over 404 since require_admin runs
    # before the handler that would surface the missing row.
    assert r.status_code == 403

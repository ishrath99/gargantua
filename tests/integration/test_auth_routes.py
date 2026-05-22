"""End-to-end tests for ``/auth/login``, ``/auth/refresh``, ``/auth/me``.

Every test runs against the real Postgres provided by the integration
conftest:

* ``migrated_engine`` (session) — schemas reset + Alembic upgrade applied once.
* ``truncate_db`` (per-test)    — every ``gargantua_app.*`` table truncated before the test.

The FastAPI app under test is constructed inline so we can swap settings
(JWT keys, DSN, etc.) via env vars without touching the production
``gargantua.main`` entry point.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from gargantua.db.models import User

# ---------------------------------------------------------------------------
# Helpers + fixtures
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
) -> tuple[Path, Path]:
    """Point Settings at the test DB + a fresh keypair, return the keypair."""
    priv, pub = _write_keypair(tmp_path / "keys")
    monkeypatch.setenv("JWT_PRIVATE_KEY_PATH", str(priv))
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", str(pub))
    monkeypatch.setenv("JWT_ISSUER", "gargantua")
    monkeypatch.setenv("JWT_AUDIENCE", "gargantua")
    monkeypatch.setenv("JWT_ACCESS_TTL_SECONDS", "60")
    monkeypatch.setenv("JWT_REFRESH_TTL_SECONDS", "600")
    monkeypatch.setenv("DATABASE_URL_ASYNC", _db_ready)
    _reset_caches()
    yield priv, pub
    _reset_caches()


@pytest.fixture
def app(configured_env: tuple[Path, Path]) -> FastAPI:
    """Build a minimal app that only mounts the auth router."""
    from gargantua.api.auth import router as auth_router

    app = FastAPI()
    app.include_router(auth_router, prefix="/auth", tags=["auth"])
    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def sync_session_maker(migrated_engine: Engine) -> sessionmaker:
    return sessionmaker(bind=migrated_engine, expire_on_commit=False, future=True)


def _seed_user(sm: sessionmaker, *, username: str, password: str, role: str) -> User:
    from gargantua.auth.password import hash_password

    with sm() as session:
        user = User(
            username=username,
            password_hash=hash_password(password),
            role=role,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


# ---------------------------------------------------------------------------
# /auth/login
# ---------------------------------------------------------------------------


def test_login_success_returns_access_and_refresh(
    client: TestClient, sync_session_maker: sessionmaker
) -> None:
    _seed_user(sync_session_maker, username="alice", password="hunter22!", role="user")

    r = client.post("/auth/login", json={"username": "alice", "password": "hunter22!"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["expires_in"] > 0


def test_login_admin_token_carries_admin_scope(
    client: TestClient, sync_session_maker: sessionmaker
) -> None:
    from gargantua.auth import SCOPE_ADMIN, SCOPE_USER, decode_token

    _seed_user(sync_session_maker, username="root", password="s3cret!", role="admin")
    r = client.post("/auth/login", json={"username": "root", "password": "s3cret!"})
    assert r.status_code == 200

    claims = decode_token(r.json()["access_token"])
    assert SCOPE_ADMIN in claims["scopes"]
    assert SCOPE_USER in claims["scopes"]


def test_login_user_role_only_carries_user_scope(
    client: TestClient, sync_session_maker: sessionmaker
) -> None:
    from gargantua.auth import SCOPE_ADMIN, SCOPE_USER, decode_token

    _seed_user(sync_session_maker, username="alice", password="hunter22!", role="user")
    r = client.post("/auth/login", json={"username": "alice", "password": "hunter22!"})
    claims = decode_token(r.json()["access_token"])
    assert claims["scopes"] == [SCOPE_USER]
    assert SCOPE_ADMIN not in claims["scopes"]


def test_login_wrong_password_returns_401(
    client: TestClient, sync_session_maker: sessionmaker
) -> None:
    _seed_user(sync_session_maker, username="alice", password="hunter22!", role="user")
    r = client.post("/auth/login", json={"username": "alice", "password": "wrong"})
    assert r.status_code == 401


def test_login_unknown_user_returns_401(client: TestClient) -> None:
    r = client.post("/auth/login", json={"username": "ghost", "password": "x"})
    assert r.status_code == 401


def test_login_missing_fields_returns_422(client: TestClient) -> None:
    r = client.post("/auth/login", json={"username": "alice"})
    assert r.status_code == 422


def test_login_refuses_inactive_user_with_generic_error(
    client: TestClient, sync_session_maker: sessionmaker
) -> None:
    """Deactivated users get the same 'Invalid credentials' response as
    a wrong password, so the route can't be used to enumerate inactive
    accounts.
    """
    user = _seed_user(sync_session_maker, username="ghost", password="hunter22!", role="user")
    with sync_session_maker() as s:
        row = s.get(User, user.id)
        row.is_active = False
        s.commit()

    r = client.post("/auth/login", json={"username": "ghost", "password": "hunter22!"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid credentials"


# ---------------------------------------------------------------------------
# /auth/refresh
# ---------------------------------------------------------------------------


def test_refresh_with_valid_refresh_token_returns_new_pair(
    client: TestClient, sync_session_maker: sessionmaker
) -> None:
    _seed_user(sync_session_maker, username="alice", password="hunter22!", role="user")
    login = client.post("/auth/login", json={"username": "alice", "password": "hunter22!"}).json()

    # Sleep 1s so iat differs and the new access_token byte-string is distinct.
    time.sleep(1)
    r = client.post("/auth/refresh", json={"refresh_token": login["refresh_token"]})
    assert r.status_code == 200, r.text
    refreshed = r.json()
    assert refreshed["token_type"] == "bearer"
    assert refreshed["access_token"]
    assert refreshed["refresh_token"]
    # The new access token is genuinely fresh, not the old one echoed back.
    assert refreshed["access_token"] != login["access_token"]


def test_refresh_with_access_token_returns_401(
    client: TestClient, sync_session_maker: sessionmaker
) -> None:
    _seed_user(sync_session_maker, username="alice", password="hunter22!", role="user")
    login = client.post("/auth/login", json={"username": "alice", "password": "hunter22!"}).json()

    # An access token must NOT be accepted on /refresh — only refresh tokens.
    r = client.post("/auth/refresh", json={"refresh_token": login["access_token"]})
    assert r.status_code == 401


def test_refresh_with_garbage_token_returns_401(client: TestClient) -> None:
    r = client.post("/auth/refresh", json={"refresh_token": "not-a-jwt"})
    assert r.status_code == 401


def test_refresh_for_deleted_user_returns_401(
    client: TestClient, sync_session_maker: sessionmaker
) -> None:
    user = _seed_user(sync_session_maker, username="alice", password="hunter22!", role="user")
    login = client.post("/auth/login", json={"username": "alice", "password": "hunter22!"}).json()

    # Hard-delete the user out from under the still-valid refresh token.
    with sync_session_maker() as session:
        session.query(User).filter(User.id == user.id).delete()
        session.commit()

    r = client.post("/auth/refresh", json={"refresh_token": login["refresh_token"]})
    assert r.status_code == 401


def test_refresh_for_deactivated_user_returns_401(
    client: TestClient, sync_session_maker: sessionmaker
) -> None:
    """Once the user is deactivated, *outstanding* refresh tokens must stop working."""
    user = _seed_user(sync_session_maker, username="alice", password="hunter22!", role="user")
    login = client.post("/auth/login", json={"username": "alice", "password": "hunter22!"}).json()

    with sync_session_maker() as s:
        row = s.get(User, user.id)
        row.is_active = False
        s.commit()

    r = client.post("/auth/refresh", json={"refresh_token": login["refresh_token"]})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# /auth/me
# ---------------------------------------------------------------------------


def test_me_requires_authentication(client: TestClient) -> None:
    r = client.get("/auth/me")
    assert r.status_code == 401


def test_me_returns_user_payload(client: TestClient, sync_session_maker: sessionmaker) -> None:
    user = _seed_user(sync_session_maker, username="alice", password="hunter22!", role="user")
    login = client.post("/auth/login", json={"username": "alice", "password": "hunter22!"}).json()

    r = client.get("/auth/me", headers={"Authorization": f"Bearer {login['access_token']}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == str(user.id)
    assert body["username"] == "alice"
    assert body["role"] == "user"

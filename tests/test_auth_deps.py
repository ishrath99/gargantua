"""Tests for ``gargantua.auth.deps`` — Bearer-token guards.

We mount a tiny test app per scenario rather than rely on the real app
factory; the focus here is the deps' behaviour, not route wiring.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _generate_keypair(out: Path) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = out / "jwt_private.pem"
    pub = out / "jwt_public.pem"
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


@pytest.fixture
def jwt_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    priv, pub = _generate_keypair(tmp_path / "keys")
    monkeypatch.setenv("JWT_PRIVATE_KEY_PATH", str(priv))
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", str(pub))
    monkeypatch.setenv("JWT_ISSUER", "gargantua")
    monkeypatch.setenv("JWT_AUDIENCE", "gargantua")

    from gargantua.auth import tokens
    from gargantua.settings import get_settings

    get_settings.cache_clear()
    tokens.reset_keys_cache()
    return priv, pub


@pytest.fixture
def test_app() -> FastAPI:
    """Tiny FastAPI app exposing one endpoint per guard."""
    from gargantua.auth.deps import (
        TokenClaims,
        get_current_claims,
        require_admin,
        require_user,
    )

    app = FastAPI()

    @app.get("/whoami")
    def whoami(claims: TokenClaims = Depends(get_current_claims)) -> dict[str, object]:
        return {"sub": claims.sub, "scopes": list(claims.scopes)}

    @app.get("/user-only")
    def user_only(claims: TokenClaims = Depends(require_user)) -> dict[str, str]:
        return {"sub": claims.sub}

    @app.get("/admin-only")
    def admin_only(claims: TokenClaims = Depends(require_admin)) -> dict[str, str]:
        return {"sub": claims.sub}

    return app


@pytest.fixture
def client(test_app: FastAPI) -> TestClient:
    return TestClient(test_app)


@pytest.fixture
def admin_token(jwt_keys: tuple[Path, Path]) -> str:
    from gargantua.auth.tokens import mint_access_token

    return mint_access_token(subject="admin-user-id", scopes=["agent_os:admin", "agent_os:user"])


@pytest.fixture
def user_token(jwt_keys: tuple[Path, Path]) -> str:
    from gargantua.auth.tokens import mint_access_token

    return mint_access_token(subject="alice-id", scopes=["agent_os:user"])


@pytest.fixture
def refresh_token(jwt_keys: tuple[Path, Path]) -> str:
    from gargantua.auth.tokens import mint_refresh_token

    return mint_refresh_token(subject="alice-id")


# ---------------------------------------------------------------------------
# get_current_claims
# ---------------------------------------------------------------------------


def test_missing_auth_header_returns_401(client: TestClient) -> None:
    r = client.get("/whoami")
    assert r.status_code == 401
    assert r.headers.get("www-authenticate", "").lower().startswith("bearer")


def test_non_bearer_scheme_returns_401(client: TestClient, user_token: str) -> None:
    r = client.get("/whoami", headers={"Authorization": f"Basic {user_token}"})
    assert r.status_code == 401


def test_empty_bearer_token_returns_401(client: TestClient) -> None:
    r = client.get("/whoami", headers={"Authorization": "Bearer "})
    assert r.status_code == 401


def test_garbage_token_returns_401(client: TestClient, jwt_keys: tuple[Path, Path]) -> None:
    r = client.get("/whoami", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


def test_refresh_token_rejected_on_protected_route(client: TestClient, refresh_token: str) -> None:
    """A refresh token must never be accepted as an access token."""
    r = client.get("/whoami", headers={"Authorization": f"Bearer {refresh_token}"})
    assert r.status_code == 401


def test_valid_access_token_returns_claims(client: TestClient, user_token: str) -> None:
    r = client.get("/whoami", headers={"Authorization": f"Bearer {user_token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["sub"] == "alice-id"
    assert "agent_os:user" in body["scopes"]


# ---------------------------------------------------------------------------
# require_user
# ---------------------------------------------------------------------------


def test_require_user_accepts_user_scope(client: TestClient, user_token: str) -> None:
    r = client.get("/user-only", headers={"Authorization": f"Bearer {user_token}"})
    assert r.status_code == 200
    assert r.json() == {"sub": "alice-id"}


def test_require_user_accepts_admin_scope(client: TestClient, admin_token: str) -> None:
    r = client.get("/user-only", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    assert r.json() == {"sub": "admin-user-id"}


def test_require_user_rejects_token_without_either_scope(
    jwt_keys: tuple[Path, Path], client: TestClient
) -> None:
    from gargantua.auth.tokens import mint_access_token

    no_scope_token = mint_access_token(subject="ghost", scopes=[])
    r = client.get("/user-only", headers={"Authorization": f"Bearer {no_scope_token}"})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# require_admin
# ---------------------------------------------------------------------------


def test_require_admin_accepts_admin_token(client: TestClient, admin_token: str) -> None:
    r = client.get("/admin-only", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    assert r.json() == {"sub": "admin-user-id"}


def test_require_admin_rejects_user_token(client: TestClient, user_token: str) -> None:
    r = client.get("/admin-only", headers={"Authorization": f"Bearer {user_token}"})
    assert r.status_code == 403

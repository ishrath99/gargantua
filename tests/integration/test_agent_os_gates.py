"""AgentOS is mounted under ``/v1`` and gated by our self-issued JWTs.

These tests verify the wiring contract — *not* AgentOS internals:

* ``/v1/agents`` requires a Bearer token (401 without).
* An admin-scoped token (``agent_os:admin``) is accepted (200).
* A user-only token (``agent_os:user``) is rejected by Agno's RBAC (403).
* Tokens with the wrong ``aud`` are rejected (401).
* The parent app's ``/auth/*`` and ``/health`` routes remain unprotected
  (no chicken-and-egg on login).

We construct the production app via :func:`gargantua.main.create_app` so the
full wiring path — including the ``/v1`` mount — gets exercised end-to-end.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine


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


def _reset_module_caches() -> None:
    from gargantua.auth import tokens
    from gargantua.db import session as session_module
    from gargantua.settings import get_settings

    get_settings.cache_clear()
    tokens.reset_keys_cache()
    session_module.get_engine.cache_clear()
    session_module.get_session_factory.cache_clear()


@pytest.fixture
def configured_app(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    truncate_db: Engine,  # noqa: ARG001 — wipes the schema
    _db_ready: str,
) -> TestClient:
    """Boot ``gargantua.main.create_app`` with a fresh keypair and DB."""
    priv, pub = _write_keypair(tmp_path / "keys")
    monkeypatch.setenv("JWT_PRIVATE_KEY_PATH", str(priv))
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", str(pub))
    monkeypatch.setenv("JWT_ISSUER", "gargantua")
    monkeypatch.setenv("JWT_AUDIENCE", "gargantua")
    monkeypatch.setenv("JWT_ACCESS_TTL_SECONDS", "60")
    monkeypatch.setenv("DATABASE_URL_ASYNC", _db_ready)
    # Strip the +psycopg driver for Agno's sync engine.
    sync_dsn = _db_ready.replace("postgresql+psycopg://", "postgresql://", 1)
    monkeypatch.setenv("DATABASE_URL", sync_dsn)
    _reset_module_caches()

    from gargantua.main import create_app

    app = create_app()
    with TestClient(app) as client:
        yield client

    _reset_module_caches()


# ---------------------------------------------------------------------------
# Route gating
# ---------------------------------------------------------------------------


def test_v1_agents_without_auth_returns_401(configured_app: TestClient) -> None:
    r = configured_app.get("/v1/agents")
    assert r.status_code == 401


def test_v1_agents_with_admin_token_returns_200(configured_app: TestClient) -> None:
    from gargantua.auth import SCOPE_ADMIN, SCOPE_USER, mint_access_token

    token = mint_access_token(
        subject="admin-id", scopes=[SCOPE_ADMIN, SCOPE_USER]
    )
    r = configured_app.get(
        "/v1/agents", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text
    # No agents are registered by default — list should be empty.
    assert r.json() == []


def test_v1_agents_with_user_only_token_returns_403(
    configured_app: TestClient,
) -> None:
    """A token that lacks any agents:* scope is denied by Agno's RBAC."""
    from gargantua.auth import SCOPE_USER, mint_access_token

    token = mint_access_token(subject="alice-id", scopes=[SCOPE_USER])
    r = configured_app.get(
        "/v1/agents", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 403


def test_v1_agents_with_garbage_token_returns_401(
    configured_app: TestClient,
) -> None:
    r = configured_app.get(
        "/v1/agents", headers={"Authorization": "Bearer not-a-jwt"}
    )
    assert r.status_code == 401


def test_v1_agents_with_wrong_audience_returns_401(
    configured_app: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A token minted for a different audience must be rejected."""
    from gargantua.auth import SCOPE_ADMIN, mint_access_token

    # Override the audience setting to mint a token aimed elsewhere, then
    # restore the original setting (so the server still checks against
    # the legitimate audience).
    from gargantua.auth import tokens
    from gargantua.settings import get_settings

    monkeypatch.setenv("JWT_AUDIENCE", "some-other-app")
    get_settings.cache_clear()
    tokens.reset_keys_cache()
    bad_token = mint_access_token(subject="x", scopes=[SCOPE_ADMIN])

    # Restore so the server still expects "gargantua".
    monkeypatch.setenv("JWT_AUDIENCE", "gargantua")
    get_settings.cache_clear()
    tokens.reset_keys_cache()

    r = configured_app.get(
        "/v1/agents", headers={"Authorization": f"Bearer {bad_token}"}
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Parent-app routes stay unprotected
# ---------------------------------------------------------------------------


def test_health_remains_unprotected(configured_app: TestClient) -> None:
    """The /v1 JWT middleware must not reach back to /health."""
    r = configured_app.get("/health")
    assert r.status_code == 200


def test_auth_login_remains_unprotected(configured_app: TestClient) -> None:
    """No chicken-and-egg: /auth/login is reachable without a token."""
    # We expect 401 (bad credentials) not 401 (missing token).  Either way the
    # route is reachable; the FastAPI route logic ran (which is what we're
    # checking), not the Agno JWT middleware (which would return a different
    # response shape).
    r = configured_app.post(
        "/auth/login", json={"username": "ghost", "password": "x"}
    )
    assert r.status_code == 401
    # The detail comes from our route, not Agno's middleware.
    assert r.json()["detail"] == "Invalid credentials"

"""End-to-end smoke test using the real ``gargantua.main.create_app`` factory.

Boots the production app (including the lifespan hook that bootstraps the
first admin), then walks the full happy-path:

  1. ``GET  /health``       — alive
  2. ``POST /auth/login``   — admin credentials succeed
  3. ``GET  /auth/me``      — returns the bootstrapped admin row

This catches the wiring bugs that single-route tests miss: router prefixes,
middleware ordering, lifespan side-effects, etc.
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


def test_full_bootstrap_login_me_flow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    truncate_db: Engine,
    _db_ready: str,
) -> None:
    priv, pub = _write_keypair(tmp_path / "keys")
    monkeypatch.setenv("JWT_PRIVATE_KEY_PATH", str(priv))
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", str(pub))
    monkeypatch.setenv("JWT_ISSUER", "gargantua")
    monkeypatch.setenv("JWT_AUDIENCE", "gargantua")
    monkeypatch.setenv("JWT_ACCESS_TTL_SECONDS", "60")
    monkeypatch.setenv("DATABASE_URL_ASYNC", _db_ready)
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "root")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "rootpw!1")
    _reset_module_caches()

    from gargantua.main import create_app

    app = create_app()
    with TestClient(app) as client:
        # 1. /health
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

        # 2. login with the bootstrapped admin credentials
        r = client.post("/auth/login", json={"username": "root", "password": "rootpw!1"})
        assert r.status_code == 200, r.text
        tokens_pair = r.json()
        access = tokens_pair["access_token"]

        # 3. /auth/me echoes the admin row
        r = client.get("/auth/me", headers={"Authorization": f"Bearer {access}"})
        assert r.status_code == 200, r.text
        me = r.json()
        assert me["username"] == "root"
        assert me["role"] == "admin"

    _reset_module_caches()

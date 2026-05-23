"""Integration tests for ``/admin/agent-templates/*``.

These routes are read-only and admin-gated.  They surface the package's
shipped seeds (see ``src/gargantua/seeds/agents/``) so the admin UI can
populate a "New from template" picker.  No DB writes, no audit rows;
just file IO behind a JWT check.

Coverage:

* Auth gating: 401 without token, 403 with user-only token, 200 with
  admin token.
* List endpoint reflects the shipped seeds (asserted by slug so adding
  a new seed surfaces here intentionally).
* Get-by-slug returns the full template body, 404 on unknown slug.
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
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from gargantua.db.models import User

# ---------------------------------------------------------------------------
# Fixtures (mirror admin test pattern)
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
        u = User(username="root", password_hash=hash_password("rootpw!1"), role="admin")
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


# Expected seeds — keep in sync with src/gargantua/seeds/agents/*.md.
# Adding a new shipped seed should require an intentional update here.
_EXPECTED_SLUGS = {
    "api-explorer",
    "db-investigator",
    "logs-explorer",
    "triage-lead",
}


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------


def test_list_templates_without_token_returns_401(client: TestClient) -> None:
    r = client.get("/api/admin/agent-templates")
    assert r.status_code == 401


def test_list_templates_with_user_token_returns_403(
    client: TestClient, seeded_user: tuple[UUID, str]
) -> None:
    """User-only tokens can't see the template catalog — it's an
    admin-only flow (only admins create agents)."""
    _, token = seeded_user
    r = client.get("/api/admin/agent-templates", headers=_auth(token))
    assert r.status_code == 403


def test_get_template_without_token_returns_401(client: TestClient) -> None:
    r = client.get("/api/admin/agent-templates/db-investigator")
    assert r.status_code == 401


def test_get_template_with_user_token_returns_403(
    client: TestClient, seeded_user: tuple[UUID, str]
) -> None:
    _, token = seeded_user
    r = client.get("/api/admin/agent-templates/db-investigator", headers=_auth(token))
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


def test_list_templates_returns_shipped_seeds(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    r = client.get("/api/admin/agent-templates", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    slugs = {item["slug"] for item in body["items"]}
    assert _EXPECTED_SLUGS <= slugs
    # ``total`` reflects what's actually returned (no pagination
    # discrepancy possible since this endpoint doesn't paginate).
    assert body["total"] == len(body["items"])


def test_list_templates_response_shape(client: TestClient, seeded_admin: tuple[UUID, str]) -> None:
    """Every item must have the full set of fields the UI relies on,
    including ``instructions`` (yes, even in the list — the catalog
    is small enough that one request is cheaper than N round-trips)."""
    _, token = seeded_admin
    r = client.get("/api/admin/agent-templates", headers=_auth(token))
    body = r.json()
    for item in body["items"]:
        for key in (
            "slug",
            "name",
            "description",
            "model",
            "suggested_mcp_server_type_slugs",
            "agent_config",
            "instructions",
        ):
            assert key in item, f"missing key {key!r} on template {item.get('slug')}"


def test_list_templates_is_sorted_by_slug(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    """Stable order is part of the contract: the UI lists templates in
    response order and operators expect alphabetical."""
    _, token = seeded_admin
    body = client.get("/api/admin/agent-templates", headers=_auth(token)).json()
    slugs = [item["slug"] for item in body["items"]]
    assert slugs == sorted(slugs)


# ---------------------------------------------------------------------------
# Get by slug
# ---------------------------------------------------------------------------


def test_get_template_by_slug_returns_full_body(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    r = client.get("/api/admin/agent-templates/db-investigator", headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == "db-investigator"
    assert body["name"] == "DB Investigator"
    # Body should be substantial — at least the first heading of the
    # shipped template.
    assert "Role" in body["instructions"]
    # And it should reference the suggested MCP type.
    assert "postgres" in body["suggested_mcp_server_type_slugs"]


def test_get_template_unknown_slug_returns_404(
    client: TestClient, seeded_admin: tuple[UUID, str]
) -> None:
    _, token = seeded_admin
    r = client.get("/api/admin/agent-templates/no-such-template", headers=_auth(token))
    assert r.status_code == 404

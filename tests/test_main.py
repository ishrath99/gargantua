"""FastAPI app stub: import + /health + static UI mount."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gargantua import __version__
from gargantua.main import app, create_app
from gargantua.settings import get_settings


def test_create_app_returns_fastapi_instance() -> None:
    fresh = create_app()
    assert fresh.title == "gargantua"
    assert fresh.version == __version__


def test_health_endpoint_returns_200_with_status_payload() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["runtime_env"] in {"dev", "prd", "prod", "production", "staging", "test"}


def test_openapi_json_is_reachable() -> None:
    with TestClient(app) as client:
        response = client.get("/openapi.json")
    assert response.status_code == 200
    spec = response.json()
    assert spec["info"]["title"] == "gargantua"


# ---------------------------------------------------------------------------
# Static UI mount
# ---------------------------------------------------------------------------


def _seed_static_export(root: Path) -> None:
    """Lay down a minimum Next.js-style static export tree.

    Mirrors ``trailingSlash: true``: every route is a directory containing
    an ``index.html`` plus a static assets directory at ``/_next``.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text("<!doctype html><title>HOME</title>")
    (root / "admin").mkdir(exist_ok=True)
    (root / "admin" / "index.html").write_text(
        "<!doctype html><title>ADMIN</title>"
    )
    assets = root / "_next" / "static" / "css"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "app.css").write_text("body{color:#000}")


def test_static_mount_serves_index_when_directory_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out = tmp_path / "ui_out"
    _seed_static_export(out)
    monkeypatch.setenv("UI_STATIC_ROOT", str(out))
    get_settings.cache_clear()

    with TestClient(create_app()) as client:
        # Root → index.html via Starlette's ``html=True`` directory resolution.
        r = client.get("/")
        assert r.status_code == 200
        assert "HOME" in r.text
        assert "text/html" in r.headers["content-type"]

        # Nested route → its own index.html.
        r = client.get("/admin/")
        assert r.status_code == 200
        assert "ADMIN" in r.text

        # Static asset is reachable.
        r = client.get("/_next/static/css/app.css")
        assert r.status_code == 200
        assert "color:#000" in r.text

        # API surface is untouched by the mount.
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

        # Unknown deep path → 404 (no SPA-style catch-all; Next emits a
        # file per route and an unknown one shouldn't pretend to exist).
        r = client.get("/this/does/not/exist")
        assert r.status_code == 404


def test_no_static_mount_when_directory_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("UI_STATIC_ROOT", str(tmp_path / "no_such_dir"))
    get_settings.cache_clear()

    with TestClient(create_app()) as client:
        # /health still works.
        assert client.get("/health").status_code == 200
        # / is not served (no UI mount, no root route).
        assert client.get("/").status_code == 404

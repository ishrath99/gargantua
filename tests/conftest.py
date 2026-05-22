"""Shared pytest fixtures.

Keep this file as the *only* place fixtures live so individual test modules
stay focused on assertions rather than setup boilerplate.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner


@pytest.fixture(autouse=True)
def _isolate_settings_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Stop any user-level ``.env`` from leaking into tests.

    Forces every test to see a known-clean environment.  Individual tests
    can still ``monkeypatch.setenv`` to set specific values.
    """
    # Point pydantic-settings at a non-existent .env so its file loader is a no-op.
    monkeypatch.chdir(tmp_path)
    # Strip Gargantua-flavoured vars from the inherited shell env.
    for key in list(os.environ):
        if key.startswith(
            (
                "APP_",
                "DATABASE_",
                "LLM_",
                "MASTER_",
                "JWT_",
                "BOOTSTRAP_",
                "MCP_CACHE_",
                "CORS_",
                "RUNTIME_",
            )
        ):
            monkeypatch.delenv(key, raising=False)
    # Drop the in-process Settings singleton so each test re-reads env.
    from gargantua.settings import get_settings

    get_settings.cache_clear()


@pytest.fixture
def cli_runner() -> CliRunner:
    """Typer CliRunner for invoking the admin CLI in-process."""
    return CliRunner()


@pytest.fixture
def keys_dir(tmp_path: Path) -> Iterator[Path]:
    """Per-test directory for generated JWT keys."""
    d = tmp_path / "keys"
    d.mkdir()
    yield d

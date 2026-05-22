"""Settings load + override behaviour."""

from __future__ import annotations

import pytest

from gargantua.settings import Settings, get_settings


def test_defaults_load_when_env_is_clean() -> None:
    s = Settings()
    assert s.app_host == "0.0.0.0"
    assert s.app_port == 7777
    assert s.runtime_env == "dev"
    assert s.is_prod is False
    assert s.cors_origin_list == ["http://localhost:3000"]


def test_runtime_env_prod_marks_is_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNTIME_ENV", "prd")
    s = Settings()
    assert s.is_prod is True


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("http://localhost:3000", ["http://localhost:3000"]),
        (
            "http://localhost:3000,https://app.example.com",
            ["http://localhost:3000", "https://app.example.com"],
        ),
        ("  http://a , http://b  ,", ["http://a", "http://b"]),
        ("", []),
    ],
)
def test_cors_origins_parses_to_list(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: list[str]
) -> None:
    monkeypatch.setenv("CORS_ORIGINS", raw)
    s = Settings()
    assert s.cors_origin_list == expected


def test_env_overrides_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pw@db:5432/test")
    s = Settings()
    assert s.database_url == "postgresql+psycopg://user:pw@db:5432/test"


def test_get_settings_returns_cached_singleton() -> None:
    a = get_settings()
    b = get_settings()
    assert a is b


def test_jwt_ttls_are_integers_with_sensible_defaults() -> None:
    s = Settings()
    assert s.jwt_access_ttl_seconds == 43_200
    assert s.jwt_refresh_ttl_seconds == 2_592_000
    assert s.mcp_cache_idle_ttl_seconds == 300


def test_agno_debug_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production safety: prompts and tool args are sensitive, so debug
    logging must stay off unless the operator opts in.

    Skip the workspace ``.env`` (which a debugging operator may have left
    with ``AGNO_DEBUG=true``) and ensure no inherited env var leaks in,
    so this test asserts the *defined default* rather than the developer's
    local state.
    """
    monkeypatch.delenv("AGNO_DEBUG", raising=False)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.agno_debug is False


@pytest.mark.parametrize("raw", ["true", "True", "1", "yes", "on"])
def test_agno_debug_truthy_env_enables(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    """Pydantic-settings accepts several truthy spellings for booleans;
    documenting the ones we promise to honour."""
    monkeypatch.setenv("AGNO_DEBUG", raw)
    s = Settings()
    assert s.agno_debug is True


@pytest.mark.parametrize("raw", ["false", "False", "0", "no", "off"])
def test_agno_debug_falsy_env_stays_off(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    """Explicit falsy values must not flip debug on.

    An *empty* string is rejected by pydantic-settings's boolean
    parser (it has no defined truthiness), so the operator has to
    either omit the var entirely (default ``False``) or set one of
    the values in the parameterized list above.
    """
    monkeypatch.setenv("AGNO_DEBUG", raw)
    s = Settings()
    assert s.agno_debug is False

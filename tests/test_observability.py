"""Phoenix tracing setup: opt-in behaviour + graceful degradation."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from gargantua import observability
from gargantua.settings import Settings


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset the module's init guard and clear Phoenix env each test.

    ``setup_phoenix_tracing`` installs a *global* tracer provider and
    latches ``_TRACING_INITIALIZED`` so repeat calls are no-ops.  Tests
    need a clean slate, and we don't want a value in the developer's
    real environment leaking in.
    """
    monkeypatch.setattr(observability, "_TRACING_INITIALIZED", False)
    monkeypatch.delenv("PHOENIX_COLLECTOR_ENDPOINT", raising=False)
    monkeypatch.delenv("PHOENIX_API_KEY", raising=False)


def _settings(**overrides: Any) -> Settings:
    # ``_env_file=None`` so a developer's local .env can't flip these on/off.
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def test_disabled_when_endpoint_blank() -> None:
    active = observability.setup_phoenix_tracing(_settings(phoenix_collector_endpoint=""))
    assert active is False
    assert observability._TRACING_INITIALIZED is False
    assert "PHOENIX_COLLECTOR_ENDPOINT" not in __import__("os").environ


def test_enabled_registers_and_sets_env(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}

    def fake_register(**kwargs: Any) -> None:
        calls.update(kwargs)

    fake_module = types.ModuleType("phoenix.otel")
    fake_module.register = fake_register  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "phoenix.otel", fake_module)

    active = observability.setup_phoenix_tracing(
        _settings(
            phoenix_collector_endpoint="http://localhost:6006",
            phoenix_api_key="secret-key",
            phoenix_project_name="my-project",
        )
    )

    import os

    assert active is True
    assert observability._TRACING_INITIALIZED is True
    assert os.environ["PHOENIX_COLLECTOR_ENDPOINT"] == "http://localhost:6006"
    assert os.environ["PHOENIX_API_KEY"] == "secret-key"
    assert calls == {"project_name": "my-project", "auto_instrument": True}


def test_endpoint_whitespace_is_treated_as_blank() -> None:
    active = observability.setup_phoenix_tracing(_settings(phoenix_collector_endpoint="   "))
    assert active is False


def test_missing_package_degrades_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    """No tracing packages installed must not raise or latch init on."""

    def _raise(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "phoenix.otel":
            raise ImportError("no phoenix")
        return orig_import(name, *args, **kwargs)

    import builtins

    orig_import = builtins.__import__
    monkeypatch.setattr(builtins, "__import__", _raise)

    active = observability.setup_phoenix_tracing(
        _settings(phoenix_collector_endpoint="http://localhost:6006")
    )
    assert active is False
    assert observability._TRACING_INITIALIZED is False


def test_second_call_is_noop_once_initialized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(observability, "_TRACING_INITIALIZED", True)
    # Endpoint set, but the guard should short-circuit before any import.
    active = observability.setup_phoenix_tracing(
        _settings(phoenix_collector_endpoint="http://localhost:6006")
    )
    assert active is True

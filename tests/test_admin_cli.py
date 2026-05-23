"""Admin CLI: keygen subcommands.

Stub commands (rotate-kek, seed-catalog) have moved to the integration
suite now that they have real behaviour.
"""

from __future__ import annotations

import base64
import stat
from pathlib import Path

import jwt
from typer.testing import CliRunner

from gargantua.admin import app


def test_root_help_lists_all_commands(cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in (
        "generate-master-key",
        "generate-jwt-keys",
        "rotate-kek",
        "seed-catalog",
    ):
        assert command in result.stdout


# ---------------------------------------------------------------------------
# generate-master-key
# ---------------------------------------------------------------------------


def test_generate_master_key_raw_emits_only_base64(cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(app, ["generate-master-key", "--raw"])
    assert result.exit_code == 0

    encoded = result.stdout.strip()
    assert "\n" not in encoded, "raw output should be a single line"

    raw_bytes = base64.b64decode(encoded)
    assert len(raw_bytes) == 32, "KEK must be exactly 32 bytes (AES-256)"


def test_generate_master_key_verbose_includes_env_hint(cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(app, ["generate-master-key"])
    assert result.exit_code == 0
    assert "MASTER_KEY=" in result.stdout
    assert "WARNING" in result.stdout


def test_two_generations_produce_different_keys(cli_runner: CliRunner) -> None:
    a = cli_runner.invoke(app, ["generate-master-key", "--raw"]).stdout.strip()
    b = cli_runner.invoke(app, ["generate-master-key", "--raw"]).stdout.strip()
    assert a != b, "KEK generation must use cryptographic randomness"


# ---------------------------------------------------------------------------
# generate-jwt-keys
# ---------------------------------------------------------------------------


def test_generate_jwt_keys_writes_pem_pair(cli_runner: CliRunner, keys_dir: Path) -> None:
    result = cli_runner.invoke(app, ["generate-jwt-keys", "--out-dir", str(keys_dir)])
    assert result.exit_code == 0, result.stdout

    private = keys_dir / "jwt_private.pem"
    public = keys_dir / "jwt_public.pem"
    assert private.is_file()
    assert public.is_file()

    private_text = private.read_text()
    public_text = public.read_text()
    assert private_text.startswith("-----BEGIN PRIVATE KEY-----")
    assert private_text.rstrip().endswith("-----END PRIVATE KEY-----")
    assert public_text.startswith("-----BEGIN PUBLIC KEY-----")
    assert public_text.rstrip().endswith("-----END PUBLIC KEY-----")


def test_generate_jwt_keys_sets_restrictive_permissions(
    cli_runner: CliRunner, keys_dir: Path
) -> None:
    cli_runner.invoke(app, ["generate-jwt-keys", "--out-dir", str(keys_dir)])
    private = keys_dir / "jwt_private.pem"
    public = keys_dir / "jwt_public.pem"
    assert stat.S_IMODE(private.stat().st_mode) == 0o600
    assert stat.S_IMODE(public.stat().st_mode) == 0o644


def test_generated_keys_can_sign_and_verify_rs256(cli_runner: CliRunner, keys_dir: Path) -> None:
    cli_runner.invoke(app, ["generate-jwt-keys", "--out-dir", str(keys_dir)])
    private = (keys_dir / "jwt_private.pem").read_bytes()
    public = (keys_dir / "jwt_public.pem").read_bytes()

    claims = {
        "sub": "alice",
        "scopes": ["agent_os:admin"],
        "iss": "gargantua",
        "aud": "gargantua",
    }
    token = jwt.encode(claims, private, algorithm="RS256")
    decoded = jwt.decode(
        token,
        public,
        algorithms=["RS256"],
        audience="gargantua",
        issuer="gargantua",
    )
    assert decoded == claims


def test_generate_jwt_keys_refuses_overwrite_without_force(
    cli_runner: CliRunner, keys_dir: Path
) -> None:
    first = cli_runner.invoke(app, ["generate-jwt-keys", "--out-dir", str(keys_dir)])
    assert first.exit_code == 0
    second = cli_runner.invoke(app, ["generate-jwt-keys", "--out-dir", str(keys_dir)])
    assert second.exit_code != 0
    assert "Refusing to overwrite" in second.stdout


def test_generate_jwt_keys_overwrites_with_force(cli_runner: CliRunner, keys_dir: Path) -> None:
    cli_runner.invoke(app, ["generate-jwt-keys", "--out-dir", str(keys_dir)])
    old_priv = (keys_dir / "jwt_private.pem").read_bytes()

    result = cli_runner.invoke(app, ["generate-jwt-keys", "--out-dir", str(keys_dir), "--force"])
    assert result.exit_code == 0

    new_priv = (keys_dir / "jwt_private.pem").read_bytes()
    assert new_priv != old_priv


def test_generate_jwt_keys_rejects_undersized_key(cli_runner: CliRunner, keys_dir: Path) -> None:
    result = cli_runner.invoke(
        app,
        ["generate-jwt-keys", "--out-dir", str(keys_dir), "--key-size", "1024"],
    )
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# stubs
# ---------------------------------------------------------------------------


# All previously-stub commands (rotate-kek, seed-catalog) now have real
# behaviour exercised in the integration suite (tests/integration/test_*).

"""Tests for ``gargantua.auth.tokens`` — settings-coupled JWT helpers."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _generate_keypair(tmp_path: Path) -> tuple[Path, Path]:
    """Write a fresh 2048-bit RS256 keypair to *tmp_path*; return ``(priv, pub)``."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_path = tmp_path / "jwt_private.pem"
    pub_path = tmp_path / "jwt_public.pem"
    priv_path.write_bytes(
        private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    pub_path.write_bytes(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return priv_path, pub_path


@pytest.fixture
def jwt_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    """Provision real RS256 keys on disk and point Settings at them."""
    priv, pub = _generate_keypair(tmp_path)
    monkeypatch.setenv("JWT_PRIVATE_KEY_PATH", str(priv))
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", str(pub))
    monkeypatch.setenv("JWT_ISSUER", "gargantua")
    monkeypatch.setenv("JWT_AUDIENCE", "gargantua")

    # Drop the settings + key caches so the next call picks up the new env.
    from gargantua.auth import tokens
    from gargantua.settings import get_settings

    get_settings.cache_clear()
    tokens.reset_keys_cache()
    return priv, pub


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_mint_access_token_carries_expected_claims(
    jwt_keys: tuple[Path, Path],
) -> None:
    from gargantua.auth import tokens

    token = tokens.mint_access_token(subject="alice", scopes=["agent_os:user"])
    claims = tokens.decode_token(token)

    assert claims["sub"] == "alice"
    assert claims["typ"] == "access"
    assert claims["scopes"] == ["agent_os:user"]
    assert claims["iss"] == "gargantua"
    assert claims["aud"] == "gargantua"
    # exp must be in the future and within the configured TTL window.
    now = int(time.time())
    assert claims["exp"] > now
    assert claims["exp"] - claims["iat"] <= 43_200 + 5  # default 12h, ±5s slack


def test_mint_refresh_token_has_no_scopes_and_refresh_typ(
    jwt_keys: tuple[Path, Path],
) -> None:
    from gargantua.auth import tokens

    token = tokens.mint_refresh_token(subject="alice")
    claims = tokens.decode_token(token)

    assert claims["typ"] == "refresh"
    assert "scopes" not in claims
    # Refresh TTL is 30 days by default.
    assert claims["exp"] - claims["iat"] >= 86_400


def test_decode_rejects_wrong_issuer(
    jwt_keys: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from gargantua.auth import tokens
    from gargantua.auth.jwt import InvalidToken
    from gargantua.settings import get_settings

    token = tokens.mint_access_token(subject="alice", scopes=["agent_os:user"])

    # Rotate the expected issuer underneath the same public key.
    monkeypatch.setenv("JWT_ISSUER", "someone-else")
    get_settings.cache_clear()

    with pytest.raises(InvalidToken):
        tokens.decode_token(token)


def test_decode_rejects_expired_token(
    jwt_keys: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from gargantua.auth import tokens
    from gargantua.auth.jwt import InvalidToken
    from gargantua.settings import get_settings

    monkeypatch.setenv("JWT_ACCESS_TTL_SECONDS", "1")
    get_settings.cache_clear()
    tokens.reset_keys_cache()

    token = tokens.mint_access_token(subject="alice", scopes=["agent_os:user"])
    # Sleep past the leeway baked into ``gargantua.auth.jwt.decode``.
    time.sleep(12)

    with pytest.raises(InvalidToken):
        tokens.decode_token(token)


def test_reset_keys_cache_picks_up_rotated_keys(
    jwt_keys: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from gargantua.auth import tokens
    from gargantua.auth.jwt import InvalidToken
    from gargantua.settings import get_settings

    token = tokens.mint_access_token(subject="alice", scopes=["agent_os:user"])
    assert tokens.decode_token(token)["sub"] == "alice"

    # Provision a *different* keypair and point Settings at it.
    new_priv, new_pub = _generate_keypair(tmp_path / "rotated")
    monkeypatch.setenv("JWT_PRIVATE_KEY_PATH", str(new_priv))
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", str(new_pub))
    get_settings.cache_clear()
    tokens.reset_keys_cache()

    # Old token must now be unverifiable.
    with pytest.raises(InvalidToken):
        tokens.decode_token(token)

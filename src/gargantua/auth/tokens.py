"""Settings-coupled wrappers around :mod:`gargantua.auth.jwt`.

These are the helpers route handlers actually call.  The pure functions in
``auth.jwt`` deliberately take every trust anchor as a keyword argument; this
module pulls them from :class:`gargantua.settings.Settings` so the call sites
stay short and the key file is read at most once per process.

Public surface:

* :func:`mint_access_token`   — sign a new ``typ=access`` token.
* :func:`mint_refresh_token`  — sign a new ``typ=refresh`` token.
* :func:`decode_token`        — verify-and-decode a token.
* :func:`reset_keys_cache`    — drop the on-disk-key caches (tests + rotation).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from gargantua.auth import jwt as _jwt
from gargantua.settings import get_settings


@lru_cache(maxsize=1)
def _private_key() -> bytes:
    """Lazy-load the RS256 signing key from disk."""
    path = get_settings().jwt_private_key_path
    return path.read_bytes()


@lru_cache(maxsize=1)
def _public_key() -> bytes:
    """Lazy-load the RS256 verification key from disk."""
    path = get_settings().jwt_public_key_path
    return path.read_bytes()


def mint_access_token(*, subject: str, scopes: list[str]) -> str:
    """Sign and return an access token for *subject* with *scopes*."""
    s = get_settings()
    return _jwt.mint_access(
        subject=subject,
        scopes=scopes,
        private_key=_private_key(),
        ttl_seconds=s.jwt_access_ttl_seconds,
        issuer=s.jwt_issuer,
        audience=s.jwt_audience,
    )


def mint_refresh_token(*, subject: str) -> str:
    """Sign and return a refresh token for *subject* (no scopes)."""
    s = get_settings()
    return _jwt.mint_refresh(
        subject=subject,
        private_key=_private_key(),
        ttl_seconds=s.jwt_refresh_ttl_seconds,
        issuer=s.jwt_issuer,
        audience=s.jwt_audience,
    )


def decode_token(token: str) -> dict[str, Any]:
    """Verify and decode *token*.  Raises :class:`gargantua.auth.jwt.InvalidToken`."""
    s = get_settings()
    return _jwt.decode(
        token,
        public_key=_public_key(),
        issuer=s.jwt_issuer,
        audience=s.jwt_audience,
    )


def reset_keys_cache() -> None:
    """Drop the in-process key caches.

    Call from a key-rotation admin command, and from test fixtures that swap
    the on-disk keypair underneath a running process.
    """
    _private_key.cache_clear()
    _public_key.cache_clear()

"""RS256 access + refresh tokens.

Three pure helpers wrap PyJWT.  They take *all* trust-anchor inputs (keys,
issuer, audience, TTL) as arguments — settings-coupled wrappers live in
``gargantua.auth.tokens`` once the settings layer is wired up.

Token shape (access):
    {
        "sub":   "<user-id>",
        "scopes": ["agent_os:admin", "agent_os:user", ...],
        "iss":   "gargantua",
        "aud":   "gargantua",
        "typ":   "access",
        "iat":   <epoch>,
        "exp":   <epoch>
    }

Refresh tokens drop ``scopes`` and set ``"typ": "refresh"``.  The split
prevents a leaked refresh token from being accepted as an access token
(the API layer asserts ``typ == "access"`` on every protected request).
"""

from __future__ import annotations

import time
from typing import Any, Final

import jwt as _pyjwt
from jwt.exceptions import PyJWTError

#: Acceptable amount of clock skew between this process and the verifier.
_LEEWAY_SECONDS: Final[int] = 10


class InvalidToken(Exception):
    """Raised when a token fails signature/audience/issuer/expiry validation."""


def _mint(
    *,
    subject: str,
    private_key: bytes | str,
    ttl_seconds: int,
    issuer: str,
    audience: str,
    typ: str,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": subject,
        "iss": issuer,
        "aud": audience,
        "typ": typ,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    if extra_claims:
        claims.update(extra_claims)
    return _pyjwt.encode(claims, private_key, algorithm="RS256")


def mint_access(
    *,
    subject: str,
    scopes: list[str],
    private_key: bytes | str,
    ttl_seconds: int,
    issuer: str,
    audience: str,
) -> str:
    """Sign and return an RS256 access token (``typ=access``)."""
    return _mint(
        subject=subject,
        private_key=private_key,
        ttl_seconds=ttl_seconds,
        issuer=issuer,
        audience=audience,
        typ="access",
        extra_claims={"scopes": list(scopes)},
    )


def mint_refresh(
    *,
    subject: str,
    private_key: bytes | str,
    ttl_seconds: int,
    issuer: str,
    audience: str,
) -> str:
    """Sign and return an RS256 refresh token (``typ=refresh``, no scopes)."""
    return _mint(
        subject=subject,
        private_key=private_key,
        ttl_seconds=ttl_seconds,
        issuer=issuer,
        audience=audience,
        typ="refresh",
    )


def decode(
    token: str,
    *,
    public_key: bytes | str,
    issuer: str,
    audience: str,
    leeway: int = _LEEWAY_SECONDS,
) -> dict[str, Any]:
    """Verify and decode *token*.  Raises ``InvalidToken`` on any failure.

    *leeway* (seconds) is the tolerance applied to the ``exp`` / ``iat`` /
    ``nbf`` claims to absorb small clock-skew between minter and verifier.
    Tests that want exact expiry semantics pass ``leeway=0``.
    """
    try:
        return _pyjwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience=audience,
            issuer=issuer,
            leeway=leeway,
            options={
                "require": ["sub", "iss", "aud", "exp", "iat", "typ"],
            },
        )
    except PyJWTError as exc:
        # Bundle every PyJWT-internal error into one app-level exception so
        # callers don't have to import jwt.exceptions.
        raise InvalidToken(str(exc)) from exc

"""FastAPI dependencies: Bearer-token extraction + scope-based guards.

Three deps form the auth pipeline:

* :func:`get_current_claims` — verifies the Authorization header, decodes the
  JWT, and returns a typed :class:`TokenClaims`.  Raises ``401`` on every
  failure mode (missing header, wrong scheme, bad signature, wrong issuer,
  expired, or refresh-token presented to a protected route).
* :func:`require_user`       — passes when the caller has either the user or
  admin scope.  Raises ``403`` otherwise.
* :func:`require_admin`      — passes only on the admin scope.  Raises ``403``
  otherwise.

Scope strings live in :data:`SCOPE_ADMIN` / :data:`SCOPE_USER` so route layers
and the AgentOS authorization config can share the same constants.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Final

from fastapi import Depends, Header, HTTPException, status

from gargantua.auth.jwt import InvalidToken
from gargantua.auth.tokens import decode_token

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCOPE_ADMIN: Final[str] = "agent_os:admin"
SCOPE_USER: Final[str] = "agent_os:user"

#: Token type that protected routes accept.  Refresh tokens carry ``typ=refresh``
#: and must be rejected here — otherwise a stolen refresh token grants access.
_ACCESS_TYPE: Final[str] = "access"


# ---------------------------------------------------------------------------
# Claims
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class TokenClaims:
    """Verified claims from an access token.

    Routes that need to know the caller's identity inject this via
    ``Depends(get_current_claims)`` (or one of the guard variants below)
    instead of re-decoding the token themselves.
    """

    sub: str
    scopes: tuple[str, ...]
    typ: str
    iss: str
    aud: str
    iat: int
    exp: int

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


# ---------------------------------------------------------------------------
# Exception factories
# ---------------------------------------------------------------------------


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _forbidden(detail: str = "Insufficient scope") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=detail,
    )


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def get_current_claims(
    authorization: Annotated[str | None, Header()] = None,
) -> TokenClaims:
    """Verify the Bearer token and return its claims.

    Raises ``401`` for every authentication-shaped failure: missing header,
    wrong scheme, malformed/expired/wrong-issuer token, or a refresh token
    presented to a protected route.
    """
    if not authorization:
        raise _unauthorized()

    scheme, _, raw_token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not raw_token.strip():
        raise _unauthorized("Invalid Authorization header")

    try:
        claims = decode_token(raw_token.strip())
    except InvalidToken as exc:
        raise _unauthorized(f"Invalid token: {exc}") from exc

    if claims.get("typ") != _ACCESS_TYPE:
        raise _unauthorized("Token is not an access token")

    return TokenClaims(
        sub=str(claims["sub"]),
        scopes=tuple(claims.get("scopes") or ()),
        typ=str(claims["typ"]),
        iss=str(claims["iss"]),
        aud=str(claims["aud"]),
        iat=int(claims["iat"]),
        exp=int(claims["exp"]),
    )


def require_user(
    claims: Annotated[TokenClaims, Depends(get_current_claims)],
) -> TokenClaims:
    """Pass when the caller has the user *or* admin scope; ``403`` otherwise."""
    if not (claims.has_scope(SCOPE_USER) or claims.has_scope(SCOPE_ADMIN)):
        raise _forbidden()
    return claims


def require_admin(
    claims: Annotated[TokenClaims, Depends(get_current_claims)],
) -> TokenClaims:
    """Pass only on the admin scope; ``403`` otherwise."""
    if not claims.has_scope(SCOPE_ADMIN):
        raise _forbidden()
    return claims

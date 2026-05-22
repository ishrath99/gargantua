"""Authentication: argon2 password hashing, RS256 JWT mint/verify, FastAPI deps."""

from gargantua.auth.deps import (
    SCOPE_ADMIN,
    SCOPE_USER,
    TokenClaims,
    get_current_claims,
    require_admin,
    require_user,
)
from gargantua.auth.jwt import InvalidToken
from gargantua.auth.password import hash_password, needs_rehash, verify_password
from gargantua.auth.tokens import (
    decode_token,
    mint_access_token,
    mint_refresh_token,
    reset_keys_cache,
)

__all__ = [
    "SCOPE_ADMIN",
    "SCOPE_USER",
    "TokenClaims",
    "InvalidToken",
    "get_current_claims",
    "require_admin",
    "require_user",
    "hash_password",
    "needs_rehash",
    "verify_password",
    "mint_access_token",
    "mint_refresh_token",
    "decode_token",
    "reset_keys_cache",
]

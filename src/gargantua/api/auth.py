"""Auth router: ``POST /login``, ``POST /refresh``, ``GET /me``.

Login flow:

  client -> POST /auth/login {username, password}
         <- 200 {access_token, refresh_token, token_type, expires_in}

The access token carries scopes derived from the user's role
(``admin`` -> ``[SCOPE_ADMIN, SCOPE_USER]``; everyone else -> ``[SCOPE_USER]``).
Refresh tokens carry no scopes; ``/auth/refresh`` re-derives them from the
user record so a role change takes effect on the next refresh without
needing the refresh token to be re-minted.

All three routes use the same async session dependency so a single
connection-pool fronts the DB.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from gargantua.auth import (
    SCOPE_ADMIN,
    SCOPE_USER,
    InvalidToken,
    TokenClaims,
    decode_token,
    mint_access_token,
    mint_refresh_token,
    require_user,
    verify_password,
)
from gargantua.auth.password import hash_password, needs_rehash
from gargantua.db.models import User
from gargantua.db.session import get_session
from gargantua.repo import users as users_repo
from gargantua.settings import get_settings

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class MeResponse(BaseModel):
    id: str
    username: str
    role: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scopes_for_role(role: str) -> list[str]:
    """Map a stored user role to the JWT scopes the access token should carry."""
    if role == "admin":
        return [SCOPE_ADMIN, SCOPE_USER]
    return [SCOPE_USER]


async def _get_user_by_username(session: AsyncSession, username: str) -> User | None:
    return await users_repo.aget_by_username(session, username)


async def _get_user_by_id(session: AsyncSession, user_id: UUID) -> User | None:
    return await users_repo.aget_by_id(session, user_id)


def _build_token_pair(user: User) -> TokenPair:
    scopes = _scopes_for_role(user.role)
    access = mint_access_token(subject=str(user.id), scopes=scopes)
    refresh = mint_refresh_token(subject=str(user.id))
    return TokenPair(
        access_token=access,
        refresh_token=refresh,
        token_type="bearer",
        expires_in=get_settings().jwt_access_ttl_seconds,
    )


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/login", response_model=TokenPair)
async def login(
    body: LoginRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenPair:
    """Exchange a username + password for an access/refresh token pair."""
    user = await _get_user_by_username(session, body.username)
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        # One generic response for every failure mode (unknown user, bad
        # password, deactivated account) so the route can't be used to
        # enumerate valid usernames or active accounts.
        raise _unauthorized("Invalid credentials")

    # Opportunistic rehash: if argon2 parameters have tightened since this
    # hash was created, replace it now while we have the plaintext at hand.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
        await session.commit()

    return _build_token_pair(user)


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    body: RefreshRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenPair:
    """Trade a valid refresh token for a fresh access/refresh pair."""
    try:
        claims = decode_token(body.refresh_token)
    except InvalidToken as exc:
        raise _unauthorized(f"Invalid refresh token: {exc}") from exc

    if claims.get("typ") != "refresh":
        raise _unauthorized("Token is not a refresh token")

    try:
        user_id = UUID(str(claims["sub"]))
    except (KeyError, ValueError) as exc:
        raise _unauthorized("Refresh token has no valid subject") from exc

    user = await _get_user_by_id(session, user_id)
    if user is None or not user.is_active:
        # Token was minted for a user who has since been deleted or
        # deactivated.  Generic message — client should return to /login.
        raise _unauthorized("Invalid refresh token")

    return _build_token_pair(user)


@router.get("/me", response_model=MeResponse)
async def me(
    claims: Annotated[TokenClaims, Depends(require_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MeResponse:
    """Return the calling user's row."""
    try:
        user_id = UUID(claims.sub)
    except ValueError as exc:
        raise _unauthorized("Access token has no valid subject") from exc

    user = await _get_user_by_id(session, user_id)
    if user is None or not user.is_active:
        raise _unauthorized("User no longer exists")

    return MeResponse(id=str(user.id), username=user.username, role=user.role)

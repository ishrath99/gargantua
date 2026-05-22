"""Repository for ``gargantua_app.users`` — sync + async accessors.

Why sync *and* async? The HTTP routes run inside FastAPI's async event
loop and want an ``AsyncSession``; the admin CLI runs synchronously and
wants a plain ``Session``.  Rather than ship two implementations, every
function here ships as a sync version that takes a ``Session`` and an
``a*`` mirror that takes an ``AsyncSession``.  Both delegate to the same
SQL.

Domain errors are typed:

* :class:`DuplicateUsername` — INSERT raced against an existing row.
* :class:`UserNotFound`     — the target user_id doesn't exist.
* :class:`InvalidRole`      — caller passed a role not in
                              ``{"admin", "user"}``.
* :class:`LastAdminError`   — the requested change would leave zero
                              active admins (system lockout guard).

Callers translate these into HTTP responses or CLI exit codes.
"""

from __future__ import annotations

from typing import Final
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from gargantua.auth.password import hash_password
from gargantua.db.models import User


VALID_ROLES: Final[frozenset[str]] = frozenset({"admin", "user"})


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RepoError(Exception):
    """Base class for typed errors raised by ``gargantua.repo``."""


class DuplicateUsername(RepoError):
    """Raised when an INSERT would collide with an existing username."""


class UserNotFound(RepoError):
    """Raised when an operation references a user_id that doesn't exist."""


class InvalidRole(RepoError):
    """Raised when ``role`` is not in :data:`VALID_ROLES`."""


class LastAdminError(RepoError):
    """Raised when an operation would leave the system without an active admin."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_role(role: str) -> None:
    if role not in VALID_ROLES:
        raise InvalidRole(
            f"role must be one of {sorted(VALID_ROLES)}, got {role!r}"
        )


def _build_list_query(
    *,
    role: str | None,
    search: str | None,
    include_inactive: bool,
):
    stmt = select(User)
    count_stmt = select(func.count()).select_from(User)

    if role is not None:
        stmt = stmt.where(User.role == role)
        count_stmt = count_stmt.where(User.role == role)

    if not include_inactive:
        stmt = stmt.where(User.is_active.is_(True))
        count_stmt = count_stmt.where(User.is_active.is_(True))

    if search:
        # Case-insensitive substring search on username (sufficient for an
        # admin console; full-text isn't worth the indexing cost here).
        pattern = f"%{search.lower()}%"
        stmt = stmt.where(func.lower(User.username).like(pattern))
        count_stmt = count_stmt.where(func.lower(User.username).like(pattern))

    stmt = stmt.order_by(User.username.asc())
    return stmt, count_stmt


def _other_active_admin_count_query(*, excluding: UUID):
    """Count *other* admins that are currently active.

    Used by both ``set_role`` and ``set_active`` to determine whether the
    change being proposed would leave the system with zero active admins.
    """
    return (
        select(func.count())
        .select_from(User)
        .where(
            User.role == "admin",
            User.is_active.is_(True),
            User.id != excluding,
        )
    )


# ---------------------------------------------------------------------------
# Sync API (used by the CLI and integration tests)
# ---------------------------------------------------------------------------


def get_by_id(session: Session, user_id: UUID) -> User | None:
    return session.get(User, user_id)


def get_by_username(session: Session, username: str) -> User | None:
    return session.execute(
        select(User).where(User.username == username)
    ).scalar_one_or_none()


def list_users(
    session: Session,
    *,
    page: int = 1,
    page_size: int = 50,
    role: str | None = None,
    search: str | None = None,
    include_inactive: bool = False,
) -> tuple[list[User], int]:
    """Paginated list of users + total matching count."""
    if page < 1 or page_size < 1:
        raise ValueError("page and page_size must be >= 1")

    stmt, count_stmt = _build_list_query(
        role=role, search=search, include_inactive=include_inactive
    )
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = list(session.execute(stmt).scalars().all())
    total = session.execute(count_stmt).scalar_one()
    return rows, total


def create_user(
    session: Session,
    *,
    username: str,
    password: str,
    role: str,
) -> User:
    """Insert a new user (hashing the password first).

    Flushes the session so a uniqueness violation surfaces immediately as
    :class:`DuplicateUsername` rather than at commit time.  Refreshes the
    row so server-computed columns (``id``, ``is_active``, ``created_at``,
    ``updated_at``) are populated on the returned instance — callers that
    feed the result into Pydantic ``model_validate`` would otherwise hit
    a lazy-load on first attribute access.  The caller is expected to
    ``commit()`` once they've recorded the corresponding audit entry in
    the same transaction.
    """
    _validate_role(role)
    user = User(
        username=username,
        password_hash=hash_password(password),
        role=role,
    )
    session.add(user)
    try:
        session.flush()
    except IntegrityError as exc:  # uq_users_username
        session.rollback()
        if "uq_users_username" in str(exc.orig) or "users_username" in str(exc.orig):
            raise DuplicateUsername(username) from exc
        raise
    session.refresh(user)
    return user


def set_role(session: Session, *, user_id: UUID, new_role: str) -> User:
    _validate_role(new_role)
    user = session.get(User, user_id)
    if user is None:
        raise UserNotFound(str(user_id))

    if user.role == "admin" and new_role != "admin":
        # Demoting an admin — must be at least one *other* active admin.
        others = session.execute(
            _other_active_admin_count_query(excluding=user_id)
        ).scalar_one()
        if others == 0:
            raise LastAdminError(
                "Refusing to demote the last active admin; promote another "
                "user to admin first."
            )

    user.role = new_role
    session.flush()
    # Refresh so server-side ``onupdate=now()`` lands on the instance and
    # Pydantic ``model_validate(user)`` doesn't trip a lazy-load.
    session.refresh(user)
    return user


def set_active(session: Session, *, user_id: UUID, is_active: bool) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise UserNotFound(str(user_id))

    if not is_active and user.role == "admin" and user.is_active:
        # Deactivating an active admin — must be at least one other.
        others = session.execute(
            _other_active_admin_count_query(excluding=user_id)
        ).scalar_one()
        if others == 0:
            raise LastAdminError(
                "Refusing to deactivate the last active admin; activate or "
                "promote another admin first."
            )

    user.is_active = is_active
    session.flush()
    session.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Async API (used by the HTTP routes)
# ---------------------------------------------------------------------------


async def aget_by_id(session: AsyncSession, user_id: UUID) -> User | None:
    return await session.get(User, user_id)


async def aget_by_username(session: AsyncSession, username: str) -> User | None:
    result = await session.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def alist_users(
    session: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 50,
    role: str | None = None,
    search: str | None = None,
    include_inactive: bool = False,
) -> tuple[list[User], int]:
    if page < 1 or page_size < 1:
        raise ValueError("page and page_size must be >= 1")

    stmt, count_stmt = _build_list_query(
        role=role, search=search, include_inactive=include_inactive
    )
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = list((await session.execute(stmt)).scalars().all())
    total = (await session.execute(count_stmt)).scalar_one()
    return rows, total


async def acreate_user(
    session: AsyncSession,
    *,
    username: str,
    password: str,
    role: str,
) -> User:
    _validate_role(role)
    user = User(
        username=username,
        password_hash=hash_password(password),
        role=role,
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        if "uq_users_username" in str(exc.orig) or "users_username" in str(exc.orig):
            raise DuplicateUsername(username) from exc
        raise
    # Pull server-computed columns onto the instance so the caller can
    # hand the row straight to ``UserOut.model_validate`` without tripping
    # a lazy-load outside the async context.
    await session.refresh(user)
    return user


async def aset_role(
    session: AsyncSession, *, user_id: UUID, new_role: str
) -> User:
    _validate_role(new_role)
    user = await session.get(User, user_id)
    if user is None:
        raise UserNotFound(str(user_id))

    if user.role == "admin" and new_role != "admin":
        others = (
            await session.execute(_other_active_admin_count_query(excluding=user_id))
        ).scalar_one()
        if others == 0:
            raise LastAdminError(
                "Refusing to demote the last active admin; promote another "
                "user to admin first."
            )

    user.role = new_role
    await session.flush()
    # ``onupdate=func.now()`` expires ``updated_at`` after flush; refresh
    # so Pydantic doesn't lazy-load it from sync ``model_validate``.
    await session.refresh(user)
    return user


async def aset_active(
    session: AsyncSession, *, user_id: UUID, is_active: bool
) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise UserNotFound(str(user_id))

    if not is_active and user.role == "admin" and user.is_active:
        others = (
            await session.execute(_other_active_admin_count_query(excluding=user_id))
        ).scalar_one()
        if others == 0:
            raise LastAdminError(
                "Refusing to deactivate the last active admin; activate or "
                "promote another admin first."
            )

    user.is_active = is_active
    await session.flush()
    await session.refresh(user)
    return user

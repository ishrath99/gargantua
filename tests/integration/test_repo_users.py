"""Repo layer for ``gargantua_app.users`` — focuses on the *tricky* logic.

The happy-path CRUD is also exercised by ``tests/integration/test_admin_users.py``
through HTTP; this file isolates:

* duplicate-username detection (race-safe via integrity error round-trip),
* the "last active admin" guard on role demotion and deactivation,
* password hashing on create.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from gargantua.db.models import User


@pytest.fixture
def sync_session_maker(truncate_db: Engine) -> sessionmaker:
    return sessionmaker(bind=truncate_db, expire_on_commit=False, future=True)


# ---------------------------------------------------------------------------
# create_user
# ---------------------------------------------------------------------------


def test_create_user_hashes_password_and_persists(sync_session_maker) -> None:
    from gargantua.auth.password import verify_password
    from gargantua.repo.users import create_user

    with sync_session_maker() as s:
        user = create_user(s, username="alice", password="hunter2!", role="user")
        s.commit()

    with sync_session_maker() as s:
        row = s.execute(select(User).where(User.username == "alice")).scalar_one()
    assert row.role == "user"
    assert row.is_active is True
    assert row.password_hash.startswith("$argon2id$")
    assert verify_password("hunter2!", row.password_hash) is True


def test_create_user_rejects_duplicate_username(sync_session_maker) -> None:
    from gargantua.repo.users import DuplicateUsername, create_user

    with sync_session_maker() as s:
        create_user(s, username="alice", password="x", role="user")
        s.commit()

    with sync_session_maker() as s:
        with pytest.raises(DuplicateUsername):
            create_user(s, username="alice", password="y", role="admin")


def test_create_user_rejects_invalid_role(sync_session_maker) -> None:
    from gargantua.repo.users import InvalidRole, create_user

    with sync_session_maker() as s:
        with pytest.raises(InvalidRole):
            create_user(s, username="bob", password="x", role="superhacker")


# ---------------------------------------------------------------------------
# set_role / set_active — last-admin guard
# ---------------------------------------------------------------------------


def _seed_admin(s, *, username: str = "root", is_active: bool = True):
    from gargantua.repo.users import create_user

    user = create_user(s, username=username, password="x", role="admin")
    user.is_active = is_active
    return user


def test_set_role_demotion_to_user_blocked_when_last_admin(
    sync_session_maker,
) -> None:
    from gargantua.repo.users import LastAdminError, set_role

    with sync_session_maker() as s:
        admin = _seed_admin(s, username="root")
        s.commit()

    with sync_session_maker() as s:
        admin = s.execute(select(User).where(User.username == "root")).scalar_one()
        with pytest.raises(LastAdminError):
            set_role(s, user_id=admin.id, new_role="user")


def test_set_role_demotion_allowed_when_other_admin_exists(
    sync_session_maker,
) -> None:
    from gargantua.repo.users import set_role

    with sync_session_maker() as s:
        _seed_admin(s, username="root")
        _seed_admin(s, username="backup")
        s.commit()

    with sync_session_maker() as s:
        root = s.execute(select(User).where(User.username == "root")).scalar_one()
        set_role(s, user_id=root.id, new_role="user")
        s.commit()

    with sync_session_maker() as s:
        row = s.execute(select(User).where(User.username == "root")).scalar_one()
    assert row.role == "user"


def test_set_active_deactivate_blocked_when_last_active_admin(
    sync_session_maker,
) -> None:
    from gargantua.repo.users import LastAdminError, set_active

    with sync_session_maker() as s:
        _seed_admin(s, username="root")
        # Second admin exists but is inactive — must not count toward the
        # "active admin" total.
        _seed_admin(s, username="dormant", is_active=False)
        s.commit()

    with sync_session_maker() as s:
        root = s.execute(select(User).where(User.username == "root")).scalar_one()
        with pytest.raises(LastAdminError):
            set_active(s, user_id=root.id, is_active=False)


def test_set_active_deactivate_allowed_when_other_active_admin(
    sync_session_maker,
) -> None:
    from gargantua.repo.users import set_active

    with sync_session_maker() as s:
        _seed_admin(s, username="root")
        _seed_admin(s, username="backup")
        s.commit()

    with sync_session_maker() as s:
        root = s.execute(select(User).where(User.username == "root")).scalar_one()
        set_active(s, user_id=root.id, is_active=False)
        s.commit()

    with sync_session_maker() as s:
        row = s.execute(select(User).where(User.username == "root")).scalar_one()
    assert row.is_active is False


def test_set_active_reactivation_always_allowed(sync_session_maker) -> None:
    """Re-enabling never trips the guard — there's no risk of admin lockout."""
    from gargantua.repo.users import set_active

    with sync_session_maker() as s:
        admin = _seed_admin(s, username="root", is_active=False)
        s.commit()

    with sync_session_maker() as s:
        admin = s.execute(select(User).where(User.username == "root")).scalar_one()
        set_active(s, user_id=admin.id, is_active=True)
        s.commit()

    with sync_session_maker() as s:
        row = s.execute(select(User).where(User.username == "root")).scalar_one()
    assert row.is_active is True


def test_set_role_raises_when_user_missing(sync_session_maker) -> None:
    from gargantua.repo.users import UserNotFound, set_role

    with sync_session_maker() as s:
        with pytest.raises(UserNotFound):
            set_role(s, user_id=uuid4(), new_role="admin")


# ---------------------------------------------------------------------------
# list_users
# ---------------------------------------------------------------------------


def test_list_users_paginates_and_filters_by_role(sync_session_maker) -> None:
    from gargantua.repo.users import create_user, list_users

    with sync_session_maker() as s:
        for i in range(3):
            create_user(s, username=f"admin{i}", password="x", role="admin")
        for i in range(5):
            create_user(s, username=f"user{i}", password="x", role="user")
        s.commit()

    with sync_session_maker() as s:
        rows, total = list_users(s, page=1, page_size=10, role="admin")
    assert total == 3
    assert {u.username for u in rows} == {"admin0", "admin1", "admin2"}

    with sync_session_maker() as s:
        rows, total = list_users(s, page=1, page_size=2, role="user")
    assert total == 5
    assert len(rows) == 2

    with sync_session_maker() as s:
        rows, total = list_users(s, page=3, page_size=2, role="user")
    assert total == 5
    assert len(rows) == 1  # 5 users, 2 per page, page 3 has the remainder


def test_list_users_search_matches_substring(sync_session_maker) -> None:
    from gargantua.repo.users import create_user, list_users

    with sync_session_maker() as s:
        create_user(s, username="alice.builder", password="x", role="user")
        create_user(s, username="bob.tester", password="x", role="user")
        create_user(s, username="charlie", password="x", role="admin")
        s.commit()

    with sync_session_maker() as s:
        rows, total = list_users(s, page=1, page_size=10, search="bui")
    assert total == 1
    assert rows[0].username == "alice.builder"


def test_list_users_excludes_inactive_by_default(sync_session_maker) -> None:
    from gargantua.repo.users import create_user, list_users, set_active

    with sync_session_maker() as s:
        active = create_user(s, username="active", password="x", role="user")
        inactive = create_user(s, username="inactive", password="x", role="user")
        s.commit()
        set_active(s, user_id=inactive.id, is_active=False)
        s.commit()
        _ = active  # silence unused

    with sync_session_maker() as s:
        rows, total = list_users(s, page=1, page_size=10)
    assert total == 1
    assert rows[0].username == "active"

    with sync_session_maker() as s:
        rows, total = list_users(s, page=1, page_size=10, include_inactive=True)
    assert total == 2

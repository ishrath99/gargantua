"""Repo layer for ``gargantua_app.audit_log`` — record + paginated query."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from gargantua.db.models import AuditLog


@pytest.fixture
def sync_session_maker(truncate_db: Engine) -> sessionmaker:
    return sessionmaker(bind=truncate_db, expire_on_commit=False, future=True)


# ---------------------------------------------------------------------------
# record
# ---------------------------------------------------------------------------


def test_record_inserts_row_with_all_fields(sync_session_maker) -> None:
    from gargantua.repo.audit import record
    from gargantua.repo.users import create_user

    target = uuid4()

    with sync_session_maker() as s:
        actor_user = create_user(s, username="auditor", password="x", role="admin")
        s.commit()
        actor = actor_user.id

    with sync_session_maker() as s:
        entry = record(
            s,
            actor_id=actor,
            action="user.create",
            target_type="user",
            target_id=target,
            before=None,
            after={"username": "alice", "role": "user"},
        )
        s.commit()

    with sync_session_maker() as s:
        row = s.execute(select(AuditLog).where(AuditLog.id == entry.id)).scalar_one()
    assert row.actor_id == actor
    assert row.action == "user.create"
    assert row.target_type == "user"
    assert row.target_id == target
    assert row.before is None
    assert row.after == {"username": "alice", "role": "user"}
    assert row.created_at is not None


def test_record_does_not_commit(sync_session_maker) -> None:
    """The repo function flushes but never commits; rollback drops the entry."""
    from gargantua.repo.audit import record

    with sync_session_maker() as s:
        record(
            s,
            actor_id=None,
            action="test.action",
            target_type="user",
            target_id=None,
            before=None,
            after=None,
        )
        s.rollback()

    with sync_session_maker() as s:
        rows = s.execute(select(AuditLog)).scalars().all()
    assert rows == []


def test_record_allows_null_actor_for_system_actions(sync_session_maker) -> None:
    from gargantua.repo.audit import record

    with sync_session_maker() as s:
        record(
            s,
            actor_id=None,
            action="bootstrap.admin_created",
            target_type="user",
            target_id=uuid4(),
            before=None,
            after={"role": "admin"},
        )
        s.commit()

    with sync_session_maker() as s:
        row = s.execute(select(AuditLog)).scalar_one()
    assert row.actor_id is None
    assert row.action == "bootstrap.admin_created"


# ---------------------------------------------------------------------------
# list_audit
# ---------------------------------------------------------------------------


def test_list_audit_filters_and_paginates(sync_session_maker) -> None:
    from gargantua.repo.audit import list_audit, record
    from gargantua.repo.users import create_user

    user_target = uuid4()

    with sync_session_maker() as s:
        actor_a = create_user(s, username="aa", password="x", role="admin").id
        actor_b = create_user(s, username="bb", password="x", role="admin").id
        s.commit()
        for i in range(3):
            record(
                s,
                actor_id=actor_a,
                action="user.create",
                target_type="user",
                target_id=uuid4(),
                before=None,
                after={"i": i},
            )
        record(
            s,
            actor_id=actor_b,
            action="user.role_update",
            target_type="user",
            target_id=user_target,
            before={"role": "user"},
            after={"role": "admin"},
        )
        record(
            s,
            actor_id=actor_a,
            action="mcp_server.create",
            target_type="mcp_server",
            target_id=uuid4(),
            before=None,
            after={"name": "pg-prod"},
        )
        s.commit()

    with sync_session_maker() as s:
        rows, total = list_audit(s, page=1, page_size=50)
    assert total == 5

    with sync_session_maker() as s:
        rows, total = list_audit(s, page=1, page_size=50, actor_id=actor_a)
    assert total == 4
    assert all(r.actor_id == actor_a for r in rows)

    with sync_session_maker() as s:
        rows, total = list_audit(s, page=1, page_size=50, target_type="user")
    assert total == 4

    with sync_session_maker() as s:
        rows, total = list_audit(s, page=1, page_size=50, target_type="user", target_id=user_target)
    assert total == 1
    assert rows[0].action == "user.role_update"

    with sync_session_maker() as s:
        rows, total = list_audit(s, page=1, page_size=50, action="user.create")
    assert total == 3


def test_list_audit_orders_newest_first(sync_session_maker) -> None:
    """Audit list defaults to descending ``created_at`` so admins see recent events first."""
    from gargantua.repo.audit import list_audit, record

    with sync_session_maker() as s:
        for action in ("first", "second", "third"):
            record(
                s,
                actor_id=None,
                action=action,
                target_type="x",
                target_id=None,
                before=None,
                after=None,
            )
        s.commit()

    with sync_session_maker() as s:
        rows, _ = list_audit(s, page=1, page_size=50)
    # The most recent insert (third) should come first; created_at is the same
    # for all three at second granularity but the PK breaks ties.
    actions = [r.action for r in rows]
    assert actions[0] == "third" or actions == ["third", "second", "first"]

"""Repository for ``ai.audit_log``.

Every admin-side mutation should write a row here in the *same*
transaction as the mutation itself, so that the audit trail can never
disagree with the row it describes.  Pattern:

.. code-block:: python

    async with session.begin():
        user = await users_repo.acreate_user(session, ...)
        await audit_repo.arecord(
            session,
            actor_id=current_admin_id,
            action="user.create",
            target_type="user",
            target_id=user.id,
            before=None,
            after=user_to_dict(user),
        )
    # commit happens here; failure rolls back both writes.

``record`` / ``arecord`` therefore **do not commit** — they only flush
so the inserted row gets its ``id`` populated for the caller.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from gargantua.db.models import AuditLog


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_list_query(
    *,
    actor_id: UUID | None,
    target_type: str | None,
    target_id: UUID | None,
    action: str | None,
):
    stmt = select(AuditLog)
    count_stmt = select(func.count()).select_from(AuditLog)

    if actor_id is not None:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
        count_stmt = count_stmt.where(AuditLog.actor_id == actor_id)
    if target_type is not None:
        stmt = stmt.where(AuditLog.target_type == target_type)
        count_stmt = count_stmt.where(AuditLog.target_type == target_type)
    if target_id is not None:
        stmt = stmt.where(AuditLog.target_id == target_id)
        count_stmt = count_stmt.where(AuditLog.target_id == target_id)
    if action is not None:
        stmt = stmt.where(AuditLog.action == action)
        count_stmt = count_stmt.where(AuditLog.action == action)

    # Newest first (PK is monotonic so it cleanly breaks created_at ties).
    stmt = stmt.order_by(desc(AuditLog.id))
    return stmt, count_stmt


# ---------------------------------------------------------------------------
# Sync API
# ---------------------------------------------------------------------------


def record(
    session: Session,
    *,
    actor_id: UUID | None,
    action: str,
    target_type: str,
    target_id: UUID | None,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> AuditLog:
    """Append an audit entry to the session and return the model instance.

    Does **not** commit; the caller is expected to commit alongside the
    row the audit entry describes, so the two are always consistent.
    """
    entry = AuditLog(
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        before=before,
        after=after,
    )
    session.add(entry)
    session.flush()
    return entry


def list_audit(
    session: Session,
    *,
    page: int = 1,
    page_size: int = 50,
    actor_id: UUID | None = None,
    target_type: str | None = None,
    target_id: UUID | None = None,
    action: str | None = None,
) -> tuple[list[AuditLog], int]:
    if page < 1 or page_size < 1:
        raise ValueError("page and page_size must be >= 1")

    stmt, count_stmt = _build_list_query(
        actor_id=actor_id,
        target_type=target_type,
        target_id=target_id,
        action=action,
    )
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = list(session.execute(stmt).scalars().all())
    total = session.execute(count_stmt).scalar_one()
    return rows, total


# ---------------------------------------------------------------------------
# Async API
# ---------------------------------------------------------------------------


async def arecord(
    session: AsyncSession,
    *,
    actor_id: UUID | None,
    action: str,
    target_type: str,
    target_id: UUID | None,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> AuditLog:
    entry = AuditLog(
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        before=before,
        after=after,
    )
    session.add(entry)
    await session.flush()
    return entry


async def alist_audit(
    session: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 50,
    actor_id: UUID | None = None,
    target_type: str | None = None,
    target_id: UUID | None = None,
    action: str | None = None,
) -> tuple[list[AuditLog], int]:
    if page < 1 or page_size < 1:
        raise ValueError("page and page_size must be >= 1")

    stmt, count_stmt = _build_list_query(
        actor_id=actor_id,
        target_type=target_type,
        target_id=target_id,
        action=action,
    )
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = list((await session.execute(stmt)).scalars().all())
    total = (await session.execute(count_stmt)).scalar_one()
    return rows, total

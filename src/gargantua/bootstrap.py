"""First-boot bootstrap helpers.

Currently exposes one function: :func:`bootstrap_admin_if_needed`, which
creates the *only* admin user the platform ships with when both:

* the ``users`` table is empty, **and**
* both ``BOOTSTRAP_ADMIN_USERNAME`` *and* ``BOOTSTRAP_ADMIN_PASSWORD`` are
  set in the environment.

This is wired into :func:`gargantua.main.lifespan` so a fresh deployment
spins up with a working admin account that can then create additional
users via the UI / admin CLI.  Subsequent boots are a no-op.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gargantua.auth.password import hash_password
from gargantua.db.models import User
from gargantua.settings import get_settings


logger = logging.getLogger(__name__)


async def bootstrap_admin_if_needed(session: AsyncSession) -> bool:
    """Create the first admin if all preconditions hold.

    Returns ``True`` if a row was inserted, ``False`` otherwise.  Errors
    propagate; the caller (lifespan) is responsible for deciding whether
    a failed bootstrap should abort startup.
    """
    settings = get_settings()
    username = settings.bootstrap_admin_username.strip()
    password = settings.bootstrap_admin_password

    if not username or not password:
        logger.debug(
            "bootstrap-admin skipped: BOOTSTRAP_ADMIN_USERNAME/PASSWORD not both set"
        )
        return False

    existing = await session.execute(select(func.count()).select_from(User))
    if existing.scalar_one() > 0:
        logger.debug("bootstrap-admin skipped: users table is non-empty")
        return False

    session.add(
        User(
            username=username,
            password_hash=hash_password(password),
            role="admin",
        )
    )
    await session.commit()
    logger.info("bootstrap-admin created user %r (role=admin)", username)
    return True

"""User-facing listings of runnable agents and teams.

These endpoints answer the question "what can I (the caller) run?"
and feed the chat UI's agent/team picker.  They are gated by
``SCOPE_USER`` (which admin tokens carry too), and return only
non-archived rows.

The response shape is deliberately trimmed compared to ``/admin/...``:
internal fields like ``tools_config``, ``agent_config``, ``created_by``
and timestamps are omitted.  A chat user shouldn't need (or see)
operator-only metadata.

Today every authenticated user can see every non-archived agent/team.
When per-user RBAC lands, the policy plug-in goes here — we'd filter
``rows`` by the caller's permissions before projecting to the
``Me*Out`` schemas.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from gargantua.api.schemas import (
    MeAgentListOut,
    MeAgentOut,
    MeTeamListOut,
    MeTeamOut,
)
from gargantua.auth import TokenClaims, require_user
from gargantua.db.session import get_session
from gargantua.repo import agents as agents_repo
from gargantua.repo import teams as teams_repo


router = APIRouter()


@router.get("/agents", response_model=MeAgentListOut, tags=["me"])
async def list_me_agents(
    session: Annotated[AsyncSession, Depends(get_session)],
    _claims: Annotated[TokenClaims, Depends(require_user)],
) -> MeAgentListOut:
    """List non-archived agents the caller can run.

    Not paginated — bounded by the size of the agent catalog.  We rely
    on the repo to order results (currently by name); changing that
    ordering belongs in :mod:`gargantua.repo.agents`, not here.
    """
    # ``page_size`` here is a generous upper bound, not a UX-facing
    # paginator.  If a tenant ever defines more than 1000 agents we'll
    # add proper pagination, but until then this single-shot listing is
    # simpler for the UI to consume.
    rows, total = await agents_repo.alist_agents(
        session, page=1, page_size=1000, include_archived=False
    )
    return MeAgentListOut(
        items=[MeAgentOut.model_validate(r) for r in rows],
        total=total,
    )


@router.get("/teams", response_model=MeTeamListOut, tags=["me"])
async def list_me_teams(
    session: Annotated[AsyncSession, Depends(get_session)],
    _claims: Annotated[TokenClaims, Depends(require_user)],
) -> MeTeamListOut:
    """List non-archived teams the caller can run.  See
    :func:`list_me_agents` for shape / pagination notes."""
    rows, total = await teams_repo.alist_teams(
        session, page=1, page_size=1000, include_archived=False
    )
    return MeTeamListOut(
        items=[MeTeamOut.model_validate(r) for r in rows],
        total=total,
    )

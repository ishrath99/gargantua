"""Runtime agent + team run routes — override Agno's ``POST /.../runs``.

Mounted on the parent app at ``/v1/agents/{agent_id}/runs`` and
``/v1/teams/{team_id}/runs`` *before* the ``/v1`` AgentOS mount, so
these handlers match first (Starlette checks routes in registration
order).

Flow — agent runs
-----------------

1.  **Lookup** — fetch the :class:`~gargantua.db.models.Agent` row.
    404 if missing or archived (archived rows are invisible to users
    by design; ``/admin/agents`` is the only way to bring one back).

2.  **Lease** — for each ``mcp_server_ids`` entry, call
    ``cache.acquire(sid)``.  If any lease fails (server deleted,
    archived, KEK-mismatch ciphertext), release everything we
    *did* acquire and return 503.

3.  **Build** — :func:`~gargantua.registry.build_agno_agent` turns the
    row + tools handles into a transient ``agno.Agent``.

4.  **Run** — ``agent.arun(...)`` dispatches by ``stream``:

    * ``stream=False`` (default): returns a coroutine; we ``await`` it
      and forward ``result.to_dict()`` as JSON.
    * ``stream=True``: returns an ``AsyncIterator`` directly (no
      ``await``); we wrap it in an SSE ``StreamingResponse`` of
      ``data: <json>\\n\\n`` events.
      ending with ``data: [DONE]\\n\\n``.  The stream generator owns
      the leases for its lifetime and releases them on completion
      (or on early disconnect, via the generator's ``finally``).

5.  **Release** — for non-streaming, leases are released before the
    response is returned.  For streaming, ownership transfers to the
    SSE generator.

Flow — team runs
----------------

Same five phases, but with two extras inserted between (1) and (2):

* **Member resolution** — load each :class:`Agent` row referenced in
  ``team.member_agent_ids``.  Empty / missing / archived members
  surface a structured 422 with the offending ids so the admin knows
  exactly what to fix.

* **Lease deduplication** — multiple members can share an MCP server.
  We collect the *union* of ``mcp_server_ids`` across the members,
  acquire one lease per unique ``server_id``, and slice the resulting
  handles per-member when building each :class:`agno.Agent`.  The
  cache's ref-count goes up by 1, not by N — so the post-run release
  is symmetric and zero-leak.

Why not register on the sub-app
-------------------------------

We could add this route to the AgentOS sub-app *before* AgentOS adds
its own.  But the parent app already has ``app.state.mcp_cache`` and
``app.state.agno_db`` — and inside a mounted sub-app, ``request.app``
is the sub-app, not the parent.  Putting the route on the parent app
keeps the state access simple and side-steps the duplicate-route
concern.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gargantua.api.schemas import AgentRunRequest
from gargantua.auth import TokenClaims, require_user
from gargantua.db.models import Agent
from gargantua.db.session import get_session
from gargantua.mcp_cache import Lease, MCPCache, ServerNotFound
from gargantua.registry import build_agno_agent, build_agno_team
from gargantua.repo import agents as agents_repo
from gargantua.repo import mcp_child_resources as child_resources_repo
from gargantua.repo import teams as teams_repo
from gargantua.settings import get_settings


logger = logging.getLogger(__name__)


router = APIRouter()


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------


def _get_cache(request: Request) -> MCPCache:
    """Resolve the cache singleton off ``app.state``.

    Returning 503 (rather than letting an ``AttributeError`` bubble up)
    means a misconfigured environment surfaces with a clear status
    code at the route layer instead of a 500 with no breadcrumb.
    """
    cache = getattr(request.app.state, "mcp_cache", None)
    if cache is None:
        raise HTTPException(
            status_code=503, detail="MCP cache is not initialized"
        )
    return cache


def _get_agno_db(request: Request) -> Any:
    """Shared :class:`~agno.db.postgres.PostgresDb`, or ``None`` in test
    setups where it hasn't been wired."""
    return getattr(request.app.state, "agno_db", None)


# ---------------------------------------------------------------------------
# Leasing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ServerLeaseKey:
    """Identifies one unique cache entry needed for a run.

    The cache is keyed by ``(server_id, sorted_child_resource_ids)`` —
    same server with different child resource filters is two distinct
    entries.  This struct is the route-layer mirror of that key so we
    can dedupe across team members and slice per-member tools.
    """

    server_id: UUID
    child_resource_ids: tuple[UUID, ...]  # canonical (sorted)


async def _resolve_lease_keys_for_agent(
    session: AsyncSession,
    *,
    mcp_server_ids: list[UUID],
    child_resource_ids: list[UUID],
) -> tuple[list[_ServerLeaseKey], dict[UUID, _ServerLeaseKey]]:
    """Compute the lease keys an agent needs, plus a server_id -> key map.

    The list is in the order ``mcp_server_ids`` declares (so the tools
    list passed to ``build_agno_agent`` matches the agent's declared
    order).  The dict mirrors that for tools-slicing per team member.

    Children that don't resolve to a parent (deleted out from under
    the agent) are silently dropped here — the cache layer will surface
    the breakage as ``ServerNotFound`` on acquire if the agent declared
    the child but it's gone.  Children whose parent is NOT in the
    agent's ``mcp_server_ids`` are also dropped: the repo's reference
    validation catches that at create-time, but defensive at runtime.
    """
    parent_map = await child_resources_repo.aget_parent_map(
        session, child_resource_ids
    )
    declared = set(mcp_server_ids)
    by_parent: dict[UUID, list[UUID]] = {}
    for child_id, parent_id in parent_map.items():
        if parent_id not in declared:
            # Child references a server the agent doesn't declare —
            # would be a config error.  Skip rather than failing.
            logger.warning(
                "agent runs: child %s references parent %s which the "
                "agent doesn't declare in mcp_server_ids; ignoring",
                child_id,
                parent_id,
            )
            continue
        by_parent.setdefault(parent_id, []).append(child_id)

    keys: list[_ServerLeaseKey] = []
    by_server: dict[UUID, _ServerLeaseKey] = {}
    for sid in mcp_server_ids:
        kids = tuple(sorted(by_parent.get(sid, [])))
        key = _ServerLeaseKey(server_id=sid, child_resource_ids=kids)
        keys.append(key)
        by_server[sid] = key
    return keys, by_server


async def _acquire_all(
    cache: MCPCache, keys: list[_ServerLeaseKey]
) -> dict[_ServerLeaseKey, Lease]:
    """Lease every requested key, releasing any already-acquired
    leases if a later one fails.

    Deduplicates within ``keys`` (same key in the list = one lease,
    not two) so team aggregation can pass overlapping keys without
    pumping ref-counts up artificially.

    Sequential rather than ``asyncio.gather`` — a typical agent uses
    1-3 MCP servers, and sequential keeps the cleanup story simple
    (we always know which keys were partially acquired).
    """
    leases: dict[_ServerLeaseKey, Lease] = {}
    try:
        for key in keys:
            if key in leases:
                # Same key appearing twice (e.g. two team members
                # needing the same server + child set) is dedupe'd
                # here so ref_count goes up by 1, not by 2.
                continue
            leases[key] = await cache.acquire(
                key.server_id,
                child_resource_ids=key.child_resource_ids,
            )
        return leases
    except ServerNotFound:
        await _release_all(leases)
        raise
    except Exception:
        # Build failure (subprocess spawn failed, URL unreachable, ...)
        # — same cleanup contract.
        await _release_all(leases)
        raise


async def _release_all(leases: dict[_ServerLeaseKey, Lease]) -> None:
    """Best-effort: release every lease, swallowing per-release errors.

    A failure to release would only leak a ref-count slot; that's
    recoverable via the admin ``evict`` endpoint and doesn't justify
    surfacing the error to the caller (who already got their response).
    """
    for key, lease in leases.items():
        try:
            await lease.release()
        except Exception:  # noqa: BLE001 — best-effort drain
            logger.exception(
                "mcp-cache: lease.release for key=%s failed", key
            )


# ---------------------------------------------------------------------------
# Run route
# ---------------------------------------------------------------------------


@router.post("/agents/{agent_id}/runs", tags=["runs"])
async def run_agent(
    agent_id: UUID,
    body: AgentRunRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    claims: Annotated[TokenClaims, Depends(require_user)],
) -> Any:
    """Run an agent identified by ``agent_id``.

    Body shape: :class:`~gargantua.api.schemas.AgentRunRequest`.
    ``stream=true`` returns an SSE stream; otherwise the run output is
    returned as JSON.
    """
    # 1. Lookup.
    row = await agents_repo.aget_by_id(session, agent_id)
    if row is None or row.archived_at is not None:
        raise HTTPException(
            status_code=404, detail=f"Agent {agent_id} not found"
        )

    cache = _get_cache(request)
    agno_db = _get_agno_db(request)

    # 2. Resolve lease keys + lease.  Keys factor in child_resource_ids
    #    so an agent that filters a multi-tool MCP server through a
    #    specific swagger set gets its own dedicated warm handle.
    keys, key_by_server = await _resolve_lease_keys_for_agent(
        session,
        mcp_server_ids=list(row.mcp_server_ids),
        child_resource_ids=list(row.child_resource_ids),
    )
    try:
        leases = await _acquire_all(cache, keys)
    except ServerNotFound as exc:
        raise HTTPException(
            status_code=503,
            detail=f"MCP server {exc} is not available",
        ) from exc
    except Exception as exc:
        logger.exception(
            "mcp-cache: lease acquisition failed for agent %s", agent_id
        )
        raise HTTPException(
            status_code=503,
            detail="Failed to acquire MCP tools for this agent",
        ) from exc

    # Tools list order mirrors ``row.mcp_server_ids`` so the agent's
    # declared tool order is preserved (relevant for tool_choice etc.).
    tools = [leases[key_by_server[sid]].tools for sid in row.mcp_server_ids]

    # 3. Build the transient agent.  ``debug`` is read from settings on
    #    each request (not cached at module import) so flipping
    #    ``AGNO_DEBUG`` in the operator's ``.env`` takes effect on the
    #    next uvicorn reload without code changes.
    debug = get_settings().agno_debug
    try:
        agent = build_agno_agent(row, tools=tools, db=agno_db, debug=debug)
    except Exception:
        # Most likely a model-resolution error from Agno.
        await _release_all(leases)
        raise

    # 4 + 5. Run + release.
    if body.stream:
        return await _stream_response(agent, body, claims.sub, leases)
    return await _nonstreaming_response(agent, body, claims.sub, leases)


# ---------------------------------------------------------------------------
# Response handlers
# ---------------------------------------------------------------------------


async def _nonstreaming_response(
    agent: Any,
    body: AgentRunRequest,
    user_id: str,
    leases: dict[UUID, Lease],
) -> Any:
    """Run synchronously and return the run output dict.

    Releases the leases inside ``finally`` so they go back to the cache
    regardless of whether ``arun`` succeeded.
    """
    try:
        result = await agent.arun(
            body.input,
            stream=False,
            user_id=user_id,
            session_id=body.session_id,
            session_state=body.session_state,
            metadata=body.metadata,
        )
    finally:
        await _release_all(leases)

    # ``RunOutput.to_dict`` is Agno's canonical serializer; we forward
    # whatever it returns as the JSON body of the response.
    return result.to_dict()


async def _stream_response(
    agent: Any,
    body: AgentRunRequest,
    user_id: str,
    leases: dict[UUID, Lease],
) -> StreamingResponse:
    """Run with ``stream=True`` and return an SSE :class:`StreamingResponse`.

    ``agno.Agent.arun(stream=True)`` is *not* a coroutine — it's a sync
    method that returns an :class:`AsyncIterator` directly.  Agno does
    a chunk of synchronous setup (input validation, hook normalization,
    session bootstrap) before producing the iterator, so this call can
    still raise.  We catch sync raises here so leases get released and
    the exception is re-raised for FastAPI to map to 500.

    Ownership of the leases transfers to the generator — they get
    released in the generator's ``finally``, after the last chunk has
    been pushed to the client (or on early disconnect).
    """
    try:
        iterator = agent.arun(
            body.input,
            stream=True,
            user_id=user_id,
            session_id=body.session_id,
            session_state=body.session_state,
            metadata=body.metadata,
        )
    except Exception:
        await _release_all(leases)
        raise

    return StreamingResponse(
        _sse_event_stream(iterator, leases),
        media_type="text/event-stream",
    )


async def _sse_event_stream(
    events: AsyncIterator[Any],
    leases: dict[UUID, Lease],
) -> AsyncIterator[str]:
    """Format Agno's run-event stream as Server-Sent Events.

    Each event is serialized via ``event.to_dict()`` (Agno's pydantic
    payloads expose this).  A terminating ``data: [DONE]\\n\\n``
    sentinel signals end-of-stream to the client so the JS reader
    can close the connection without waiting for an EOF.

    Lease cleanup runs in ``finally`` to cover the normal completion,
    exception, *and* client-disconnect paths (Starlette throws
    ``CancelledError`` into the generator on disconnect).
    """
    try:
        async for event in events:
            try:
                payload = (
                    event.to_dict() if hasattr(event, "to_dict") else event
                )
                yield f"data: {json.dumps(payload, default=str)}\n\n"
            except Exception:  # noqa: BLE001 — never break the stream on one bad event
                logger.exception("mcp-runs: event serialization failed")
                continue
        yield "data: [DONE]\n\n"
    finally:
        await _release_all(leases)


# ---------------------------------------------------------------------------
# Team-run helpers
# ---------------------------------------------------------------------------


async def _resolve_team_members(
    session: AsyncSession, member_ids: list[UUID]
) -> list[Agent]:
    """Load every member agent referenced by a team.

    Raises :class:`HTTPException` 422 with a structured detail if any
    of the referenced agents is missing or archived.  The detail
    payload separates the two buckets so the admin UI can render a
    clear "these are gone, these are retired" message instead of one
    opaque "broken team" error.

    An empty ``member_ids`` list is reported as ``team_has_no_members``
    — different bucket, different fix (add members vs. unarchive an
    existing one).
    """
    if not member_ids:
        raise HTTPException(
            status_code=422,
            detail={
                "reason": "team_has_no_members",
                "message": (
                    "Team has no members.  Add at least one agent via "
                    "/admin/teams before running."
                ),
            },
        )

    stmt = select(Agent).where(Agent.id.in_(member_ids))
    rows = list((await session.execute(stmt)).scalars().all())
    found = {row.id: row for row in rows}

    missing = [sid for sid in member_ids if sid not in found]
    archived = [
        sid for sid in member_ids
        if sid in found and found[sid].archived_at is not None
    ]

    if missing or archived:
        raise HTTPException(
            status_code=422,
            detail={
                "reason": "members_invalid",
                "message": (
                    "One or more team members are missing or archived; "
                    "edit the team to remove them or unarchive the agents."
                ),
                "missing": [str(sid) for sid in missing],
                "archived": [str(sid) for sid in archived],
            },
        )

    # Preserve the order declared in ``member_agent_ids`` — the team
    # mode interprets that order (especially for "route"), so we don't
    # want SQL row order to silently change behaviour.
    return [found[sid] for sid in member_ids]


async def _resolve_team_lease_keys(
    session: AsyncSession, members: list[Agent]
) -> tuple[list[_ServerLeaseKey], list[dict[UUID, _ServerLeaseKey]]]:
    """Collect the unique lease keys across team members.

    Returns:

    * ``unique_keys`` — list of distinct keys (in first-seen order
      across members; the cache's per-key lock dedupes the build, but
      we dedupe the *acquire* call list here so ref_count stays
      accurate).
    * ``per_member_maps`` — one ``{server_id: key}`` dict per member,
      so :func:`_build_team_members` can slice the right key per
      member's declared ``mcp_server_ids`` (members can have different
      child sets even on the same server).
    """
    per_member_maps: list[dict[UUID, _ServerLeaseKey]] = []
    seen: set[_ServerLeaseKey] = set()
    unique_keys: list[_ServerLeaseKey] = []
    for member in members:
        _keys, by_server = await _resolve_lease_keys_for_agent(
            session,
            mcp_server_ids=list(member.mcp_server_ids),
            child_resource_ids=list(member.child_resource_ids),
        )
        per_member_maps.append(by_server)
        for key in by_server.values():
            if key not in seen:
                seen.add(key)
                unique_keys.append(key)
    return unique_keys, per_member_maps


def _build_team_members(
    member_rows: list[Agent],
    member_key_maps: list[dict[UUID, _ServerLeaseKey]],
    leases: dict[_ServerLeaseKey, Lease],
    agno_db: Any,
    *,
    debug: bool = False,
) -> list[Any]:
    """Build one :class:`agno.Agent` per row, slicing tools to that
    row's own (server, child_set) combination.

    Each member's key map (built by :func:`_resolve_team_lease_keys`)
    tells us which cache entry serves *that* member's view of each
    server.  Two members on the same parent server with different
    child sets each get their own warm handle, sliced in here.

    ``debug`` is forwarded verbatim to every member; the route caller
    reads :attr:`Settings.agno_debug` once and passes the same value
    so every member of the team logs at the same verbosity.
    """
    members: list[Any] = []
    for row, key_map in zip(member_rows, member_key_maps, strict=True):
        member_tools = [
            leases[key_map[sid]].tools
            for sid in row.mcp_server_ids
            if sid in key_map
        ]
        members.append(
            build_agno_agent(
                row, tools=member_tools, db=agno_db, debug=debug
            )
        )
    return members


# ---------------------------------------------------------------------------
# Team run route
# ---------------------------------------------------------------------------


@router.post("/teams/{team_id}/runs", tags=["runs"])
async def run_team(
    team_id: UUID,
    body: AgentRunRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    claims: Annotated[TokenClaims, Depends(require_user)],
) -> Any:
    """Run a team identified by ``team_id``.

    See module docstring for the full flow.  Reuses the same
    :class:`~gargantua.api.schemas.AgentRunRequest` body shape as
    agent runs — the fields (``input``, ``stream``, ``session_id``,
    ``session_state``, ``metadata``) apply equally to teams.
    """
    # 1. Team lookup.
    team_row = await teams_repo.aget_by_id(session, team_id)
    if team_row is None or team_row.archived_at is not None:
        raise HTTPException(
            status_code=404, detail=f"Team {team_id} not found"
        )

    # 1b. Resolve members (422 on any structural problem).
    member_rows = await _resolve_team_members(
        session, list(team_row.member_agent_ids)
    )

    cache = _get_cache(request)
    agno_db = _get_agno_db(request)

    # 2. Resolve lease keys across members (deduplicated by
    #    (server_id, child_set)), then acquire them.
    unique_keys, member_key_maps = await _resolve_team_lease_keys(
        session, member_rows
    )
    try:
        leases = await _acquire_all(cache, unique_keys)
    except ServerNotFound as exc:
        raise HTTPException(
            status_code=503,
            detail=f"MCP server {exc} is not available",
        ) from exc
    except Exception as exc:
        logger.exception(
            "mcp-cache: lease acquisition failed for team %s", team_id
        )
        raise HTTPException(
            status_code=503,
            detail="Failed to acquire MCP tools for this team",
        ) from exc

    # 3. Build members (each gets its own (server, child_set)-keyed
    #    tools slice), then the team.  ``debug`` is read once and
    #    threaded through both the team and every member so they all
    #    log at the same verbosity.
    debug = get_settings().agno_debug
    try:
        members = _build_team_members(
            member_rows, member_key_maps, leases, agno_db, debug=debug
        )
        team = build_agno_team(
            team_row, members=members, db=agno_db, debug=debug
        )
    except Exception:
        await _release_all(leases)
        raise

    # 4 + 5. Run + release — shares the same helpers as agent runs.
    if body.stream:
        return await _stream_response(team, body, claims.sub, leases)
    return await _nonstreaming_response(team, body, claims.sub, leases)

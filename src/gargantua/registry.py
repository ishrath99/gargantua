"""Pure-function bridge between DB rows and live Agno objects.

Two responsibilities:

* :func:`build_agno_agent` — turn an :class:`~gargantua.db.models.Agent`
  row plus an already-resolved tool list into an :class:`agno.agent.Agent`.
* :func:`build_agno_team` — turn a :class:`~gargantua.db.models.Team`
  row plus already-built member agents into an :class:`agno.team.Team`.

What this module is **not**:

* Not a DB layer.  Callers (the runtime routes in :mod:`gargantua.api.runs`)
  load the row themselves and hand it in.
* Not a tools constructor.  MCP tool spawning lives in
  :mod:`gargantua.mcp_tools`, and the cache (:mod:`gargantua.mcp_cache`)
  is what hands the warm handles back to the runtime route.
* Not stateful.  No singletons, no caches, no globals.  The runtime
  route builds a *transient* Agno object per request, which is
  cheap (constructor only — the expensive part is the MCP connection,
  which the cache shares).

Design note on ``agent_config`` / ``team_config``
-------------------------------------------------

The DB schema gives admins a free-form JSON bag to tune knobs that
don't deserve their own column yet (history depth, session summaries,
iteration limits, ...).  Each builder copies a **fixed allow-list** of
keys into the Agno constructor.  Unknown keys are ignored silently —
that lets us add new flags here without coordinating with every
admin's saved configs, and lets admins put arbitrary metadata into the
bag (e.g. notes, owner-team) without breaking the build.
"""

from __future__ import annotations

from typing import Any, Final

from agno.agent import Agent as AgnoAgent
from agno.team import Team as AgnoTeam

from gargantua.db.models import Agent as AgentRow
from gargantua.db.models import Team as TeamRow

# Keys we forward verbatim from ``agent_config`` into the Agno Agent
# constructor.  Anything outside this allow-list is silently dropped
# (treated as admin metadata).
_AGENT_CONFIG_FORWARDED: Final[frozenset[str]] = frozenset(
    {
        # History / context
        "add_history_to_context",
        "num_history_runs",
        "num_history_messages",
        # Memory / session
        "enable_session_summaries",
        "enable_user_memories",
        "add_memories_to_context",
        "add_session_summary_to_context",
        # Tools
        "tool_call_limit",
        "tool_choice",
        "compress_tool_results",
        # Streaming
        "stream",
        "stream_events",
        # Reasoning / parser / output
        "use_instruction_tags",
    }
)


# Same idea for teams.  ``model`` and ``instructions`` are also accepted
# but handled explicitly so an explicit ``model=`` kwarg can override.
_TEAM_CONFIG_FORWARDED: Final[frozenset[str]] = frozenset(
    {
        "max_iterations",
        "add_team_history_to_members",
        "num_team_history_runs",
        "respond_directly",
        "determine_input_for_members",
        "delegate_to_all_members",
    }
)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


def build_agno_agent(
    row: AgentRow,
    *,
    tools: list[Any] | None = None,
    db: Any | None = None,
    debug: bool = False,
) -> AgnoAgent:
    """Materialize a transient Agno Agent from a DB row.

    Parameters
    ----------
    row
        The persisted (or just-loaded) ``Agent`` row.  Only the public
        column values are read; no lazy-load happens here.
    tools
        Already-resolved tool handles to attach — typically the live
        :class:`MCPTools` instances handed back from the MCP cache.
        Pass ``None`` (the default) when the agent has no MCP servers
        wired; Agno will treat it as a no-tools agent.
    db
        Optional Agno ``Db`` (e.g. ``PostgresDb``) so session / memory /
        run-output rows land in the same store as the rest of AgentOS.
        Routes pass ``request.app.state.agno_db`` here.
    debug
        When true, build the Agent with ``debug_mode=True``.  Agno then
        bumps its ``agno`` logger to DEBUG and prints the full run
        trace (prompts, tool calls + args + results, intermediate
        reasoning) to the same console as the API.  Driven by the
        ``AGNO_DEBUG`` env var via ``Settings.agno_debug`` (see
        :mod:`gargantua.settings`).  The route layer reads the setting
        and forwards it here so this module stays free of
        configuration imports.

    Returns
    -------
    A fresh :class:`agno.agent.Agent`.  Constructing this is cheap
    (no network); the heavy lifting is in ``arun``.
    """
    cfg: dict[str, Any] = row.agent_config or {}

    kwargs: dict[str, Any] = {
        "id": str(row.id),
        "name": row.name,
        "description": row.description,
        "model": row.model,
        "instructions": row.instructions,
    }
    if tools:
        kwargs["tools"] = tools
    if db is not None:
        kwargs["db"] = db
    if debug:
        kwargs["debug_mode"] = True

    for key in _AGENT_CONFIG_FORWARDED:
        if key in cfg:
            kwargs[key] = cfg[key]

    return AgnoAgent(**kwargs)


# ---------------------------------------------------------------------------
# Team
# ---------------------------------------------------------------------------


def build_agno_team(
    row: TeamRow,
    *,
    members: list[AgnoAgent | AgnoTeam],
    model: str | None = None,
    db: Any | None = None,
    debug: bool = False,
) -> AgnoTeam:
    """Materialize a transient Agno Team from a DB row + built members.

    Parameters
    ----------
    row
        The persisted (or just-loaded) ``Team`` row.
    members
        Already-built Agno Agent / Team objects.  Callers resolve
        ``row.member_agent_ids`` -> ``Agent`` rows -> built objects in
        the runtime route.  This function does *not* touch the database
        to find them.
    model
        Explicit override for the team's coordinator model.  Wins over
        ``team_config['model']``.  Useful when the route wants to pin
        a cheaper model for the coordinator while leaving member
        agents on their default.  If both this arg and the config are
        absent, Agno falls back to whatever default it computes.
    db
        Optional Agno ``Db`` (see :func:`build_agno_agent`).
    debug
        When true, build the Team with ``debug_mode=True``.  See
        :func:`build_agno_agent` for the semantics — same flag, same
        env var (``AGNO_DEBUG``).  The team's own ``agno-team`` logger
        plus each member's ``agno`` logger are bumped to DEBUG when
        the team runs.

    Returns
    -------
    A fresh :class:`agno.team.Team`.
    """
    cfg: dict[str, Any] = row.team_config or {}

    kwargs: dict[str, Any] = {
        "id": str(row.id),
        "name": row.name,
        "description": row.description,
        "mode": row.mode,
        "members": members,
    }

    chosen_model = model if model is not None else cfg.get("model")
    if chosen_model is not None:
        kwargs["model"] = chosen_model

    # team_config['instructions'] is handled here (not in the
    # generic forwarded set) because Agno accepts both a string and a
    # list of strings; we just forward whatever the admin saved.
    if "instructions" in cfg:
        kwargs["instructions"] = cfg["instructions"]

    if db is not None:
        kwargs["db"] = db
    if debug:
        kwargs["debug_mode"] = True

    for key in _TEAM_CONFIG_FORWARDED:
        if key in cfg:
            kwargs[key] = cfg[key]

    return AgnoTeam(**kwargs)

"""Unit tests for :mod:`gargantua.registry`.

The registry is the pure-function bridge between the DB and Agno: it
takes a row (plus already-resolved tools / member agents) and returns
a live ``agno.Agent`` or ``agno.Team`` instance.  Nothing in these
tests touches the database or constructs MCP tools — that wiring lives
in :mod:`gargantua.mcp_tools` and is exercised separately.

We deliberately construct REAL Agno objects (not mocks) so that:

* The mapping from row column to Agno constructor kwarg is verified
  against the actual library surface, not against a mock that might
  diverge from upstream over time.
* Any signature drift in Agno 2.6.7 -> future versions fails loudly
  in this test, not silently at runtime.

The Agent / Team rows we pass in are real :class:`~gargantua.db.models.Agent`
/ :class:`Team` SQLAlchemy instances, but they are detached (never
persisted), so no Postgres is needed.
"""

from __future__ import annotations

from uuid import uuid4

from agno.agent import Agent as AgnoAgent
from agno.team import Team as AgnoTeam

from gargantua.db.models import Agent as AgentRow, Team as TeamRow
from gargantua.registry import build_agno_agent, build_agno_team


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agent_row(**overrides) -> AgentRow:
    """Construct an unpersisted Agent row with sane defaults."""
    defaults = dict(
        id=uuid4(),
        name="alice",
        description="Test agent",
        model="openai:gpt-4o-mini",
        instructions="Be helpful.",
        tools_config={},
        mcp_server_ids=[],
        child_resource_ids=[],
        agent_config={},
    )
    defaults.update(overrides)
    return AgentRow(**defaults)


def _team_row(**overrides) -> TeamRow:
    """Construct an unpersisted Team row with sane defaults."""
    defaults = dict(
        id=uuid4(),
        name="ops",
        description="Test team",
        mode="route",
        member_agent_ids=[],
        team_config={},
    )
    defaults.update(overrides)
    return TeamRow(**defaults)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


def test_build_agno_agent_maps_required_fields() -> None:
    row = _agent_row(
        name="planner",
        description="Plans things",
        model="openai:gpt-4o-mini",
        instructions="Plan carefully.",
    )

    agent = build_agno_agent(row)

    assert isinstance(agent, AgnoAgent)
    assert agent.id == str(row.id)
    assert agent.name == "planner"
    assert agent.description == "Plans things"
    assert agent.instructions == "Plan carefully."
    # model is auto-resolved by Agno from the "openai:..." string into a
    # concrete model object; the resolution itself is Agno's
    # responsibility, we only assert *something* was set.
    assert agent.model is not None


def test_build_agno_agent_with_no_tools_passes_none() -> None:
    row = _agent_row()
    agent = build_agno_agent(row)
    # No MCP servers / no tools wired => Agent.tools is None or empty.
    assert agent.tools in (None, [])


def test_build_agno_agent_with_tools() -> None:
    """The tools list is opaque to the registry — it just forwards
    whatever the caller passed in to Agno."""

    class _Stub:
        pass

    stubs = [_Stub(), _Stub()]
    row = _agent_row()
    agent = build_agno_agent(row, tools=stubs)
    assert agent.tools == stubs


def test_build_agno_agent_honors_agent_config_history() -> None:
    row = _agent_row(
        agent_config={
            "add_history_to_context": True,
            "num_history_runs": 7,
        }
    )
    agent = build_agno_agent(row)
    assert agent.add_history_to_context is True
    assert agent.num_history_runs == 7


def test_build_agno_agent_honors_memory_and_sessions_flags() -> None:
    row = _agent_row(
        agent_config={
            "enable_session_summaries": True,
            "enable_user_memories": True,
            "compress_tool_results": True,
        }
    )
    agent = build_agno_agent(row)
    assert agent.enable_session_summaries is True
    assert agent.enable_user_memories is True
    assert agent.compress_tool_results is True


def test_build_agno_agent_honors_tool_call_limit() -> None:
    row = _agent_row(agent_config={"tool_call_limit": 12})
    agent = build_agno_agent(row)
    assert agent.tool_call_limit == 12


def test_build_agno_agent_ignores_unknown_config_keys() -> None:
    """Forward-compat: an unknown key in agent_config (typo, new field
    not yet supported) must not crash the build."""
    row = _agent_row(agent_config={"this_is_not_a_known_flag": "whatever"})
    # Should not raise.
    agent = build_agno_agent(row)
    assert agent is not None


def test_build_agno_agent_passes_db_when_provided() -> None:
    """Sentinel db object should be forwarded so Agent run output is
    persisted to the same store as AgentOS uses."""
    sentinel = object()
    row = _agent_row()
    agent = build_agno_agent(row, db=sentinel)
    assert agent.db is sentinel


def test_build_agno_agent_omits_db_when_none() -> None:
    """Without a db, Agent.db should remain Agno's default (None)."""
    row = _agent_row()
    agent = build_agno_agent(row)
    assert agent.db is None


def test_build_agno_agent_debug_default_off() -> None:
    """Default builds must NOT enable Agno's debug logging.

    The traces contain prompts and tool args, which can carry
    sensitive data, so debug must stay off unless the operator
    explicitly opts in via ``AGNO_DEBUG``.
    """
    row = _agent_row()
    agent = build_agno_agent(row)
    assert agent.debug_mode is False


def test_build_agno_agent_debug_propagated() -> None:
    """``debug=True`` must reach Agno's ``debug_mode`` so its loggers
    are bumped to DEBUG for the duration of the run."""
    row = _agent_row()
    agent = build_agno_agent(row, debug=True)
    assert agent.debug_mode is True


# ---------------------------------------------------------------------------
# Team
# ---------------------------------------------------------------------------


def _bare_member() -> AgnoAgent:
    return AgnoAgent(
        id=str(uuid4()),
        name="member",
        model="openai:gpt-4o-mini",
        instructions="Help.",
    )


def test_build_agno_team_maps_required_fields() -> None:
    member = _bare_member()
    row = _team_row(name="sre-team", mode="coordinate", description="On-call")

    team = build_agno_team(row, members=[member], model="openai:gpt-4o-mini")

    assert isinstance(team, AgnoTeam)
    assert team.id == str(row.id)
    assert team.name == "sre-team"
    assert team.mode == "coordinate"
    assert team.description == "On-call"
    assert team.members == [member]


def test_build_agno_team_uses_config_model_when_no_override() -> None:
    member = _bare_member()
    row = _team_row(team_config={"model": "openai:gpt-4o-mini"})
    team = build_agno_team(row, members=[member])
    assert team.model is not None


def test_build_agno_team_explicit_model_wins_over_config() -> None:
    """An explicit ``model=`` arg wins over team_config['model'] so the
    runtime route can override per request (e.g. cheaper model for the
    coordinator in dev)."""
    member = _bare_member()
    row = _team_row(team_config={"model": "openai:gpt-4o-mini"})
    # We can't easily compare model objects (Agno wraps strings), so
    # assert by class name to confirm a different one was picked.
    team_default = build_agno_team(row, members=[member])
    team_override = build_agno_team(
        row, members=[member], model="anthropic:claude-3-5-sonnet-latest"
    )
    assert type(team_default.model).__name__ != type(team_override.model).__name__


def test_build_agno_team_honors_team_config_iteration_limits() -> None:
    member = _bare_member()
    row = _team_row(
        team_config={
            "max_iterations": 4,
            "add_team_history_to_members": True,
            "num_team_history_runs": 2,
        }
    )
    team = build_agno_team(row, members=[member], model="openai:gpt-4o-mini")
    assert team.max_iterations == 4
    assert team.add_team_history_to_members is True
    assert team.num_team_history_runs == 2


def test_build_agno_team_honors_team_config_instructions() -> None:
    """``team_config['instructions']`` overrides whatever Agno's default
    would have been."""
    member = _bare_member()
    row = _team_row(
        team_config={"instructions": "Coordinate the SREs."}
    )
    team = build_agno_team(
        row, members=[member], model="openai:gpt-4o-mini"
    )
    assert team.instructions == "Coordinate the SREs."


def test_build_agno_team_passes_db_when_provided() -> None:
    member = _bare_member()
    sentinel = object()
    row = _team_row()
    team = build_agno_team(
        row, members=[member], model="openai:gpt-4o-mini", db=sentinel
    )
    assert team.db is sentinel


def test_build_agno_team_supports_all_three_modes() -> None:
    """Schema CHECK constraint allows route / coordinate / collaborate.
    All three should map to a valid Agno Team without complaint."""
    member = _bare_member()
    for mode in ("route", "coordinate", "collaborate"):
        row = _team_row(mode=mode)
        team = build_agno_team(
            row, members=[member], model="openai:gpt-4o-mini"
        )
        assert team.mode == mode


def test_build_agno_team_with_no_members_still_constructs() -> None:
    """A team with zero members is unusual but legal per our schema
    (member_agent_ids defaults to empty array).  Agno should accept
    it; if it later refuses, the registry test fails loudly and we
    can add a validation guard upstream in the repo layer."""
    row = _team_row()
    team = build_agno_team(row, members=[], model="openai:gpt-4o-mini")
    assert team.members == []


def test_build_agno_team_debug_default_off() -> None:
    """Mirror of the Agent-side guard: teams must default to debug off."""
    member = _bare_member()
    row = _team_row()
    team = build_agno_team(row, members=[member], model="openai:gpt-4o-mini")
    assert team.debug_mode is False


def test_build_agno_team_debug_propagated() -> None:
    """``debug=True`` reaches the Team's ``debug_mode``.  The route
    layer also forwards the same flag to every member via
    :func:`build_agno_agent`, so the per-member assertion lives in the
    Agent tests above."""
    member = _bare_member()
    row = _team_row()
    team = build_agno_team(
        row, members=[member], model="openai:gpt-4o-mini", debug=True
    )
    assert team.debug_mode is True

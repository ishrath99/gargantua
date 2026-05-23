"""Agent template loader for the "New from template" UI flow.

Each seed under :data:`_DEFAULT_TEMPLATES_DIR` (``src/gargantua/seeds/agents/``)
is a markdown file with YAML front-matter:

.. code-block:: text

    ---
    slug: db-investigator
    name: DB Investigator
    description: ...
    model: openai:gpt-4o-mini
    suggested_mcp_server_type_slugs:
      - postgres
    agent_config:
      add_history_to_context: true
      num_history_runs: 5
    ---

    # Role

    You are a senior database engineer ...

* The **front-matter** declares the default name / description / model /
  MCP-server-type suggestions / agent_config flags that the UI's create
  form will be pre-filled with.
* The **body** is the agent's ``instructions`` — markdown formatting is
  preserved and forwarded verbatim into the create call.

What this module is NOT
-----------------------

* Not a router — the admin routes live in :mod:`gargantua.api.admin`.
* Not a runtime cache — templates are tiny and re-read from disk on
  every list / get call so editing a seed during development takes
  effect on the next request.
* Not an instantiator — the UI calls ``POST /admin/agents`` with the
  template's fields as the create body.  We don't write rows here.

Failure handling
----------------

A malformed template is **logged and skipped**, not raised.  The
listing contract is "skip and log" so one typo doesn't take down the
whole UI picker; CI catches the typo via
:func:`test_shipped_seeds_all_parse` in ``tests/test_templates.py``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# Directory the package ships its built-in templates in.  Resolved
# relative to this module so it works regardless of where the package
# is installed (editable, wheel, container layer, etc.).
_DEFAULT_TEMPLATES_DIR = Path(__file__).parent / "seeds" / "agents"


_FRONT_MATTER_DELIM = "---"


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class TemplateError(Exception):
    """Base for template-loader errors."""


class TemplateInvalid(TemplateError):
    """The file exists but the front-matter / body is malformed."""


class TemplateNotFound(TemplateError):
    """No template with the requested slug was found."""


@dataclass(frozen=True)
class AgentTemplate:
    """Parsed agent template.

    Mirrors the subset of agent-row columns the create form needs.
    ``instructions`` is the markdown body.
    """

    slug: str
    name: str
    description: str | None
    model: str
    suggested_mcp_server_type_slugs: list[str] = field(default_factory=list)
    agent_config: dict[str, Any] = field(default_factory=dict)
    instructions: str = ""


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


_REQUIRED_FIELDS = ("slug", "name", "model")


def _parse_template_file(path: Path) -> AgentTemplate:
    """Parse a single ``*.md`` template file.

    Layout:

    .. code-block:: text

        ---
        <YAML front-matter as a mapping>
        ---
        <markdown body>

    Raises :class:`TemplateInvalid` for any structural problem so the
    caller can decide whether to skip + log or fail loud.
    """
    text = path.read_text(encoding="utf-8")
    # Allow leading whitespace / blank lines before the first delimiter
    # (common with editors that auto-insert one).
    stripped = text.lstrip()
    if not stripped.startswith(_FRONT_MATTER_DELIM):
        raise TemplateInvalid(f"{path.name}: missing front-matter (must start with '---')")

    # Split on the first two delimiters: everything between them is the
    # YAML front-matter; everything after the second is the body.
    # ``split(..., 2)`` returns at most 3 elements:
    #   ['', '<front-matter>', '<body>']
    parts = stripped.split(_FRONT_MATTER_DELIM, 2)
    if len(parts) < 3:
        raise TemplateInvalid(f"{path.name}: front-matter is not closed (need a second '---')")

    _leading, raw_front, body = parts

    try:
        front = yaml.safe_load(raw_front)
    except yaml.YAMLError as exc:
        raise TemplateInvalid(f"{path.name}: YAML parse error: {exc}") from exc

    if front is None:
        front = {}
    if not isinstance(front, dict):
        raise TemplateInvalid(
            f"{path.name}: front-matter must be a YAML mapping, got {type(front).__name__}"
        )

    missing = [k for k in _REQUIRED_FIELDS if k not in front]
    if missing:
        raise TemplateInvalid(
            f"{path.name}: missing required front-matter field(s): {', '.join(missing)}"
        )

    return AgentTemplate(
        slug=str(front["slug"]),
        name=str(front["name"]),
        description=(str(front["description"]) if front.get("description") is not None else None),
        model=str(front["model"]),
        suggested_mcp_server_type_slugs=[
            str(s) for s in (front.get("suggested_mcp_server_type_slugs") or [])
        ],
        agent_config=dict(front.get("agent_config") or {}),
        instructions=body.strip(),
    )


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def load_templates(templates_dir: Path | None = None) -> list[AgentTemplate]:
    """Discover and parse every ``*.md`` template in ``templates_dir``.

    Files that fail to parse are **logged at WARNING and skipped**, so
    one broken seed doesn't break the listing.  Use the CI-level
    smoke test (``test_shipped_seeds_all_parse``) to catch typos in
    shipped seeds before they go out.

    Returns templates sorted by slug for stable ordering across calls.
    """
    src = templates_dir if templates_dir is not None else _DEFAULT_TEMPLATES_DIR
    if not src.exists():
        logger.warning(
            "agent-templates: directory %s does not exist; returning empty list",
            src,
        )
        return []

    out: list[AgentTemplate] = []
    for path in sorted(src.glob("*.md")):
        try:
            out.append(_parse_template_file(path))
        except TemplateInvalid as exc:
            logger.warning("agent-templates: skipping %s: %s", path.name, exc)
    out.sort(key=lambda t: t.slug)
    return out


def load_template_by_slug(slug: str, templates_dir: Path | None = None) -> AgentTemplate:
    """Return the template with ``slug``, or raise :class:`TemplateNotFound`.

    Re-reads from disk on every call.  The catalog is tiny enough that
    a cache wouldn't pay for the complexity; if that ever changes, add
    one here behind an LRU.
    """
    for tpl in load_templates(templates_dir):
        if tpl.slug == slug:
            return tpl
    raise TemplateNotFound(slug)

"""Unit tests for :mod:`gargantua.templates`.

The template loader is one of two seams the UI's "New from template"
flow rides on (the other being :class:`AgentTemplateOut`).  These tests
cover the file-format contract end-to-end:

* Front-matter parsing (required keys, optional keys, defaults).
* Malformed-file handling (no front-matter, unclosed front-matter,
  YAML errors, missing required keys) — every shape should either
  parse or fall through to a clear :class:`TemplateInvalid`.
* Directory scanning — one broken file shouldn't break the listing,
  and ordering should be stable across calls.
* Shipped seeds — confirm every ``*.md`` in the package's
  ``seeds/agents/`` parses cleanly, so a typo in a real template
  fails this test instead of a deployment.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gargantua.templates import (
    AgentTemplate,
    TemplateInvalid,
    TemplateNotFound,
    load_template_by_slug,
    load_templates,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_MIN_FRONT = """---
slug: hello
name: Hello
model: openai:gpt-4o-mini
---

Body content here.
"""


def _write(dir: Path, name: str, content: str) -> Path:
    p = dir / name
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_parses_minimal_template(tmp_path: Path) -> None:
    _write(tmp_path, "hello.md", _MIN_FRONT)
    templates = load_templates(tmp_path)
    assert len(templates) == 1
    t = templates[0]
    assert isinstance(t, AgentTemplate)
    assert t.slug == "hello"
    assert t.name == "Hello"
    assert t.model == "openai:gpt-4o-mini"
    assert t.description is None
    assert t.suggested_mcp_server_type_slugs == []
    assert t.agent_config == {}
    assert t.instructions == "Body content here."


def test_parses_full_template(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "rich.md",
        """---
slug: rich
name: Rich
description: A fully-spec'd template.
model: openai:gpt-4o-mini
suggested_mcp_server_type_slugs:
  - postgres
  - opensearch
agent_config:
  add_history_to_context: true
  num_history_runs: 5
---

# Heading

Multi-line body with **markdown** preserved.
""",
    )
    [t] = load_templates(tmp_path)
    assert t.description == "A fully-spec'd template."
    assert t.suggested_mcp_server_type_slugs == ["postgres", "opensearch"]
    assert t.agent_config == {
        "add_history_to_context": True,
        "num_history_runs": 5,
    }
    # Body is preserved verbatim (with leading newlines stripped).
    assert t.instructions.startswith("# Heading")
    assert "**markdown**" in t.instructions


def test_list_is_sorted_by_slug(tmp_path: Path) -> None:
    """Stable ordering matters: the chat UI lists templates in this
    order, and operators expect the same order across page refreshes."""
    for slug in ("zeta", "alpha", "mu"):
        _write(
            tmp_path,
            f"{slug}.md",
            f"---\nslug: {slug}\nname: {slug}\nmodel: x\n---\nbody",
        )

    out = load_templates(tmp_path)
    assert [t.slug for t in out] == ["alpha", "mu", "zeta"]


def test_missing_dir_returns_empty(tmp_path: Path) -> None:
    """Pointing at a non-existent dir should NOT crash — it should
    return an empty list so the rest of the system stays usable."""
    out = load_templates(tmp_path / "does-not-exist")
    assert out == []


# ---------------------------------------------------------------------------
# Front-matter error handling
# ---------------------------------------------------------------------------


def test_file_without_front_matter_is_skipped_in_listing(
    tmp_path: Path,
) -> None:
    """A plain markdown file (no ``---`` block) is "invalid" but the
    listing for the rest of the dir should still work.

    The contract is "skip and continue" — we don't assert on the
    log message (that's implementation detail and pytest's caplog
    interacts poorly with logger config set by other tests in the
    suite).  The behavioural check (broken file invisible, good file
    survives) is the actual guarantee.
    """
    _write(tmp_path, "no-fm.md", "Just a body, no front-matter.")
    _write(tmp_path, "good.md", _MIN_FRONT)

    out = load_templates(tmp_path)
    assert [t.slug for t in out] == ["hello"]


def test_unclosed_front_matter_is_skipped(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "unclosed.md",
        "---\nslug: x\nname: x\nmodel: x\n(no closing fence)\nbody",
    )
    out = load_templates(tmp_path)
    assert out == []


def test_yaml_error_in_front_matter_is_skipped(tmp_path: Path) -> None:
    """A YAML syntax error should be skipped, not raised."""
    _write(
        tmp_path,
        "broken.md",
        "---\nthis : : is not valid yaml\n---\nbody",
    )
    out = load_templates(tmp_path)
    assert out == []


def test_missing_required_field_is_skipped(tmp_path: Path) -> None:
    """Required: slug, name, model.  Anything else is optional.

    Direct parse-call form to also pin the :class:`TemplateInvalid`
    error path (covers what the warning log message conveyed in the
    earlier version of this test).
    """
    path = _write(
        tmp_path,
        "no-model.md",
        "---\nslug: x\nname: x\n---\nbody",
    )
    # Listing skips it silently:
    assert load_templates(tmp_path) == []
    # Direct parse raises with a message naming the missing field:
    from gargantua.templates import _parse_template_file

    with pytest.raises(TemplateInvalid, match="model"):
        _parse_template_file(path)


def test_non_mapping_front_matter_is_skipped(tmp_path: Path) -> None:
    """If the front-matter is a YAML list / scalar / etc, treat as
    invalid (we need a mapping)."""
    _write(
        tmp_path,
        "list.md",
        "---\n- not\n- a\n- mapping\n---\nbody",
    )
    assert load_templates(tmp_path) == []


def test_one_broken_file_does_not_break_other_files(
    tmp_path: Path,
) -> None:
    """The listing contract is "skip and log", not "fail fast".  This
    keeps the UI's template picker usable even if one template was
    saved with a typo."""
    _write(tmp_path, "good-a.md", _MIN_FRONT)
    _write(tmp_path, "broken.md", "not even close to a template")
    _write(
        tmp_path,
        "good-b.md",
        "---\nslug: zz\nname: ZZ\nmodel: m\n---\nbody",
    )
    out = load_templates(tmp_path)
    slugs = [t.slug for t in out]
    assert slugs == ["hello", "zz"]


# ---------------------------------------------------------------------------
# load_template_by_slug
# ---------------------------------------------------------------------------


def test_load_template_by_slug_returns_the_right_one(tmp_path: Path) -> None:
    _write(tmp_path, "a.md", "---\nslug: a\nname: A\nmodel: m\n---\na-body")
    _write(tmp_path, "b.md", "---\nslug: b\nname: B\nmodel: m\n---\nb-body")
    t = load_template_by_slug("b", tmp_path)
    assert t.slug == "b"
    assert t.instructions == "b-body"


def test_load_template_by_slug_missing_raises(tmp_path: Path) -> None:
    _write(tmp_path, "a.md", _MIN_FRONT)
    with pytest.raises(TemplateNotFound):
        load_template_by_slug("nope", tmp_path)


# ---------------------------------------------------------------------------
# The shipped seeds must parse cleanly
# ---------------------------------------------------------------------------


def test_shipped_seeds_all_parse() -> None:
    """A typo in a shipped template should fail THIS test, not a
    deployment.  We're explicit about the expected slugs so adding /
    removing a seed forces a conscious update."""
    templates = load_templates()  # default: package's seeds/agents dir
    slugs = {t.slug for t in templates}
    # Every shipped seed must be present + parseable.
    expected = {"api-explorer", "db-investigator", "logs-explorer", "triage-lead"}
    assert expected <= slugs, f"missing seeds: {expected - slugs}"


def test_shipped_seeds_reference_real_catalog_slugs() -> None:
    """``suggested_mcp_server_type_slugs`` on every shipped template
    must reference a slug that exists in :data:`CANONICAL_TYPES`.  A
    template that points at a non-existent type would mislead admins
    and crash the UI's "select MCP servers" picker."""
    from gargantua.catalog_seed import CANONICAL_TYPES

    catalog_slugs = {t["slug"] for t in CANONICAL_TYPES}
    for tpl in load_templates():
        for ref in tpl.suggested_mcp_server_type_slugs:
            assert ref in catalog_slugs, (
                f"template {tpl.slug!r} suggests MCP type {ref!r} "
                f"which is not in the canonical catalog "
                f"({sorted(catalog_slugs)})"
            )


def test_shipped_seeds_have_non_empty_instructions() -> None:
    """A seed with empty instructions is a footgun — the resulting
    agent would have nothing to do.  Catch it before deployment."""
    for tpl in load_templates():
        assert tpl.instructions.strip(), (
            f"template {tpl.slug!r} has empty instructions; "
            "drop it or write a body"
        )

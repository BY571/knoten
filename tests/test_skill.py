"""SKILL.md is now the primary way an agent learns knoten. If it and the MCP server's
`instructions` disagree, one of the two surfaces is lying — the same enforced-duplication
check test_docs.py applies to the README.

The MCP server names TOOLS (`knoten_frontier`, `knoten_get`, ...); the skill names SHELL
COMMANDS (`knoten frontier`, `knoten show`, ...). Those strings can never match verbatim —
and forcing them to would make one of the two surfaces wrong for its own reader. What has
to match is the LOOP: the same six steps, in the same order, teaching the same commands
(`knoten_get` and `knoten show` are the same step — the CLI verb is `show`, `get` does
not exist).
"""
import re
from pathlib import Path

import pytest

pytest.importorskip("knoten.mcp_server", reason="the loop text lives there")
from knoten.mcp_server import INSTRUCTIONS

SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"

# CLI verbs the loop is allowed to talk about, and how a tool-name/verb collapses onto
# the "same step" the other surface uses. `get` is an MCP tool name; the CLI verb for the
# same step is `show`, so they alias onto one subject.
COMMANDS = {"frontier", "index", "query", "get", "show", "gates", "commit", "update", "attach"}
ALIAS = {"get": "show"}

STEP_START = re.compile(r"^(\d+)\.\s")
MENTION = re.compile(r"knoten[_ ]([a-z]+)")


def step_blocks(text):
    """Split text into the numbered-step paragraphs 1..N. A step's block is its opening
    line plus every following non-blank line, up to the next numbered line or a blank
    line — whichever ends the paragraph first. That keeps trailing prose after the last
    numbered step (INSTRUCTIONS has a paragraph after step 6) from bleeding into it."""
    blocks = {}
    current = None
    buf = []
    for raw in text.splitlines():
        stripped = raw.strip()
        m = STEP_START.match(stripped)
        if m:
            if current is not None:
                blocks[current] = "\n".join(buf)
            current = int(m.group(1))
            buf = [stripped]
        elif not stripped:
            if current is not None:
                blocks[current] = "\n".join(buf)
            current = None
            buf = []
        elif current is not None:
            buf.append(stripped)
    if current is not None:
        blocks[current] = "\n".join(buf)
    return blocks


def subjects(block):
    """The set of CLI commands / MCP tools a step's paragraph names, normalized so an MCP
    tool and its CLI verb count as the same subject."""
    return frozenset(ALIAS.get(w, w) for w in MENTION.findall(block) if w in COMMANDS)


def test_the_skill_exists_and_declares_itself():
    text = SKILL.read_text(encoding="utf-8")

    assert text.startswith("---")          # frontmatter
    assert "name:" in text and "description:" in text


def test_the_skill_carries_the_same_loop_as_the_server():
    server_steps = step_blocks(INSTRUCTIONS)
    skill_steps = step_blocks(SKILL.read_text(encoding="utf-8"))

    # If either extractor found nothing, every assertion below would pass vacuously.
    assert server_steps, "found no numbered steps in mcp_server.INSTRUCTIONS"
    assert skill_steps, "found no numbered steps in SKILL.md"

    server_order = sorted(server_steps)
    assert server_order == list(range(1, len(server_order) + 1)), \
        "INSTRUCTIONS' numbered steps aren't a contiguous 1..N sequence"
    assert sorted(skill_steps) == server_order, (
        f"SKILL.md has steps {sorted(skill_steps)}, INSTRUCTIONS has {server_order} — "
        "a step was added, dropped, or renumbered in only one surface"
    )

    for n in server_order:
        server_subjects = subjects(server_steps[n])
        skill_subjects = subjects(skill_steps[n])
        assert server_subjects, f"INSTRUCTIONS step {n} names no known command: {server_steps[n]!r}"
        assert skill_subjects, f"SKILL.md step {n} names no known command: {skill_steps[n]!r}"
        assert server_subjects == skill_subjects, (
            f"step {n} teaches different commands — "
            f"INSTRUCTIONS: {sorted(server_subjects)}, SKILL.md: {sorted(skill_subjects)}"
        )


def test_the_skill_names_every_cli_command_an_agent_needs():
    text = SKILL.read_text(encoding="utf-8")

    for cmd in ["knoten frontier", "knoten index", "knoten query", "knoten show",
                "knoten gates", "knoten commit", "knoten update", "knoten attach"]:
        assert cmd in text

    # `get` is an MCP tool name, not a CLI command — it must never appear as a shell
    # instruction here, because `knoten get` does not exist.
    assert "knoten get" not in text

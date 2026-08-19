"""SKILL.md is how an agent learns knoten, and now the only surface that teaches the loop.

It used to be checked against the MCP server's own instructions, step for step, so the two
could not drift. With that server gone there is nothing to compare against — so what is
left has to be pinned on its own: the steps are a contiguous sequence, each one names a
real command, and every command an agent needs appears somewhere in the file.
"""
import re
from pathlib import Path

import pytest


SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"

# CLI verbs the loop is allowed to talk about, and how a tool-name/verb collapses onto
# `get` was the tool name on the retired MCP surface; the CLI verb for the
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
    """The set of CLI commands a step's paragraph names, normalized so a legacy
    tool and its CLI verb count as the same subject."""
    return frozenset(ALIAS.get(w, w) for w in MENTION.findall(block) if w in COMMANDS)


def test_the_skill_exists_and_declares_itself():
    text = SKILL.read_text(encoding="utf-8")

    assert text.startswith("---")          # frontmatter
    assert "name:" in text and "description:" in text


def test_the_skill_steps_are_a_contiguous_numbered_loop():
    """A loop with a gap in it — or a step nobody can act on — is a loop an agent
    abandons halfway."""
    steps = step_blocks(SKILL.read_text(encoding="utf-8"))

    assert steps, "found no numbered steps in SKILL.md"
    assert sorted(steps) == list(range(1, len(steps) + 1)), \
        f"SKILL.md steps are not 1..N: {sorted(steps)}"
    for n, block in steps.items():
        assert subjects(block), f"SKILL.md step {n} names no known command: {block!r}"


def test_the_skill_names_every_cli_command_an_agent_needs():
    text = SKILL.read_text(encoding="utf-8")

    for cmd in ["knoten frontier", "knoten index", "knoten query", "knoten show",
                "knoten gates", "knoten commit", "knoten update", "knoten attach"]:
        assert cmd in text

    # `get` was a tool name on the retired surface, never a CLI command — it must not appear as a shell
    # instruction here, because `knoten get` does not exist.
    assert "knoten get" not in text

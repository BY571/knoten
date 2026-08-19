"""SKILL.md is how an agent learns knoten, and now the only surface that teaches the loop.

It used to be checked against a second surface's instructions, step for step, so the two
could not drift. With that surface gone there is nothing to compare against — so what is
left has to be pinned on its own: the steps are a contiguous sequence, each one names a
real command, and every command an agent needs appears somewhere in the file.
"""
import re
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"

# CLI verbs the loop is allowed to talk about, and how a tool-name/verb collapses onto
COMMANDS = {"frontier", "index", "query", "show", "gates", "commit", "update", "attach"}

STEP_START = re.compile(r"^(\d+)\.\s")
# Backticked on purpose: SKILL.md also says "knoten defines none of these words",
# and prose is not a command.
MENTION = re.compile(r"`knoten ([a-z]+)")


def step_blocks(text):
    """Split text into the numbered-step paragraphs 1..N. A step's block is its opening
    line plus every following non-blank line, up to the next numbered line or a blank
    line — whichever ends the paragraph first. That keeps trailing prose after the last
    numbered step, and a trailing paragraph after the last one, from bleeding into it."""
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
    """The set of CLI commands a step's paragraph names — the ones an agent has to
    tool and its CLI verb count as the same subject."""
    return frozenset(w for w in MENTION.findall(block) if w in COMMANDS)


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
    assert "knoten get" not in text


def test_every_command_the_skill_teaches_is_a_real_subcommand():
    """The guarantee that went missing when the cross-surface check was dropped. SKILL.md
    used to be checked against another DOCUMENT; nothing checked it against the code, so
    renaming a subcommand left the skill teaching a verb that does not exist and the whole
    suite green. This checks the parser itself, which is the thing that can drift."""
    from knoten.cli import _parser
    subs = next(a.choices for a in _parser()._actions if hasattr(a, "choices") and a.choices)
    taught = set(MENTION.findall(SKILL.read_text(encoding="utf-8")))

    unreal = sorted(v for v in taught if v not in subs)

    assert not unreal, f"SKILL.md teaches commands that do not exist: {unreal}"

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
    """Split text into the numbered-step paragraphs 1..N."""
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
    return frozenset(w for w in MENTION.findall(block) if w in COMMANDS)


def test_the_skill_exists_and_declares_itself():
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---")          # frontmatter
    assert "name:" in text and "description:" in text


def test_the_skill_steps_are_a_contiguous_numbered_loop():
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
    """The guarantee that went missing when the cross-surface check was dropped."""
    from knoten.cli import _parser
    subs = next(a.choices for a in _parser()._actions if hasattr(a, "choices") and a.choices)
    taught = set(MENTION.findall(SKILL.read_text(encoding="utf-8")))
    unreal = sorted(v for v in taught if v not in subs)
    assert not unreal, f"SKILL.md teaches commands that do not exist: {unreal}"

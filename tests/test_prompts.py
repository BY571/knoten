from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "src" / "knoten" / "prompts"

# Each stage names one stage before it, the thing it derives from. `question` and `gate`
# stand outside the loop: question is its root, gate the bar beside it, neither derives.
PARENT = {
    "source": "question",
    "idea": "source",
    "hypothesis": "idea",
    "experiment": "hypothesis",
    "finding": "experiment",
}
# The field only that stage carries; its absence would leave the node unhelpful.
HEADLINE = {
    "source": "origin",
    "idea": "false",
    "hypothesis": "kill",
    "experiment": "rerun",
    "finding": "reopen",
}
STAGES = {"question", "source", "idea", "hypothesis", "experiment", "finding", "gate"}


def read(name: str) -> str:
    return (PROMPTS / f"prompt-{name}.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", sorted(STAGES))
def test_every_stage_prompt_exists_and_is_not_empty(name):
    text = read(name)
    assert text.strip(), f"prompt-{name}.md is empty"


@pytest.mark.parametrize("name,parent", sorted(PARENT.items()))
def test_each_stage_names_the_stage_before_it(name, parent):
    assert parent in read(name).lower(), (
        f"prompt-{name}.md must name its stage before it, '{parent}'")


@pytest.mark.parametrize("name,field", sorted(HEADLINE.items()))
def test_each_stage_states_its_own_required_field(name, field):
    assert field in read(name).lower(), (
        f"prompt-{name}.md must state its required field, '{field}'")


def test_the_graph_docs_point_at_the_prompts():
    """SKILL.md is the loop."""
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert "prompts" in skill

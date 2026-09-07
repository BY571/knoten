"""The docs are executable."""
import re
import shutil
from pathlib import Path

import pytest

from knoten.cli import main
from knoten.core import FM_RE, load, parse_text
from knoten.validate import check, load_rules

ROOT = Path(__file__).resolve().parents[1]
DOCS = ["README.md", "SPEC.md"]
PROSE = ["README.md", "SKILL.md", "SPEC.md"]

# The closing fence must be the SAME length as the opening one. The README's node example
# is a ````markdown fence wrapping an inner ```python block; a naive `^`{3,}` closer stops
# at the inner fence and silently truncates the node.
FENCE = re.compile(r"^(`{3,})[\w-]*\n(.*?)^\1\s*$", re.S | re.M)


def blocks(doc):
    return [body for _, body in FENCE.findall((ROOT / doc).read_text(encoding="utf-8"))]


def node_blocks():
    return [(d, b) for d in DOCS for b in blocks(d) if b.startswith("---\n") and "type:" in b]


def rule_blocks():
    # `re.match` would miss a block that opens with a `#` comment, which SPEC's does.
    return [(d, b) for d in DOCS for b in blocks(d)
            if re.search(r"^(rules|node_types|statuses):", b, re.M)]


@pytest.fixture
def example(tmp_path):
    dst = tmp_path / "g"
    shutil.copytree(ROOT / "examples" / "llm-research", dst)
    return dst


def test_the_docs_actually_contain_the_examples_we_think_they_do():
    """If the extractor silently matches nothing, every test below passes vacuously."""
    assert len(node_blocks()) >= 1
    assert len(rule_blocks()) >= 2


@pytest.mark.parametrize("doc,block", node_blocks(), ids=lambda v: v if v in DOCS else "")
def test_a_documented_node_parses_and_obeys_the_example_graphs_rules(doc, block, example):
    # Written under the id the block declares, not a fixed name: the filename IS the id,
    # so renaming a documented node here would trip `mismatched-id` on the harness's own
    # doing rather than on anything the docs got wrong.
    node = parse_text(block, "doc-example")
    nid = str(node.frontmatter.get("id") or "doc-example")
    assert node.type
    (example / "nodes" / f"{nid}.md").write_text(block, encoding="utf-8")
    violations = [v for v in check(load(example), example) if v.node == nid]
    assert not violations, f"{doc}: documented node violates the example graph's own rules: " \
                           + "; ".join(f"[{v.rule}] {v.message}" for v in violations)


@pytest.mark.parametrize("doc,block", rule_blocks(), ids=lambda v: v if v in DOCS else "")
def test_a_documented_rules_block_is_accepted_by_the_engine(doc, block, tmp_path):
    (tmp_path / "graph.yaml").write_text(block, encoding="utf-8")
    load_rules(tmp_path)         # raises GraphError on an unknown key


@pytest.mark.parametrize("doc", PROSE)
def test_the_prose_docs_use_no_em_dashes(doc):
    text = (ROOT / doc).read_text(encoding="utf-8")
    assert "\u2014" not in text, f"{doc} contains an em dash"


@pytest.fixture
def readme_compression(example, tmp_path, monkeypatch, capsys):
    block = next(b for b in blocks("README.md") if "npx:supersedes" in b)
    fm, body = FM_RE.match(block).groups()
    (tmp_path / "fm").write_text(fm, encoding="utf-8")
    (tmp_path / "body").write_text(body, encoding="utf-8")
    monkeypatch.chdir(example)
    assert main(["commit", "finding-sc-needs-scale",
                 "--frontmatter", str(tmp_path / "fm"), "--body", str(tmp_path / "body")]) == 0
    return capsys.readouterr().out


def test_the_readmes_reward_block_is_what_the_command_actually_prints(readme_compression):
    """The numbers in that block are the reward the whole feature exists to hand out."""
    quoted = next(b for b in blocks("README.md") if b.startswith("  + nodes/"))
    # One contiguous slice, not line by line: a block whose lines all appear somewhere is
    # still a block nobody ever saw, and the order and the blank line are part of it.
    assert quoted.rstrip("\n") in readme_compression


def test_a_compression_is_not_warned_about_resembling_what_it_just_superseded(readme_compression):
    warned = readme_compression.split("! This resembles")[1:]
    assert warned, "the example no longer trips the resemblance warning at all"
    assert "finding-sc-small-models" not in warned[0]
    assert "finding-sc-large-models" not in warned[0]


def test_every_image_the_readme_shows_is_in_the_repo():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for src in re.findall(r'src="([^"]+)"', text):
        assert (ROOT / src).is_file(), src

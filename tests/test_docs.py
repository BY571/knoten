"""The docs are executable.

Earlier in this project's life, SPEC.md documented a rule syntax the engine did not
implement — so a graph copied from the spec parsed fine and enforced NOTHING. Then a new
rule was added and the README's own flagship node stopped satisfying it, and nobody
noticed until someone thought to check.

A tool whose thesis is "enforcement beats good intentions" should enforce its own
documentation. Every node and every rules block in README.md and SPEC.md is extracted and
run against the real example graph.
"""
import re
import shutil
from pathlib import Path

import pytest

from knoten.core import load, parse_text
from knoten.validate import check, load_rules

ROOT = Path(__file__).resolve().parents[1]
DOCS = ["README.md", "SPEC.md"]

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
    """A real copy of the shipped example graph, so a documented node is checked against
    the rules the example actually declares."""
    dst = tmp_path / "g"
    shutil.copytree(ROOT / "examples" / "llm-research", dst)
    return dst


def test_the_docs_actually_contain_the_examples_we_think_they_do():
    """If the extractor silently matches nothing, every test below passes vacuously."""
    assert len(node_blocks()) >= 1
    assert len(rule_blocks()) >= 2


@pytest.mark.parametrize("doc,block", node_blocks(), ids=lambda v: v if v in DOCS else "")
def test_a_documented_node_parses_and_obeys_the_example_graphs_rules(doc, block, example):
    """This is the check that would have caught the README's flagship node failing the
    `underpowered` rule the moment that rule was added."""
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
    """SPEC.md once documented `when: {status: alive}`, which the engine silently ignored.
    Any config we print must be config we accept."""
    (tmp_path / "graph.yaml").write_text(block, encoding="utf-8")

    load_rules(tmp_path)         # raises GraphError on an unknown key

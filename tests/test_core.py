"""Parsing. Every finding here was a silent failure: the parser skipped what it
could not understand, so a broken node looked like a valid one."""
import pytest

from knoten.core import GraphError, load, parse

FM = """\
id: hyp-x
type: hypothesis
status: dead
tags: [decoding, reasoning]
links:
  - {rel: kn:killedByGate, to: method-gate}
repro:
  script: experiments/x.py
  model: Qwen3-8B
  cmd: python x.py --n 5
results:
  acc_greedy: 0.741
  tokens_per_question: 1420
  n_independent: 1319
"""


def test_results_contains_only_the_results_block(graph):
    """Finding 4: every 2-space-indented key landed in `results`, so the whole
    `repro:` block (script/model/cmd) was reported as experimental results."""
    graph.node("hyp-x", FM)
    n = load(graph.root)["hyp-x"]

    assert set(n.results) == {"acc_greedy", "tokens_per_question", "n_independent"}
    assert n.repro["script"] == "experiments/x.py"
    assert "script" not in n.results


def test_results_keep_their_types(graph):
    """A regex parser makes everything a string, so no rule can ever compare a
    number. `underpowered = n<30` (SPEC 5) is unwritable against strings."""
    graph.node("hyp-x", FM)
    n = load(graph.root)["hyp-x"]

    assert n.results["acc_greedy"] == 0.741
    assert n.results["n_independent"] == 1319


def test_list_fields_parse_as_lists(graph):
    graph.node("hyp-x", FM)
    n = load(graph.root)["hyp-x"]

    assert n.frontmatter["tags"] == ["decoding", "reasoning"]


def test_edges_parse(graph):
    graph.node("hyp-x", FM)
    n = load(graph.root)["hyp-x"]

    assert n.links == [{"rel": "kn:killedByGate", "to": "method-gate"}]


def test_backlink_is_generated_on_the_target(graph):
    graph.node("hyp-x", FM).node("method-gate", "id: method-gate\ntype: method")
    nodes = load(graph.root)

    assert nodes["method-gate"].backlinks == [{"rel": "kn:gateKilled", "to": "hyp-x"}]


def test_status_stays_a_string_despite_yaml_1_1_coercion(graph):
    """PyYAML is YAML 1.1: bare `no`/`on`/`yes` coerce to booleans. A tag named
    `no` must not silently become False."""
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead\ntags: [no, on]")
    n = load(graph.root)["hyp-x"]

    assert n.frontmatter["tags"] == ["no", "on"]


def test_malformed_yaml_raises_instead_of_being_skipped(graph):
    """Finding 6: a node the parser could not read was silently dropped from the
    graph. A node that vanishes is worse than a node that errors."""
    (graph.root / "nodes" / "bad.md").write_text(
        "---\nid: bad\n  oops: bad indent\n---\n# body\n", encoding="utf-8"
    )
    with pytest.raises(GraphError, match="bad.md"):
        load(graph.root)


def test_node_without_frontmatter_raises(graph):
    (graph.root / "nodes" / "nofm.md").write_text("# just prose\n", encoding="utf-8")
    with pytest.raises(GraphError, match="frontmatter"):
        load(graph.root)


def test_parse_is_utf8_regardless_of_locale(graph, tmp_path):
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead", body="# köpke – ✓\n")
    n = load(graph.root)["hyp-x"]

    assert "köpke – ✓" in n.body

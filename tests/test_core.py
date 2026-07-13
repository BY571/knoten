"""Parsing. Every finding here was a silent failure: the parser skipped what it
could not understand, so a broken node looked like a valid one."""
import pytest

from knoten.core import GraphError, load

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


@pytest.mark.parametrize("line,expected", [
    ("wallclock: 12:30", "12:30"),      # YAML 1.1 sexagesimal -> 750
    ("t: 1:30.5", "1:30.5"),            #                      -> 90.5
    ("seed: 042", "042"),               # YAML 1.1 octal       -> 34
    ("n: 1_000", "1_000"),              # underscore digits    -> 1000
    ("run: 2024-01-15", "2024-01-15"),  # implicit timestamp   -> datetime.date
])
def test_yaml_1_1_does_not_silently_rewrite_a_result(graph, line, expected):
    """`results:` holds experimental numbers. A runtime of 12:30 recorded as `750`, or a
    seed of 042 as `34`, is a research-integrity bug — and it validated clean."""
    graph.node("hyp-x", f"id: hyp-x\ntype: hypothesis\nstatus: dead\nresults:\n  {line}")
    n = load(graph.root)["hyp-x"]

    assert list(n.results.values()) == [expected]


def test_real_numbers_still_parse_as_numbers(graph):
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead\nresults:\n"
                        "  acc: 0.741\n  n: 1319\n  neg: -3\n  exp: 1.2e-3\n  big: 0")
    r = load(graph.root)["hyp-x"].results

    assert r == {"acc": 0.741, "n": 1319, "neg": -3, "exp": 1.2e-3, "big": 0}

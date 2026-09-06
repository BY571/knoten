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
  - {rel: kn:killedByGate, to: gate-cost}
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
    graph.node("hyp-x", FM).node("gate-cost", "id: gate-cost\ntype: gate")
    nodes = load(graph.root)

    assert nodes["gate-cost"].backlinks == [{"rel": "kn:gateKilled", "to": "hyp-x"}]


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


from knoten.core import (backlink, compressible_types, is_general, load, question_of,
                         supersedes)


def _rooted(graph):
    graph.node("question-q", "id: question-q\ntype: question\nstatus: open", "# Q\n")
    graph.node("source-s", "id: source-s\ntype: source\nstatus: alive\norigin: x", "# S\n")
    graph.node("idea-i", "id: idea-i\ntype: idea\nstatus: open\nlinks:\n"
                         "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
                         "  - {rel: prov:wasDerivedFrom, to: source-s}", "# I\n")
    graph.node("hyp-h", "id: hyp-h\ntype: hypothesis\nstatus: open\nlinks:\n"
                        "  - {rel: prov:wasDerivedFrom, to: idea-i}", "# H\n")
    graph.node("exp-e", "id: exp-e\ntype: experiment\nstatus: alive\nlinks:\n"
                        "  - {rel: kn:tests, to: hyp-h}", "# E\n")
    graph.node("finding-f", "id: finding-f\ntype: finding\nstatus: alive\nlinks:\n"
                            "  - {rel: prov:wasDerivedFrom, to: exp-e}", "# F\n")
    graph.node("finding-lost", "id: finding-lost\ntype: finding\nstatus: alive", "# L\n")
    return backlink(load(graph.root))


def test_question_of_walks_derivation_and_tests_edges_up_to_the_question(graph):
    nodes = _rooted(graph)

    assert question_of(nodes, "finding-f") == "question-q"
    assert question_of(nodes, "hyp-h") == "question-q"
    assert question_of(nodes, "question-q") == "question-q"


def test_a_node_no_walk_reaches_a_question_from_is_unrooted(graph):
    nodes = _rooted(graph)

    assert question_of(nodes, "finding-lost") is None
    assert question_of(nodes, "source-s") is None


def test_question_of_survives_a_cycle(graph):
    graph.node("a", "id: a\ntype: idea\nstatus: open\nlinks:\n"
                    "  - {rel: prov:wasDerivedFrom, to: b}", "# a\n")
    graph.node("b", "id: b\ntype: idea\nstatus: open\nlinks:\n"
                    "  - {rel: prov:wasDerivedFrom, to: a}", "# b\n")

    assert question_of(backlink(load(graph.root)), "a") is None


def test_supersedes_lists_distinct_targets_and_two_make_a_general_node(graph):
    graph.node("f1", "id: f1\ntype: finding\nstatus: alive", "# 1\n")
    graph.node("f2", "id: f2\ntype: finding\nstatus: alive", "# 2\n")
    graph.node("one", "id: one\ntype: finding\nstatus: alive\nlinks:\n"
                      "  - {rel: npx:supersedes, to: f1}", "# one\n")
    graph.node("rule", "id: rule\ntype: finding\nstatus: alive\nlinks:\n"
                       "  - {rel: npx:supersedes, to: f2}\n"
                       "  - {rel: npx:supersedes, to: f1}\n"
                       "  - {rel: npx:supersedes, to: f1}", "# rule\n")
    nodes = load(graph.root)

    assert supersedes(nodes["rule"]) == ["f1", "f2"]
    assert supersedes(nodes["f1"]) == []
    assert is_general(nodes["rule"]) and not is_general(nodes["one"])


def test_compressible_types_default_to_finding():
    assert compressible_types({}) == ("finding",)
    assert compressible_types({"compressible": ["finding", "principle"]}) == ("finding", "principle")

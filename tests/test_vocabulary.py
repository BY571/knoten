import pytest

from knoten.core import GraphError, load
from knoten.validate import check, load_rules

VOCAB = """\
name: t
node_types: [hypothesis, gate]
statuses: [alive, dead, retracted, active]
rules: []
"""


def vocab(graph, text=VOCAB):
    (graph.root / "graph.yaml").write_text(text, encoding="utf-8")
    return graph


def test_a_type_outside_the_declared_vocabulary_is_a_violation(graph):
    vocab(graph).node("hyp-x", "id: hyp-x\ntype: hypthesis\nstatus: dead")
    violations = check(load(graph.root), graph.root)
    assert [v.rule for v in violations] == ["unknown-type"]
    assert "hypthesis" in violations[0].message


def test_a_status_outside_the_declared_vocabulary_is_a_violation(graph):
    """`status: ded` validated clean, then vanished from every query."""
    vocab(graph).node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: ded")

    violations = check(load(graph.root), graph.root)

    assert [v.rule for v in violations] == ["unknown-status"]
    assert "ded" in violations[0].message


def test_a_graph_that_declares_no_vocabulary_does_not_get_one(graph):
    """The core knows no domain. If you don't declare types, we don't invent them."""
    (graph.root / "graph.yaml").write_text("name: t\nrules: []\n", encoding="utf-8")
    graph.node("hyp-x", "id: hyp-x\ntype: whatever-i-like\nstatus: banana")

    assert check(load(graph.root), graph.root) == []


def test_unknown_top_level_key_in_graph_yaml_is_rejected(graph):
    """`node_type:` (singular) would have been the next silent no-op."""
    (graph.root / "graph.yaml").write_text(
        "name: t\nnode_type: [hypothesis]\nrules: []\n", encoding="utf-8")

    with pytest.raises(GraphError, match="node_type"):
        load_rules(graph.root)


def test_node_types_must_be_a_list(graph):
    (graph.root / "graph.yaml").write_text("name: t\nnode_types: hypothesis\n", encoding="utf-8")
    with pytest.raises(GraphError, match="node_types"):
        load_rules(graph.root)


# ------------------------------------------------------------------ tags

TAGGED = """\
name: t
node_types: [hypothesis, gate]
statuses: [alive, dead, retracted, active]
tags: [decoding, prompting, evaluation]
rules: []
"""


def test_a_tag_outside_the_declared_vocabulary_is_a_violation(graph):
    vocab(graph, TAGGED).node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead\n"
                                       "tags: [decodng]")
    violations = check(load(graph.root), graph.root)
    assert [v.rule for v in violations] == ["unknown-tag"]
    assert "decodng" in violations[0].message


def test_a_node_whose_tags_are_not_a_list_is_a_violation(graph):
    vocab(graph, TAGGED).node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead\n"
                                       "tags: decoding")
    violations = check(load(graph.root), graph.root)
    assert [v.rule for v in violations] == ["malformed-tags"]


# ------------------------------------------------- how a claim was reached, not just that

@pytest.mark.parametrize("rel,inverse", [
    ("kn:explains", "kn:explainedBy"),
    ("kn:generalises", "kn:generalisedBy"),
    ("kn:followsFrom", "kn:entails"),
])
def test_the_kind_of_a_derivation_is_a_relation_with_a_back_link(graph, rel, inverse):
    graph.node("find-a", "id: find-a\ntype: finding\nstatus: alive")
    graph.node("hyp-x", f"id: hyp-x\ntype: hypothesis\nstatus: open\nlinks:\n"
                        f"  - {{rel: {rel}, to: find-a}}")
    nodes = load(graph.root)
    assert [b["rel"] for b in nodes["find-a"].backlinks] == [inverse]
    assert check(nodes, graph.root) == []


# ------------------------------------- a graph may also say what its own words MEAN

MEANINGS = """\
name: t
node_types:
  hypothesis: a falsifiable claim derived from an idea
  gate: a standing rule every claim must survive
rules: []
"""


def test_a_meaning_that_is_not_a_sentence_is_rejected(graph):
    graph.rules("name: t\nnode_types:\n  hypothesis:\nrules: []\n")
    with pytest.raises(GraphError):
        check(load(graph.root), graph.root)


def test_a_list_of_one_key_mappings_is_caught(graph):
    graph.rules("name: t\nnode_types:\n  - hypothesis: a falsifiable claim\nrules: []\n")
    with pytest.raises(GraphError):
        check(load(graph.root), graph.root)

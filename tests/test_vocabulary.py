"""`node_types:` was declared in every graph.yaml and read by nothing, and `status` was
a free string. So `type: hypthesis` and `status: ded` both validated clean — and a claim
with a typo'd status drops out of every query, because query filters on the known set.

Config that looks like a constraint but enforces nothing is the same bug as a rule key
that enforces nothing. It just lives in a different file."""
import pytest

from knoten.core import GraphError, load
from knoten.validate import check, load_rules

VOCAB = """\
name: t
node_types: [hypothesis, method]
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


def test_a_node_with_no_type_is_a_violation(graph):
    vocab(graph).node("hyp-x", "id: hyp-x\nstatus: dead")

    violations = check(load(graph.root), graph.root)

    assert [v.rule for v in violations] == ["missing-type"]


def test_a_declared_vocabulary_passes(graph):
    vocab(graph).node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead")
    vocab(graph).node("method-g", "id: method-g\ntype: method\nstatus: active")

    assert check(load(graph.root), graph.root) == []


def test_omitting_status_is_a_violation_when_statuses_are_declared(graph):
    """The same fail-open as `status: ded`, reached by omission: a node with no status
    escapes every `when_status` rule AND never shows up in a query. If you declared a
    status vocabulary, you said nodes have one."""
    vocab(graph).node("src-x", "id: src-x\ntype: method")

    violations = check(load(graph.root), graph.root)

    assert [v.rule for v in violations] == ["missing-status"]


def test_status_is_not_required_when_the_graph_declares_no_statuses(graph):
    """Still no vocabulary invented by the core."""
    (graph.root / "graph.yaml").write_text(
        "name: t\nnode_types: [method]\nrules: []\n", encoding="utf-8")
    graph.node("src-x", "id: src-x\ntype: method")

    assert check(load(graph.root), graph.root) == []


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

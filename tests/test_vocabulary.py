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


# ------------------------------------------------------------------ tags

TAGGED = """\
name: t
node_types: [hypothesis, method]
statuses: [alive, dead, retracted, active]
tags: [decoding, prompting, evaluation]
rules: []
"""


def test_a_tag_outside_the_declared_vocabulary_is_a_violation(graph):
    """Tags are the axis that narrows a 5k-node graph to something an agent can read in
    one call. `tags: [decodng]` is a node that drops out of that filter forever — the
    same silent-disappearance bug as `status: ded`, one field over."""
    vocab(graph, TAGGED).node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead\n"
                                       "tags: [decodng]")

    violations = check(load(graph.root), graph.root)

    assert [v.rule for v in violations] == ["unknown-tag"]
    assert "decodng" in violations[0].message


def test_tags_are_unconstrained_when_the_graph_declares_none(graph):
    """The core invents no vocabulary (SPEC §2). Declaring no `tags:` means free tagging,
    exactly as `node_types` behaves."""
    vocab(graph).node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead\ntags: [anything]")

    assert check(load(graph.root), graph.root) == []


def test_every_declared_tag_on_a_node_is_checked(graph):
    """One good tag must not vouch for a bad one sitting beside it."""
    vocab(graph, TAGGED).node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead\n"
                                       "tags: [decoding, promting]")

    violations = check(load(graph.root), graph.root)

    assert [v.rule for v in violations] == ["unknown-tag"]
    assert "promting" in violations[0].message


def test_tags_must_be_a_list_in_graph_yaml(graph):
    vocab(graph, "name: t\ntags: decoding\nrules: []")

    with pytest.raises(GraphError, match="must be a list"):
        load_rules(graph.root)


def test_a_node_whose_tags_are_not_a_list_is_a_violation(graph):
    """`tags: decoding` (a bare string) is legal YAML and iterates as characters, which
    would report every letter as an unknown tag."""
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
    """knoten had one `prov:wasDerivedFrom`, which records THAT a claim came from
    something and never HOW. Rules match on the relation, so with one untyped derivation
    there is no way to demand three instances of a generalisation without demanding them
    of every derivation. A per-edge qualifier would not help: no rule key can see one."""
    graph.node("find-a", "id: find-a\ntype: finding\nstatus: alive")
    graph.node("hyp-x", f"id: hyp-x\ntype: hypothesis\nstatus: open\nlinks:\n"
                        f"  - {{rel: {rel}, to: find-a}}")

    nodes = load(graph.root)

    assert [b["rel"] for b in nodes["find-a"].backlinks] == [inverse]
    assert check(nodes, graph.root) == []

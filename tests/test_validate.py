"""The rules engine."""
import pytest
from conftest import compressible_graph

from knoten.core import GraphError, load
from knoten.validate import check, load_config, load_rules

ALIVE_NO_GATE = "id: hyp-x\ntype: hypothesis\nstatus: alive"


def rules(graph, text):
    (graph.root / "graph.yaml").write_text(text, encoding="utf-8")
    return graph


def test_unknown_edge_relation_is_a_violation(graph):
    graph.node("hyp-x", """\
id: hyp-x
type: hypothesis
status: dead
links:
  - {rel: kn:killdByGate, to: gate-cost}
""").node("gate-cost", "id: gate-cost\ntype: gate")
    violations = check(load(graph.root), graph.root)
    assert [v.rule for v in violations] == ["unknown-relation"]
    assert "kn:killdByGate" in violations[0].message


def test_authoring_a_back_link_by_hand_is_a_violation(graph):
    """Back-links are generated, never authored."""
    graph.node("hyp-x", """\
id: hyp-x
type: hypothesis
status: dead
links:
  - {rel: kn:gateKilled, to: gate-cost}
""").node("gate-cost", "id: gate-cost\ntype: gate")
    violations = check(load(graph.root), graph.root)
    assert [v.rule for v in violations] == ["authored-backlink"]


def test_dangling_edge_is_a_violation(graph):
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead\nlinks:\n  - {rel: kn:killedByGate, to: ghost}")
    violations = check(load(graph.root), graph.root)
    assert [v.rule for v in violations] == ["dangling-edge"]


def test_missing_attachment_is_a_violation(graph):
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead\nattachments:\n  - plot.png")
    violations = check(load(graph.root), graph.root)
    assert [v.rule for v in violations] == ["missing-attachment"]


def test_require_edge_rule_fires(graph):
    graph.node("hyp-x", ALIVE_NO_GATE)
    violations = check(load(graph.root), graph.root)
    assert [v.rule for v in violations] == ["live-claims-must-cite-their-gates"]


def test_require_sections_rule_fires(graph):
    rules(graph, """\
rules:
  - id: dead-claims-must-say-why
    when_status: dead, retracted
    require_sections: Why it died, reopen
    message: The post-mortem IS the asset.
""")
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead", body="# x\n## Why it died\nno signal\n")
    violations = check(load(graph.root), graph.root)
    assert [v.rule for v in violations] == ["dead-claims-must-say-why"]
    assert "reopen" in violations[0].message


def test_numeric_rule_can_compare_a_result(graph):
    rules(graph, """\
rules:
  - id: underpowered
    when_type: hypothesis
    require_result_min: {n_independent: 30}
    message: A t-stat on fewer than 30 independent bets is not evidence.
""")
    graph.node("hyp-small", "id: hyp-small\ntype: hypothesis\nstatus: alive\nresults:\n  n_independent: 12")
    graph.node("hyp-big", "id: hyp-big\ntype: hypothesis\nstatus: alive\nresults:\n  n_independent: 1319")
    violations = check(load(graph.root), graph.root)
    assert [v.node for v in violations] == ["hyp-small"]


def test_a_boolean_does_not_satisfy_a_numeric_floor(graph):
    (graph.root / "graph.yaml").write_text(
        "rules:\n  - id: r\n    require_result_min: {accuracy: 0.8}\n    message: m\n",
        encoding="utf-8")
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead\nresults:\n  accuracy: true")
    assert [v.rule for v in check(load(graph.root), graph.root)] == ["r"]


# ------------------------------------------------------ require_field_one_of

CAUSES = """\
name: t
rules:
  - id: deaths-must-name-a-cause
    when_status: dead
    require_field_one_of:
      cause: [no_signal, cost_hurdle, weak_baseline]
    message: A cause of death you cannot filter on is a story, not an index.
"""


def test_a_field_outside_the_declared_set_is_a_violation(graph):
    graph.rules(CAUSES).node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead\n"
                                      "cause: week_baseline")
    violations = check(load(graph.root), graph.root)
    assert [v.rule for v in violations] == ["deaths-must-name-a-cause"]
    assert "week_baseline" in violations[0].message


def test_a_missing_field_is_a_violation(graph):
    graph.rules(CAUSES).node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead")
    assert [v.rule for v in check(load(graph.root), graph.root)] == ["deaths-must-name-a-cause"]


def test_a_frontmatter_id_that_disagrees_with_the_filename_is_a_violation(graph):
    graph.node("hyp-x", "id: hyp-someone-else\ntype: hypothesis\nstatus: dead")
    violations = check(load(graph.root), graph.root)
    assert [v.rule for v in violations] == ["mismatched-id"]
    assert "hyp-someone-else" in violations[0].message


@pytest.mark.parametrize("block", ["results", "repro"])
def test_a_structured_block_that_is_a_scalar_is_a_violation_not_a_crash(graph, block):
    graph.rules("""\
name: t
rules:
  - id: underpowered
    when_type: hypothesis
    require_result_min: {n: 30}
    message: too few.
""").node("hyp-x", f"id: hyp-x\ntype: hypothesis\nstatus: dead\n{block}: 5")
    violations = check(load(graph.root), graph.root)
    assert f"malformed-{block}" in [v.rule for v in violations]


# ----------------------------------------------- rules can see what an edge points at

CASCADE = """\
name: t
rules:
  - id: methods-rest-on-live-claims
    when_type: method
    require_edge_target: {rel: prov:wasDerivedFrom, type: finding, status: alive}
    message: A method built on a dead finding is a method built on sand.
"""


def cascade(graph, finding_status):
    return (graph.rules(CASCADE)
            .node("find-a", f"id: find-a\ntype: finding\nstatus: {finding_status}")
            .node("method-x", "id: method-x\ntype: method\nstatus: alive\nlinks:\n"
                              "  - {rel: prov:wasDerivedFrom, to: find-a}"))


@pytest.mark.parametrize("bad", ["prov:used", "{rel: [a, b]}", "{type: finding}",
                                 "{rel: nope:invented}", "{rel: kn:gateKilled}",
                                 "{rel: prov:used, min: 0}", "{rel: prov:used, min: true}"])
def test_a_malformed_requirement_is_a_graph_error_not_a_crash(graph, bad):
    graph.rules(f"name: t\nrules:\n  - id: r\n    require_edge_target: {bad}\n")
    with pytest.raises(GraphError):
        check(load(graph.root), graph.root)


def test_citing_the_same_finding_twice_is_not_two_findings(graph):
    """`min` states an inductive standard — one observation is an anecdote."""
    graph.rules("""\
name: t
rules:
  - id: needs-instances
    when_type: hypothesis
    require_edge_target: {rel: prov:wasDerivedFrom, type: finding, min: 2}
    message: One observation is not a pattern.
""").node("find-a", "id: find-a\ntype: finding\nstatus: alive")
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: alive\nlinks:\n"
                        "  - {rel: prov:wasDerivedFrom, to: find-a}\n"
                        "  - {rel: prov:wasDerivedFrom, to: find-a}")
    assert [v.rule for v in check(load(graph.root), graph.root)] == ["needs-instances"]


# ------------------------------------------- rules can also look at what points AT a node

TESTED = """\
name: t
rules:
  - id: hypotheses-must-be-tested
    when_type: hypothesis
    when_status: alive
    require_backlink: {rel: kn:testedBy, type: experiment}
    message: An untested hypothesis is not alive, it is unexamined.
"""


def test_a_hypothesis_nobody_tested_is_reported(graph):
    graph.rules(TESTED).node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: alive")
    assert [v.rule for v in check(load(graph.root), graph.root)] == ["hypotheses-must-be-tested"]


def test_the_wrong_kind_of_neighbour_does_not_satisfy_it(graph):
    """A hypothesis someone merely commented on is still untested."""
    graph.rules(TESTED).node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: alive")
    graph.node("find-a", "id: find-a\ntype: finding\nstatus: alive\nlinks:\n"
                         "  - {rel: kn:tests, to: hyp-x}")

    assert [v.rule for v in check(load(graph.root), graph.root)] == ["hypotheses-must-be-tested"]


def test_naming_the_forward_relation_on_a_backlink_rule_is_an_error(graph):
    """The trap the two keys create together."""
    graph.rules("name: t\nrules:\n  - id: r\n    when_type: hypothesis\n"
                "    require_backlink: {rel: kn:tests}\n")
    with pytest.raises(GraphError):
        check(load(graph.root), graph.root)


# ------------------------------------------- a field that must be there, any value

ORIGIN = """\
name: t
rules:
  - id: sources-say-where-they-came-from
    when_type: source
    require_field: origin
    message: A source you cannot go back to is a rumour.
"""


def test_a_required_field_must_be_present(graph):
    """`require_field_one_of` needs a closed set, which a url or a doi does not have."""
    graph.rules(ORIGIN).node("src-x", "id: src-x\ntype: source\nstatus: open")
    assert [v.rule for v in check(load(graph.root), graph.root)] == ["sources-say-where-they-came-from"]


@pytest.mark.parametrize("value", ["", '""', "   "])
def test_an_empty_value_does_not_satisfy_it(graph, value):
    graph.rules(ORIGIN).node("src-x", f"id: src-x\ntype: source\nstatus: open\norigin: {value}")
    assert [v.rule for v in check(load(graph.root), graph.root)] == ["sources-say-where-they-came-from"]


def test_unless_edge_skips_the_rule_for_nodes_that_declare_that_relation(graph):
    rules(graph, """\
name: t
statuses: [alive]
node_types: [finding, experiment]
rules:
  - id: findings-come-from-experiments
    when_type: finding
    unless_edge: npx:supersedes
    require_edge_target: {rel: prov:wasDerivedFrom, type: experiment, min: 1}
    message: Cite the experiment.
""")
    graph.node("finding-a", "id: finding-a\ntype: finding\nstatus: alive", "# a\n")
    graph.node("finding-b", "id: finding-b\ntype: finding\nstatus: alive", "# b\n")
    graph.node("finding-g", "id: finding-g\ntype: finding\nstatus: alive\nlinks:\n"
                            "  - {rel: npx:supersedes, to: finding-a}\n"
                            "  - {rel: npx:supersedes, to: finding-b}",
               "# g\n\n## Covers\n- finding-a\n- finding-b\n")
    hit = {e.node for e in check(load(graph.root), graph.root) if e.rule == "findings-come-from-experiments"}
    assert hit == {"finding-a", "finding-b"}


def test_unless_edge_must_name_a_declared_relation(graph):
    rules(graph, "rules:\n  - id: u\n    unless_edge: kn:supersededBy\n    require_edge: x\n    message: m\n")
    with pytest.raises(GraphError, match="unless_edge"):
        load_rules(graph.root)


def test_a_rule_may_not_take_the_name_of_a_structural_check(graph):
    rules(graph, "name: t\nrules:\n  - id: supersession\n"
                 "    max_alive: {type: finding, count: 3}\n    message: m\n")
    with pytest.raises(GraphError, match="always runs"):
        load_config(graph.root)


def test_compressible_is_a_known_graph_key_and_must_list_declared_types(graph):
    rules(graph, "name: t\nnode_types: [finding, principle]\ncompressible: [finding, principle]\nrules: []\n")
    load_config(graph.root)
    rules(graph, "name: t\nnode_types: [finding]\ncompressible: [principle]\nrules: []\n")
    with pytest.raises(GraphError, match="compressible"):
        load_config(graph.root)
    rules(graph, "name: t\nnode_types: [finding]\ncompressible: finding\nrules: []\n")
    with pytest.raises(GraphError, match="compressible"):
        load_config(graph.root)


def _bar_graph(graph):
    compressible_graph(graph, n=2)
    graph.node("question-r", "id: question-r\ntype: question\nstatus: open", "# R\n")
    return graph


GOOD_GENERAL = ("id: finding-g\ntype: finding\nstatus: alive\nlinks:\n"
                "  - {rel: npx:supersedes, to: finding-1}\n"
                "  - {rel: npx:supersedes, to: finding-2}\n"
                "  - {rel: kn:survivedGate, to: gate-a}\n"
                "  - {rel: kn:survivedGate, to: gate-b}")
GOOD_COVERS = "# G\n\n## Covers\n- finding-1: the small case\n- finding-2: the large case\n"


def _bar(graph):
    return [e for e in check(load(graph.root), graph.root) if e.rule == "supersession"]


def test_a_target_must_be_alive(graph):
    _bar_graph(graph)
    graph.node("finding-2", "id: finding-2\ntype: finding\nstatus: dead\nlinks:\n"
                            "  - {rel: prov:wasDerivedFrom, to: question-q}", "# 2\n")
    graph.node("finding-g", GOOD_GENERAL, GOOD_COVERS)
    (err,) = _bar(graph)
    assert err.node == "finding-g" and "finding-2, which is dead, not alive" in err.message


def test_a_target_must_be_a_compressible_type(graph):
    _bar_graph(graph)
    graph.node("source-s", "id: source-s\ntype: source\nstatus: alive\norigin: x\nlinks:\n"
                           "  - {rel: prov:wasDerivedFrom, to: question-q}", "# S\n")
    graph.node("finding-g", GOOD_GENERAL.replace("to: finding-2", "to: source-s"),
               GOOD_COVERS.replace("finding-2", "source-s"))
    (err,) = _bar(graph)
    assert "source-s (source); only finding can be superseded" in err.message


def test_targets_must_stand_under_one_question(graph):
    _bar_graph(graph)
    graph.node("finding-2", "id: finding-2\ntype: finding\nstatus: alive\nlinks:\n"
                            "  - {rel: prov:wasDerivedFrom, to: question-r}\n"
                            "  - {rel: kn:survivedGate, to: gate-b}", "# 2\n")
    graph.node("finding-g", GOOD_GENERAL, GOOD_COVERS)
    (err,) = _bar(graph)
    assert "different questions (question-q, question-r)" in err.message


def test_an_unrooted_target_is_refused_by_name(graph):
    _bar_graph(graph)
    graph.node("finding-2", "id: finding-2\ntype: finding\nstatus: alive\nlinks:\n"
                            "  - {rel: kn:survivedGate, to: gate-b}", "# 2\n")
    graph.node("finding-g", GOOD_GENERAL, GOOD_COVERS)
    (err,) = _bar(graph)
    assert "cannot find the question finding-2 stands under" in err.message


def test_the_general_node_faces_the_union_of_the_gates(graph):
    _bar_graph(graph).node("finding-g", GOOD_GENERAL.replace(
        "  - {rel: kn:survivedGate, to: gate-b}", ""), GOOD_COVERS)
    (err,) = _bar(graph)
    assert "must survive gate-b, which finding-2 survived" in err.message


def test_covers_must_exist_and_name_every_target(graph):
    _bar_graph(graph).node("finding-g", GOOD_GENERAL, "# G\n")
    (err,) = _bar(graph)
    assert "no '## Covers' section" in err.message
    graph.node("finding-g", GOOD_GENERAL, "# G\n\n## Covers\n- finding-1: only\n")
    (err,) = _bar(graph)
    assert "## Covers of finding-g does not mention finding-2" in err.message


def test_a_node_that_supersedes_itself_is_refused_by_name(graph):
    _bar_graph(graph).node("finding-g", "id: finding-g\ntype: finding\nstatus: alive\nlinks:\n"
                                        "  - {rel: npx:supersedes, to: finding-g}", "# G\n")
    assert [e.message for e in _bar(graph)] == ["finding-g cannot supersede itself"]


def _mutual(a, b):
    return (f"id: {a}\ntype: finding\nstatus: alive\nlinks:\n"
            f"  - {{rel: npx:supersedes, to: {b}}}\n"
            "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
            "  - {rel: kn:survivedGate, to: gate-a}\n"
            "  - {rel: kn:survivedGate, to: gate-b}")


def test_two_nodes_that_supersede_each_other_are_refused_once(graph):
    _bar_graph(graph)
    graph.node("finding-1", _mutual("finding-1", "finding-2"),
               "# 1\n\n## Covers\n- finding-2: the other one\n")
    graph.node("finding-2", _mutual("finding-2", "finding-1"),
               "# 2\n\n## Covers\n- finding-1: the other one\n")
    errs = _bar(graph)
    assert [(e.node, e.message) for e in errs] == [
        ("finding-1", "finding-1 and finding-2 supersede each other")]


def test_a_target_already_superseded_by_another_node_is_refused(graph):
    _bar_graph(graph)
    graph.node("finding-3", "id: finding-3\ntype: finding\nstatus: superseded\nlinks:\n"
                            "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
                            "  - {rel: kn:survivedGate, to: gate-a}", "# 3\n")
    graph.node("finding-h", "id: finding-h\ntype: finding\nstatus: alive\nlinks:\n"
                            "  - {rel: npx:supersedes, to: finding-3}\n"
                            "  - {rel: kn:survivedGate, to: gate-a}",
               "# H\n\n## Covers\n- finding-3: x\n")
    graph.node("finding-g", "id: finding-g\ntype: finding\nstatus: alive\nlinks:\n"
                            "  - {rel: npx:supersedes, to: finding-1}\n"
                            "  - {rel: npx:supersedes, to: finding-3}\n"
                            "  - {rel: kn:survivedGate, to: gate-a}",
               "# G\n\n## Covers\n- finding-1: x\n- finding-3: y\n")
    msgs = [e.message for e in _bar(graph) if e.node == "finding-g"]
    assert any("finding-g supersedes finding-3, which finding-h already superseded" in m for m in msgs)


SUPERSEDED_1 = ("id: finding-1\ntype: finding\nstatus: superseded\nlinks:\n"
                "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
                "  - {rel: kn:survivedGate, to: gate-a}")
SUPERSEDED_2 = ("id: finding-2\ntype: finding\nstatus: superseded\nlinks:\n"
                "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
                "  - {rel: kn:survivedGate, to: gate-b}")


def _compressed(graph):
    _bar_graph(graph).node("finding-g", GOOD_GENERAL, GOOD_COVERS)
    graph.node("finding-1", SUPERSEDED_1)
    graph.node("finding-2", SUPERSEDED_2)
    return graph


def test_only_a_compressible_type_may_supersede(graph):
    _bar_graph(graph).node("hyp-g", GOOD_GENERAL.replace("id: finding-g", "id: hyp-g")
                                                .replace("type: finding", "type: hypothesis"),
                           GOOD_COVERS)
    assert "hyp-g (hypothesis) may not supersede; only finding can" in \
        [e.message for e in _bar(graph)]


def test_deleting_the_general_node_orphans_the_targets_it_retired(graph):
    _compressed(graph)
    (graph.root / "nodes" / "finding-g.md").unlink()
    assert [e.node for e in _bar(graph)] == ["finding-1", "finding-2"]
    assert all("is superseded by nothing" in e.message for e in _bar(graph))


OUTER = ("id: finding-outer\ntype: finding\nstatus: alive\nlinks:\n"
         "  - {rel: npx:supersedes, to: finding-g}\n"
         "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
         "  - {rel: kn:survivedGate, to: gate-a}\n"
         "  - {rel: kn:survivedGate, to: gate-b}")


def _recursive(graph):
    _compressed(graph)
    graph.node("finding-g", GOOD_GENERAL.replace("status: alive", "status: superseded"),
               GOOD_COVERS)
    graph.node("finding-outer", OUTER, "# outer\n\n## Covers\n- finding-g: the two cases\n")
    return graph


def test_a_rule_over_a_rule_validates(graph):
    assert _bar(_recursive(graph)) == []


def _ring_node(a, b):
    return (f"id: finding-{a}\ntype: finding\nstatus: alive\nlinks:\n"
            f"  - {{rel: npx:supersedes, to: finding-{b}}}\n"
            "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
            "  - {rel: kn:survivedGate, to: gate-a}\n"
            "  - {rel: kn:survivedGate, to: gate-b}")


def test_covers_matching_is_whole_token_not_substring(graph):
    _bar_graph(graph).node("finding-g", "id: finding-g\ntype: finding\nstatus: alive\nlinks:\n"
                                        "  - {rel: npx:supersedes, to: finding-1}\n"
                                        "  - {rel: kn:survivedGate, to: gate-a}",
                            "# G\n\n## Covers\n- finding-10: not the same node\n")
    msgs = [e.message for e in _bar(graph)]
    assert any("## Covers of finding-g does not mention finding-1" in m for m in msgs)

# ------------------------------------------- saying what a type must NOT carry

NO_RESULTS = """\
name: t
rules:
  - id: hypotheses-carry-no-results
    when_type: hypothesis
    forbid_fields: results, repro
    message: A hypothesis is a claim, not a run.
"""


def test_a_forbidden_field_is_a_violation(graph):
    graph.rules(NO_RESULTS).node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: open\n"
                                          "results:\n  auc: 0.9")
    assert [v.rule for v in check(load(graph.root), graph.root)] == ["hypotheses-carry-no-results"]



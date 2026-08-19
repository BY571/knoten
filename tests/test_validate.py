"""The rules engine. Its one job is to refuse. Every finding here made it
silently accept instead — the worst possible failure for a validator."""
import pytest

from knoten.core import GraphError, load
from knoten.validate import check, load_rules

ALIVE_NO_GATE = "id: hyp-x\ntype: hypothesis\nstatus: alive"


def rules(graph, text):
    (graph.root / "graph.yaml").write_text(text, encoding="utf-8")
    return graph


def test_unknown_rule_key_is_rejected(graph):
    """Finding 1: rule keys the engine did not recognise were parsed and then
    ignored, so a rule with a typo enforced NOTHING and validate said `all rules
    pass`. This is the exact syntax SPEC.md documented."""
    rules(graph, """\
rules:
  - id: live-claims-must-cite-their-gates
    when: {status: alive}
    require: {edge: kn:survivedGate}
    message: An unchallenged claim is not a hope.
""")
    with pytest.raises(GraphError, match="when"):
        load_rules(graph.root)


def test_rule_without_id_is_rejected(graph):
    rules(graph, "rules:\n  - when_status: alive\n    require_edge: kn:survivedGate\n")
    with pytest.raises(GraphError, match="id"):
        load_rules(graph.root)


def test_folded_message_survives(graph):
    """Finding 3: a `message: >` folded scalar was truncated to the literal
    string '>'. The message IS the product — it is what tells the next person
    why the rule exists."""
    rules(graph, """\
rules:
  - id: token-budget
    when_type: hypothesis
    require_result: tokens_per_question
    message: >
      An accuracy gain bought with extra inference compute is not a method,
      it is a bigger bill.
""")
    (msg,) = [r["message"] for r in load_rules(graph.root)]

    assert msg.startswith("An accuracy gain bought")
    assert "bigger bill" in msg


def test_unknown_edge_relation_is_a_violation(graph):
    """Finding 2: `kn:killdByGate` (one letter dropped) was accepted silently,
    produced no back-link, and passed validation. The hypothesis vanished from
    the graph — defeating the one question knoten exists to answer."""
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
    """Back-links are generated, never authored. Authoring one by hand creates a
    fact no generator will ever reconcile."""
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


def test_require_edge_rule_passes_when_gate_is_cited(graph):
    graph.node("hyp-x", """\
id: hyp-x
type: hypothesis
status: alive
links:
  - {rel: kn:survivedGate, to: gate-cost}
""").node("gate-cost", "id: gate-cost\ntype: gate")

    assert check(load(graph.root), graph.root) == []


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
    """Now that results keep their types, SPEC 5's `underpowered` (n<30) is
    finally expressible. It was not, against strings."""
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


def test_a_rule_with_a_non_numeric_floor_is_a_clean_error(graph):
    """Crashed with a raw TypeError, contradicting "a rule this engine cannot understand
    is a hard error"."""
    (graph.root / "graph.yaml").write_text(
        "rules:\n  - id: r\n    require_result_min: {n: '30'}\n", encoding="utf-8")

    with pytest.raises(GraphError, match="require_result_min"):
        load_rules(graph.root)


def test_require_result_min_must_be_a_mapping(graph):
    (graph.root / "graph.yaml").write_text(
        "rules:\n  - id: r\n    require_result_min: n_independent\n", encoding="utf-8")

    with pytest.raises(GraphError, match="require_result_min"):
        load_rules(graph.root)


def test_require_edge_must_be_a_string(graph):
    """Natural mistake — `when_status` accepts a list, so why not this? Raw TypeError:
    unhashable type 'list'."""
    (graph.root / "graph.yaml").write_text(
        "rules:\n  - id: r\n    require_edge: [kn:survivedGate]\n", encoding="utf-8")

    with pytest.raises(GraphError, match="require_edge"):
        load_rules(graph.root)


def test_a_boolean_does_not_satisfy_a_numeric_floor(graph):
    """`bool` is a subclass of `int`, so `accuracy: true` passed `require_result_min:
    {accuracy: 0.8}` as 1. Floors below 1 (accuracy, F1, AUC) are the common case."""
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
    """SPEC §5 defines seven causes of death and the engine could enforce none of them,
    so the cause lived only as prose and nothing could ask "what died of a weak
    baseline?" — the question that makes a research graph pay for itself."""
    graph.rules(CAUSES).node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead\n"
                                      "cause: week_baseline")

    violations = check(load(graph.root), graph.root)

    assert [v.rule for v in violations] == ["deaths-must-name-a-cause"]
    assert "week_baseline" in violations[0].message


def test_a_missing_field_is_a_violation(graph):
    graph.rules(CAUSES).node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead")

    assert [v.rule for v in check(load(graph.root), graph.root)] == ["deaths-must-name-a-cause"]


def test_a_declared_value_passes(graph):
    graph.rules(CAUSES).node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead\n"
                                      "cause: weak_baseline")

    assert check(load(graph.root), graph.root) == []


def test_the_rule_only_applies_where_it_says(graph):
    graph.rules(CAUSES).node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: alive")

    assert check(load(graph.root), graph.root) == []


def test_require_field_one_of_must_be_a_mapping_of_field_to_list(graph):
    graph.rules("name: t\nrules:\n  - id: r\n    require_field_one_of: cause")

    with pytest.raises(GraphError, match="require_field_one_of"):
        load_rules(graph.root)


def test_the_allowed_values_must_be_a_list(graph):
    graph.rules("name: t\nrules:\n  - id: r\n    require_field_one_of: {cause: no_signal}")

    with pytest.raises(GraphError, match="cause"):
        load_rules(graph.root)


def test_a_frontmatter_id_that_disagrees_with_the_filename_is_a_violation(graph):
    """The real id is the filename — `parse_text` takes it from the stem and the
    frontmatter `id:` is decorative. So a node whose `id:` says something else lies about
    itself to every human reading it, while every query still resolves it by its filename.
    Nothing caught that, which made `id` the one field an editor could corrupt silently."""
    graph.node("hyp-x", "id: hyp-someone-else\ntype: hypothesis\nstatus: dead")

    violations = check(load(graph.root), graph.root)

    assert [v.rule for v in violations] == ["mismatched-id"]
    assert "hyp-someone-else" in violations[0].message


def test_a_node_with_no_id_in_its_frontmatter_is_fine(graph):
    """`knoten commit` does not write one — the filename already carries it."""
    graph.node("hyp-x", "type: hypothesis\nstatus: dead")

    assert check(load(graph.root), graph.root) == []


@pytest.mark.parametrize("block", ["results", "repro"])
def test_a_structured_block_that_is_a_scalar_is_a_violation_not_a_crash(graph, block):
    """`results: 5` reached `n.results.get(key)` in the `require_result_min` loop and came
    back as `AttributeError: 'int' object has no attribute 'get'` — a traceback, from the
    validator whose entire job is refusing bad nodes politely. Reachable from `commit`
    long before `--field` existed; `--field` just made it easy to hit."""
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


def test_a_claim_resting_on_a_live_finding_passes(graph):
    g = cascade(graph, "alive")

    assert [v.rule for v in check(load(g.root), g.root)] == []


def test_the_day_the_finding_dies_everything_built_on_it_fails(graph):
    """The reason this primitive exists. `validate` re-runs over the whole graph, so this
    is not a create-time check — killing one result indicts everything standing on it.
    Before this, knoten recorded that a claim died and let its dependants stand."""
    g = cascade(graph, "dead")

    assert [v.rule for v in check(load(g.root), g.root)] == ["methods-rest-on-live-claims"]


def test_a_generalisation_can_demand_more_than_one_instance(graph):
    """`min:` is what makes an inductive standard writable: one observation is an
    anecdote. Without it a rule can only ask whether the edge exists at all."""
    graph.rules("""\
name: t
rules:
  - id: needs-instances
    when_type: hypothesis
    require_edge_target: {rel: prov:wasDerivedFrom, type: finding, min: 2}
    message: One observation is not a pattern.
""").node("find-a", "id: find-a\ntype: finding\nstatus: alive")
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: alive\nlinks:\n"
                        "  - {rel: prov:wasDerivedFrom, to: find-a}")

    assert [v.rule for v in check(load(graph.root), graph.root)] == ["needs-instances"]


def test_a_dangling_target_does_not_count_towards_the_requirement(graph):
    """A link to a node that does not exist is already `dangling-edge`. It must not also
    satisfy a requirement — that would let a typo stand in for evidence."""
    graph.rules(CASCADE).node("method-x", "id: method-x\ntype: method\nstatus: alive\n"
                              "links:\n  - {rel: prov:wasDerivedFrom, to: nope}")

    rules = [v.rule for v in check(load(graph.root), graph.root)]

    assert "methods-rest-on-live-claims" in rules


@pytest.mark.parametrize("bad", ["prov:used", "{rel: [a, b]}", "{type: finding}",
                                 "{rel: nope:invented}", "{rel: kn:gateKilled}",
                                 "{rel: prov:used, min: 0}", "{rel: prov:used, min: true}"])
def test_a_malformed_requirement_is_a_graph_error_not_a_crash(graph, bad):
    """Same bargain as every other rule key: the shape is checked once, loudly, rather
    than blowing up mid-validation on somebody's node.

    `kn:gateKilled` is the interesting one. It is a real relation — but a GENERATED
    back-link, which no node ever declares, so the rule would load cleanly and then fail
    every node forever with no hint that the direction was wrong. An always-firing rule
    is as corrosive as a never-firing one. `min: true` is here because `bool` is a
    subclass of `int` and would otherwise sail through as 1."""
    graph.rules(f"name: t\nrules:\n  - id: r\n    require_edge_target: {bad}\n")

    with pytest.raises(GraphError):
        check(load(graph.root), graph.root)


def test_citing_the_same_finding_twice_is_not_two_findings(graph):
    """`min` states an inductive standard — one observation is an anecdote. Counting
    EDGES rather than distinct targets let copy-paste satisfy it."""
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


def test_the_violation_says_what_was_wanted_and_what_was_found(graph):
    """The message is the product: an agent reads it and has to know what to do next."""
    graph.rules(CASCADE).node("method-x", "id: method-x\ntype: method\nstatus: alive")

    msg = next(v.message for v in check(load(graph.root), graph.root)
               if v.rule == "methods-rest-on-live-claims")

    assert "0 matching, need >= 1" in msg and "status=alive" in msg


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
    """The gap `require_edge` structurally cannot cover: rules see only what a node
    DECLARES, so they police the author of a claim and never what the graph failed to do
    next. That is the failure an autonomous loop actually has — not writing bad nodes,
    but abandoning good ones."""
    graph.rules(TESTED).node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: alive")

    assert [v.rule for v in check(load(graph.root), graph.root)] == ["hypotheses-must-be-tested"]


def test_a_hypothesis_with_an_experiment_passes(graph):
    graph.rules(TESTED).node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: alive")
    graph.node("exp-a", "id: exp-a\ntype: experiment\nstatus: alive\nlinks:\n"
                        "  - {rel: kn:tests, to: hyp-x}")

    assert [v.rule for v in check(load(graph.root), graph.root)] == []


def test_the_wrong_kind_of_neighbour_does_not_satisfy_it(graph):
    """A hypothesis someone merely commented on is still untested."""
    graph.rules(TESTED).node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: alive")
    graph.node("find-a", "id: find-a\ntype: finding\nstatus: alive\nlinks:\n"
                         "  - {rel: kn:tests, to: hyp-x}")

    assert [v.rule for v in check(load(graph.root), graph.root)] == ["hypotheses-must-be-tested"]


def test_naming_the_forward_relation_on_a_backlink_rule_is_an_error(graph):
    """The trap the two keys create together. `kn:tests` is what an experiment DECLARES;
    the back-link it generates is `kn:testedBy`. A rule asking for the forward name on
    the incoming side would load cleanly and then fail every hypothesis forever, with
    nothing in the message saying the direction was wrong."""
    graph.rules("name: t\nrules:\n  - id: r\n    when_type: hypothesis\n"
                "    require_backlink: {rel: kn:tests}\n")

    with pytest.raises(GraphError):
        check(load(graph.root), graph.root)

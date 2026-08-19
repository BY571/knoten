"""Moving a node through its own lifecycle.

An agent could open a hypothesis and never close it: `knoten commit` refuses to overwrite,
and nothing else could change a status. So the loop — open, run, record the verdict — had
no third step, and the agent's only outs were a raw file write (bypassing every gate) or a
second node leaving the first `open` forever.

Immutability protects what was CLAIMED. It was never meant to protect the status field,
which IS the lifecycle.
"""
import pytest

from knoten.core import GraphError, load
from knoten.update import update

RULES = """\
name: t
statuses: [open, alive, dead]
node_types: [hypothesis, gate]
rules:
  - id: live-claims-must-cite-their-gates
    when_status: alive
    require_edge: kn:survivedGate
    message: An unchallenged claim is not a finding, it is a hope.
  - id: dead-claims-must-say-why
    when_status: dead
    require_sections: Why it died
    message: The post-mortem IS the asset.
"""


@pytest.fixture
def g(graph):
    graph.rules(RULES)
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: open", "# A claim\n")
    graph.node("gate-cost", "id: gate-cost\ntype: gate\nstatus: open")
    return graph


def test_status_moves_and_persists(g):
    update(g.root, "hyp-x", status="dead", append="## Why it died\nnoise\n")

    assert load(g.root)["hyp-x"].status == "dead"


def test_a_transition_that_breaks_a_rule_is_refused(g):
    """The gate is the point and it must survive the new write path: `dead` without the
    post-mortem is exactly what `knoten commit` already refuses."""
    with pytest.raises(GraphError, match="dead-claims-must-say-why"):
        update(g.root, "hyp-x", status="dead")


def test_a_refused_update_does_not_touch_the_file(g):
    before = g.read("hyp-x")

    with pytest.raises(GraphError):
        update(g.root, "hyp-x", status="dead")

    assert g.read("hyp-x") == before


def test_marking_a_claim_alive_still_requires_a_gate(g):
    with pytest.raises(GraphError, match="live-claims-must-cite-their-gates"):
        update(g.root, "hyp-x", status="alive")

    update(g.root, "hyp-x", status="alive",
           links=[{"rel": "kn:survivedGate", "to": "gate-cost"}])

    assert load(g.root)["hyp-x"].status == "alive"


def test_body_text_is_appended_not_replaced(g):
    update(g.root, "hyp-x", append="## Why it died\nnoise\n")
    text = g.read("hyp-x")

    assert "# A claim" in text
    assert "## Why it died" in text


def test_results_merge_into_the_existing_block(g):
    g.node("hyp-y", "id: hyp-y\ntype: hypothesis\nstatus: open\nresults:\n  acc: 0.7")

    update(g.root, "hyp-y", results={"n_independent": 100})
    r = load(g.root)["hyp-y"].results

    assert r == {"acc": 0.7, "n_independent": 100}


def test_a_recorded_result_cannot_be_silently_rewritten(g):
    """Appending to a claim is the lifecycle. Rewriting a number that was already
    published is falsification of the record — that is what retraction is for."""
    g.node("hyp-y", "id: hyp-y\ntype: hypothesis\nstatus: open\nresults:\n  acc: 0.7")

    with pytest.raises(GraphError, match="acc"):
        update(g.root, "hyp-y", results={"acc": 0.9})


def test_rewriting_a_result_to_the_same_value_is_not_an_error(g):
    g.node("hyp-y", "id: hyp-y\ntype: hypothesis\nstatus: open\nresults:\n  acc: 0.7")

    update(g.root, "hyp-y", results={"acc": 0.7})     # idempotent retry, not a rewrite

    assert load(g.root)["hyp-y"].results == {"acc": 0.7}


def test_untouched_frontmatter_keeps_its_comments_and_order(g):
    """`attachments.set_list` goes out of its way not to re-dump frontmatter through
    yaml, because that strips what a human wrote. This write path must not undo that."""
    g.node("hyp-z", "id: hyp-z\ntype: hypothesis\n# hand-written note\nstatus: open\n"
                    "repro:\n  cmd: python x.py")

    update(g.root, "hyp-z", status="dead", append="## Why it died\nnoise\n")
    text = g.read("hyp-z")

    assert "# hand-written note" in text
    assert "cmd: python x.py" in text
    assert text.index("id: hyp-z") < text.index("status: dead")


def test_an_unknown_node_is_an_error(g):
    with pytest.raises(GraphError, match="hyp-nope"):
        update(g.root, "hyp-nope", status="dead")


def test_an_id_that_escapes_the_graph_is_refused(g):
    with pytest.raises(GraphError, match="not a valid node id"):
        update(g.root, "../../etc/passwd", status="dead")


def test_an_update_that_changes_nothing_is_an_error(g):
    """A no-op update means the agent thinks it recorded something and did not."""
    with pytest.raises(GraphError, match="nothing to change"):
        update(g.root, "hyp-x")


# ------------------------------------------------------------------ top-level fields

CAUSES = """\
name: t
statuses: [open, alive, dead]
node_types: [hypothesis, gate]
rules:
  - id: deaths-must-name-a-cause
    when_status: dead
    require_field_one_of:
      cause: [no_signal, weak_baseline]
    message: A cause of death you cannot filter on is a story, not an index.
"""


def test_a_hypothesis_can_be_closed_when_the_graph_demands_a_cause(graph):
    """The whole issue. `require_field_one_of` made a rule that the lifecycle could not
    satisfy: you learn a cause of death WHEN the experiment dies, but `update` could only
    reach `status`, `results`, `links` and prose — never a top-level field. A hypothesis
    that cannot be closed is the ghost-on-the-frontier problem, one field over."""
    graph.rules(CAUSES).node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: open", "# c\n")

    update(graph.root, "hyp-x", status="dead", fields={"cause": "weak_baseline"})

    n = load(graph.root)["hyp-x"]
    assert n.status == "dead"
    assert n.frontmatter["cause"] == "weak_baseline"


def test_a_field_that_is_absent_is_set(graph):
    """Also pins that `nothing to change` does not fire when a field is the only argument."""
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: open", "# c\n")

    update(graph.root, "hyp-x", fields={"cause": "no_signal"})

    assert load(graph.root)["hyp-x"].frontmatter["cause"] == "no_signal"


def test_a_field_already_recorded_can_be_changed(graph):
    """`fields` is a general editor by design decision: it sets any top-level key to any
    value, recorded or not. What stops a broken node is the same thing that stops one from
    `commit` — the candidate is validated in memory and never reaches disk if it fails.
    Correcting a published claim by retraction remains the convention; it is no longer
    enforced by this path."""
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: open\ncause: no_signal",
               "# c\n")

    update(graph.root, "hyp-x", fields={"cause": "weak_baseline"})

    assert load(graph.root)["hyp-x"].frontmatter["cause"] == "weak_baseline"


@pytest.mark.parametrize("key,value", [("type", "method"), ("status", "dead"),
                                       ("created", "2020-01-01")])
def test_any_top_level_key_can_be_set(graph, key, value):
    """No allow-list. `fields` reaches every top-level key — the rules decide what is
    acceptable, not a hardcoded list of names."""
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: open", "# c\n")

    update(graph.root, "hyp-x", fields={key: value})

    assert str(load(graph.root)["hyp-x"].frontmatter[key]) == value


def test_a_node_the_rules_reject_still_never_reaches_disk(graph):
    """The guard that remains, and the only one that ever mattered: capability is bounded
    by the graph's own declared rules, not by a list of field names."""
    graph.rules("""\
name: t
node_types: [hypothesis]
statuses: [open, dead]
rules: []
""").node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: open", "# c\n")
    before = graph.read("hyp-x")

    with pytest.raises(GraphError, match="status"):
        update(graph.root, "hyp-x", fields={"status": "bogus"})

    assert graph.read("hyp-x") == before


def test_a_frontmatter_id_that_would_disagree_with_the_filename_is_refused(graph):
    """`--field id=x` was the one silent corruption left once the allow-list went: the
    filename is the real id, so the node would have lied about itself while every query
    still resolved it. Now `validate` refuses it, on this path and any other."""
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: open", "# c\n")

    with pytest.raises(GraphError, match="mismatched-id"):
        update(graph.root, "hyp-x", fields={"id": "hyp-someone-else"})

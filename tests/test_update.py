"""Moving a node through its own lifecycle."""
import pytest

from knoten.core import GraphError, load
from knoten.update import update
from knoten.validate import check

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


def test_a_refused_update_does_not_touch_the_file(g):
    before = g.read("hyp-x")
    with pytest.raises(GraphError):
        update(g.root, "hyp-x", status="dead")
    assert g.read("hyp-x") == before


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
    """Appending to a claim is the lifecycle."""
    g.node("hyp-y", "id: hyp-y\ntype: hypothesis\nstatus: open\nresults:\n  acc: 0.7")
    with pytest.raises(GraphError, match="acc"):
        update(g.root, "hyp-y", results={"acc": 0.9})


def test_untouched_frontmatter_keeps_its_comments_and_order(g):
    g.node("hyp-z", "id: hyp-z\ntype: hypothesis\n# hand-written note\nstatus: open\n"
                    "repro:\n  cmd: python x.py")
    update(g.root, "hyp-z", status="dead", append="## Why it died\nnoise\n")
    text = g.read("hyp-z")
    assert "# hand-written note" in text
    assert "cmd: python x.py" in text
    assert text.index("id: hyp-z") < text.index("status: dead")


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


@pytest.mark.parametrize("key,value", [("type", "method"), ("status", "dead"),
                                       ("created", "2020-01-01")])
def test_any_top_level_key_can_be_set(graph, key, value):
    """No allow-list."""
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: open", "# c\n")
    update(graph.root, "hyp-x", fields={key: value})
    assert str(load(graph.root)["hyp-x"].frontmatter[key]) == value


def test_a_node_the_rules_reject_still_never_reaches_disk(graph):
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
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: open", "# c\n")
    with pytest.raises(GraphError, match="mismatched-id"):
        update(graph.root, "hyp-x", fields={"id": "hyp-someone-else"})


def test_update_that_adds_a_supersedes_link_flips_the_target(graph):
    graph.rules("""\
name: t
statuses: [open, alive, superseded]
node_types: [question, finding, gate]
rules: []
""")
    graph.node("question-q", "id: question-q\ntype: question\nstatus: open", "# Q\n")
    graph.node("gate-a", "id: gate-a\ntype: gate\nstatus: open", "# A\n")
    graph.node("finding-1", "id: finding-1\ntype: finding\nstatus: alive\nlinks:\n"
                            "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
                            "  - {rel: kn:survivedGate, to: gate-a}", "# 1\n")
    graph.node("finding-g", "id: finding-g\ntype: finding\nstatus: alive\nlinks:\n"
                            "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
                            "  - {rel: kn:survivedGate, to: gate-a}",
               "# G\n\n## Covers\n- finding-1: it\n")
    update(graph.root, "finding-g", links=[{"rel": "npx:supersedes", "to": "finding-1"}])
    assert load(graph.root)["finding-1"].status == "superseded"


def test_a_target_already_superseded_is_left_alone(graph):
    graph.rules("name: t\nstatuses: [alive, superseded]\nnode_types: [question, finding]\nrules: []\n")
    graph.node("question-q", "id: question-q\ntype: question\nstatus: alive", "# Q\n")
    graph.node("finding-1", "id: finding-1\ntype: finding\nstatus: superseded\nlinks:\n"
                            "  - {rel: prov:wasDerivedFrom, to: question-q}", "# 1\n")
    graph.node("finding-2", "id: finding-2\ntype: finding\nstatus: alive\nlinks:\n"
                            "  - {rel: prov:wasDerivedFrom, to: question-q}", "# 2\n")
    graph.node("finding-g", "id: finding-g\ntype: finding\nstatus: alive\nlinks:\n"
                            "  - {rel: prov:wasDerivedFrom, to: question-q}",
               "# G\n\n## Covers\n- finding-2: it\n")
    was = graph.read("finding-1")
    update(graph.root, "finding-g", links=[{"rel": "npx:supersedes", "to": "finding-2"}])
    assert graph.read("finding-1") == was


def test_a_plain_status_change_does_not_cascade_to_a_dependant(graph):
    graph.rules("""\
name: t
statuses: [open, alive, dead]
node_types: [hypothesis, note]
rules:
  - id: needs-an-alive-support
    when_type: note
    require_edge_target: {rel: mp:supports, status: alive, min: 1}
    message: A note needs an alive support.
""")
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: alive", "# X\n")
    graph.node("note-x", "id: note-x\ntype: note\nstatus: open\nlinks:\n"
                        "  - {rel: mp:supports, to: hyp-x}", "# N\n")
    status = update(graph.root, "hyp-x", status="dead")
    assert status == "dead"
    nodes = load(graph.root)
    assert nodes["hyp-x"].status == "dead"
    violations = check(nodes, graph.root)
    assert any(v.rule == "needs-an-alive-support" and v.node == "note-x" for v in violations)

"""Every question the graph answers, as a dict."""
from conftest import compressible_graph

from knoten import ops
from knoten.core import load
from knoten.validate import check


def test_get_reports_an_attachments_size_from_disk(graph):
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead\n"
                        "attachments:\n  - plot.png\n  - gone.png")
    graph.attachment("hyp-x", "plot.png", content="x" * 2048)
    res = ops.get(graph.root, "hyp-x")
    by_path = {a["path"]: a for a in res["attachment_files"]}
    assert by_path["attachments/hyp-x/plot.png"]["size_kb"] == 2.0
    assert by_path["attachments/hyp-x/gone.png"]["missing"] is True


def test_ops_update_that_adds_a_supersedes_link_reports_the_compression(graph):
    compressible_graph(graph, n=4)
    graph.node("finding-g", "id: finding-g\ntype: finding\nstatus: alive\nlinks:\n"
                            "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
                            "  - {rel: kn:survivedGate, to: gate-a}\n"
                            "  - {rel: kn:survivedGate, to: gate-b}",
               "# G\n\n## Covers\n- finding-1: small\n- finding-2: large\n")
    res = ops.update(graph.root, "finding-g",
                     links=[{"rel": "npx:supersedes", "to": "finding-1"},
                            {"rel": "npx:supersedes", "to": "finding-2"}])
    assert res["status"] == "UPDATED"
    assert res["compressed"]["targets"] == ["finding-1", "finding-2"]


def test_ops_update_omits_an_already_superseded_target_from_flipped(graph):
    graph.rules("""\
name: t
statuses: [open, alive, superseded]
node_types: [question, finding, gate]
rules: []
""")
    graph.node("question-q", "id: question-q\ntype: question\nstatus: open", "# Q\n")
    graph.node("gate-a", "id: gate-a\ntype: gate\nstatus: open", "# A\n")
    graph.node("finding-1", "id: finding-1\ntype: finding\nstatus: superseded\nlinks:\n"
                            "  - {rel: prov:wasDerivedFrom, to: question-q}", "# 1\n")
    graph.node("finding-2", "id: finding-2\ntype: finding\nstatus: alive\nlinks:\n"
                            "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
                            "  - {rel: kn:survivedGate, to: gate-a}", "# 2\n")
    graph.node("finding-g", "id: finding-g\ntype: finding\nstatus: alive\nlinks:\n"
                            "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
                            "  - {rel: kn:survivedGate, to: gate-a}",
               "# G\n\n## Covers\n- finding-1: it\n- finding-2: it\n")
    res = ops.update(graph.root, "finding-g",
                     links=[{"rel": "npx:supersedes", "to": "finding-1"},
                            {"rel": "npx:supersedes", "to": "finding-2"}])
    assert res["compressed"]["targets"] == ["finding-1", "finding-2"]
    assert res["compressed"]["flipped"] == ["finding-2"]


def test_ops_update_gates_bonus_is_false_when_the_union_does_not_exceed_the_richest_target(graph):
    graph.rules("""\
name: t
statuses: [open, alive, superseded]
node_types: [question, finding, gate]
rules: []
""")
    graph.node("question-q", "id: question-q\ntype: question\nstatus: open", "# Q\n")
    graph.node("gate-a", "id: gate-a\ntype: gate\nstatus: open", "# A\n")
    graph.node("gate-b", "id: gate-b\ntype: gate\nstatus: open", "# B\n")
    graph.node("finding-1", "id: finding-1\ntype: finding\nstatus: alive\nlinks:\n"
                            "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
                            "  - {rel: kn:survivedGate, to: gate-a}\n"
                            "  - {rel: kn:survivedGate, to: gate-b}", "# 1\n")
    graph.node("finding-2", "id: finding-2\ntype: finding\nstatus: alive\nlinks:\n"
                            "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
                            "  - {rel: kn:survivedGate, to: gate-a}", "# 2\n")
    graph.node("finding-g", "id: finding-g\ntype: finding\nstatus: alive\nlinks:\n"
                            "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
                            "  - {rel: kn:survivedGate, to: gate-a}\n"
                            "  - {rel: kn:survivedGate, to: gate-b}",
               "# G\n\n## Covers\n- finding-1: it\n- finding-2: it\n")
    res = ops.update(graph.root, "finding-g",
                     links=[{"rel": "npx:supersedes", "to": "finding-1"},
                            {"rel": "npx:supersedes", "to": "finding-2"}])
    assert res["compressed"]["gates"] == 2
    assert res["compressed"]["gates_bonus"] is False


def test_ops_update_refuses_before_write_when_the_flip_breaks_a_third_party_node(graph):
    graph.rules("""\
name: t
statuses: [open, alive, dead, superseded]
node_types: [question, finding, gate, note]
rules:
  - id: needs-two-alive-supports
    when_type: note
    require_edge_target: {rel: mp:supports, status: alive, min: 2}
    message: A note needs two alive supports.
""")
    graph.node("question-q", "id: question-q\ntype: question\nstatus: open", "# Q\n")
    graph.node("gate-a", "id: gate-a\ntype: gate\nstatus: open", "# A\n")
    graph.node("gate-b", "id: gate-b\ntype: gate\nstatus: open", "# B\n")
    graph.node("finding-1", "id: finding-1\ntype: finding\nstatus: alive\nlinks:\n"
                            "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
                            "  - {rel: kn:survivedGate, to: gate-a}", "# 1\n\nThe claim 1.\n")
    graph.node("finding-2", "id: finding-2\ntype: finding\nstatus: alive\nlinks:\n"
                            "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
                            "  - {rel: kn:survivedGate, to: gate-b}", "# 2\n\nThe claim 2.\n")
    graph.node("note-x", "id: note-x\ntype: note\nstatus: open\nlinks:\n"
                        "  - {rel: mp:supports, to: finding-1}\n"
                        "  - {rel: mp:supports, to: finding-2}", "# X\n")
    graph.node("finding-g", "id: finding-g\ntype: finding\nstatus: alive\nlinks:\n"
                            "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
                            "  - {rel: kn:survivedGate, to: gate-a}\n"
                            "  - {rel: kn:survivedGate, to: gate-b}",
               "# G\n\n## Covers\n- finding-1: small\n- finding-2: large\n")
    before = {nid: graph.read(nid) for nid in ("finding-g", "finding-1", "finding-2", "note-x")}
    res = ops.update(graph.root, "finding-g",
                     links=[{"rel": "npx:supersedes", "to": "finding-1"},
                            {"rel": "npx:supersedes", "to": "finding-2"}])
    assert res["status"] == "REJECTED"
    for nid, text in before.items():
        assert graph.read(nid) == text


def test_a_supersedes_edge_written_through_fields_flips_its_targets_too(graph):
    compressible_graph(graph, n=2)
    graph.node("finding-g", "id: finding-g\ntype: finding\nstatus: alive\nlinks:\n"
                            "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
                            "  - {rel: kn:survivedGate, to: gate-a}\n"
                            "  - {rel: kn:survivedGate, to: gate-b}",
               "# G\n\n## Covers\n- finding-1: small\n- finding-2: large\n")
    res = ops.update(graph.root, "finding-g", fields={"links": [
        {"rel": "prov:wasDerivedFrom", "to": "question-q"},
        {"rel": "kn:survivedGate", "to": "gate-a"},
        {"rel": "kn:survivedGate", "to": "gate-b"},
        {"rel": "npx:supersedes", "to": "finding-1"},
        {"rel": "npx:supersedes", "to": "finding-2"}]})
    assert res["compressed"]["flipped"] == ["finding-1", "finding-2"]
    nodes = load(graph.root)
    assert nodes["finding-1"].status == nodes["finding-2"].status == "superseded"


def test_retracting_a_general_node_leaves_its_targets_superseded(graph):
    """The known property: a retracted rule stops covering, and only a person can decide
    whether to revive the targets it retired."""
    compressible_graph(graph, n=2)
    graph.node("finding-g", "id: finding-g\ntype: finding\nstatus: alive\nlinks:\n"
                            "  - {rel: npx:supersedes, to: finding-1}\n"
                            "  - {rel: npx:supersedes, to: finding-2}\n"
                            "  - {rel: kn:survivedGate, to: gate-a}\n"
                            "  - {rel: kn:survivedGate, to: gate-b}",
               "# G\n\n## Covers\n- finding-1: small\n- finding-2: large\n")
    ops.update(graph.root, "finding-g", append="a line")      # flips both targets
    res = ops.update(graph.root, "finding-g", status="retracted")
    assert res["status"] == "UPDATED"
    assert res.get("compressed") is None
    nodes = load(graph.root)
    assert nodes["finding-1"].status == nodes["finding-2"].status == "superseded"
    assert check(nodes, graph.root) == []
    for tid in ("finding-1", "finding-2"):
        assert ops.update(graph.root, tid, status="alive")["status"] == "UPDATED"
    assert check(load(graph.root), graph.root) == []

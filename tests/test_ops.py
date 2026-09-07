"""Every question the graph answers, as a dict. One implementation; the CLI renders it,
--json dumps it. These lived twice before — once per surface — and drifted."""
from conftest import compressible_graph

from knoten import ops
from knoten.core import load


def test_index_returns_one_shape_both_renderings_share(graph):
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead\ntags: [decoding]",
               "# A claim\n")

    res = ops.index(graph.root)

    assert res["total"] == 1
    assert res["truncated"] is False
    assert res["nodes"] == [{"id": "hyp-x", "type": "hypothesis", "verdict": "DEAD",
                             "tags": ["decoding"], "title": "A claim"}]
    assert res["declared_tags"] == []


def test_get_reports_an_attachments_size_from_disk(graph):
    """`show` used to print size / MISSING from the filesystem directly — additive here
    so both surfaces (not just the CLI) can see it, not just a bare path string."""
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead\n"
                        "attachments:\n  - plot.png\n  - gone.png")
    graph.attachment("hyp-x", "plot.png", content="x" * 2048)

    res = ops.get(graph.root, "hyp-x")

    by_path = {a["path"]: a for a in res["attachment_files"]}
    assert by_path["attachments/hyp-x/plot.png"]["size_kb"] == 2.0
    assert by_path["attachments/hyp-x/gone.png"]["missing"] is True


def test_ops_update_that_adds_a_supersedes_link_reports_the_compression(graph):
    """`ops.update` gains the same `compressed` key `ops.commit`-equivalent (`commit()`)
    already carries, once the update itself is what adds the `npx:supersedes` edge."""
    compressible_graph(graph, n=4, cap=4)
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
    """A target that is already superseded is left alone, and does not appear in
    `flipped` -- but it still counts as one of the general node's `targets`."""
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


def test_ops_update_reports_no_budget_without_a_max_alive_rule(graph):
    """`free`/`count` are None when the graph declares no `max_alive` rule for the
    target's type: there is no budget to report against."""
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

    res = ops.update(graph.root, "finding-g",
                     links=[{"rel": "npx:supersedes", "to": "finding-1"}])

    assert res["compressed"]["free"] is None and res["compressed"]["count"] is None


def test_ops_update_gates_bonus_is_false_when_the_union_does_not_exceed_the_richest_target(graph):
    """`gates_bonus` rewards SURPASSING the richest single target's gate count, not
    merely matching it. A general node that survives exactly the union its targets
    already forced it to (the bar itself requires the union, never more) earns no
    bonus when that union equals the single richest target's own count."""
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
    """The flip can invalidate a node that is neither the general node nor one of its
    targets: `note-x` here rests on two alive supports, and flipping both to superseded
    breaks it. The whole write must refuse before anything lands — `finding-g` and both
    targets stay byte-identical to what they were before the call."""
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


def test_bringing_a_node_back_alive_past_the_cap_is_refused(graph):
    """`update` writes as truly as `commit`, and `fields` sets any top-level key -- so an
    author could revive a node past a full cap and backdate it in the same call, leaving
    the violation on somebody else's node and the write on disk."""
    compressible_graph(graph, n=3, cap=3)
    graph.node("finding-4", "id: finding-4\ntype: finding\nstatus: dead\n"
                            "created: 2001-01-01\nlinks:\n"
                            "  - {rel: prov:wasDerivedFrom, to: question-q}", "# 4\n")

    res = ops.update(graph.root, "finding-4", status="alive",
                     fields={"updated": "2001-01-02"})

    assert res["status"] == "REJECTED"
    assert "4 alive finding under question-q, budget 3" in res["reason"]
    assert load(graph.root)["finding-4"].status == "dead"


def test_a_supersedes_edge_written_through_fields_flips_its_targets_too(graph):
    """The targets to flip are read off the candidate, not off the `links` argument:
    `fields={"links": [...]}` declares the edge just as truly, and reading the argument
    left the general node claiming to have retired findings the graph still counted."""
    compressible_graph(graph, n=2, cap=4)
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

"""Filing a claim."""

from conftest import compressible_graph

from knoten import commit as commit_mod
from knoten.commit import commit
from knoten.core import load

RULES = """\
name: t
statuses: [open, alive, dead]
node_types: [hypothesis, gate]
rules:
  - id: live-claims-must-cite-their-gates
    when_status: alive
    require_edge: kn:survivedGate
    message: An unchallenged claim is not a finding, it is a hope.
"""

ALIVE_CITING = ("type: hypothesis\nstatus: alive\n"
                "links:\n  - {rel: kn:survivedGate, to: gate-new}")

def test_a_violating_node_never_reaches_disk(graph):
    graph.rules(RULES)
    res = commit(graph.root, "hyp-x", "type: hypothesis\nstatus: alive", "# A claim\n")
    assert res["status"] == "REJECTED"
    assert not (graph.root / "nodes" / "hyp-x.md").exists()

def test_commit_validates_the_graph_as_it_is_when_the_lock_is_held(graph, monkeypatch):
    graph.rules(RULES)
    real_lock = commit_mod.graph_lock
    def peer_commits_first(root):
        graph.node("gate-new", "id: gate-new\ntype: gate\nstatus: open")
        monkeypatch.setattr(commit_mod, "graph_lock", real_lock)   # once, not every call
        return real_lock(root)
    monkeypatch.setattr(commit_mod, "graph_lock", peer_commits_first)
    res = commit(graph.root, "hyp-x", ALIVE_CITING, "# A claim\n")
    assert res["status"] == "COMMITTED", res

def test_an_existing_node_is_not_overwritten(graph):
    graph.rules(RULES).node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: open")
    res = commit(graph.root, "hyp-x", "type: hypothesis\nstatus: open", "# again\n")
    assert res["status"] == "REJECTED"
    assert "already exists" in res["reason"]

def test_an_id_that_escapes_the_graph_is_refused(graph, tmp_path):
    graph.rules(RULES)
    res = commit(graph.root, "../../pwned", "type: hypothesis\nstatus: open", "# x\n")
    assert res["status"] == "REJECTED"
    assert not (tmp_path.parent / "pwned.md").exists()


# ------------------------------------------------------------- the duplicate warning

def test_commit_warns_when_the_new_claim_resembles_a_settled_one(graph):
    graph.node("hyp-self-consistency", "id: hyp-self-consistency\ntype: hypothesis\n"
                                       "status: dead",
               "# Self-consistency majority vote beats greedy decoding\n\n"
               "## Why it died\nThe gain was compute, not method.\n")
    res = commit(graph.root, "hyp-sample-and-vote", "type: hypothesis\nstatus: open",
                 "# Majority vote over sampled chains beats greedy decoding\n")
    assert res["status"] == "COMMITTED"          # a warning, never a block
    assert [s["id"] for s in res["similar"]] == ["hyp-self-consistency"]
    assert res["similar"][0]["verdict"] == "DEAD"
    assert "supersede" in res["warning"]


def test_a_malformed_candidate_is_refused_not_raised(graph):
    res = commit(graph.root, "hyp-bad", "id: hyp-bad\n  oops: bad indent", "# x\n")
    assert res["status"] == "REJECTED"
    assert not (graph.root / "nodes" / "hyp-bad.md").exists()


GENERAL = ("type: finding\nstatus: alive\nlinks:\n"
           "  - {rel: npx:supersedes, to: finding-1}\n"
           "  - {rel: npx:supersedes, to: finding-2}\n"
           "  - {rel: kn:survivedGate, to: gate-a}\n"
           "  - {rel: kn:survivedGate, to: gate-b}")
COVERS = "# G\n\n## Covers\n- finding-1: small\n- finding-2: large\n"


def test_committing_a_general_node_flips_its_targets_and_keeps_their_claims(graph):
    compressible_graph(graph, n=2)
    before = {i: graph.read(f"finding-{i}") for i in (1, 2)}
    res = commit(graph.root, "finding-g", GENERAL, COVERS)
    assert res["status"] == "COMMITTED"
    nodes = load(graph.root)
    assert nodes["finding-1"].status == nodes["finding-2"].status == "superseded"
    for i in (1, 2):
        after = graph.read(f"finding-{i}")
        assert f"The claim {i}." in after and after.startswith("---\n")
        assert "\n## Superseded\nsuperseded by finding-g on " in after
        assert before[i].split("---")[2].strip() in after      # the body survives verbatim


def test_a_compression_reports_what_it_freed(graph):
    compressible_graph(graph, n=4)
    res = commit(graph.root, "finding-g", GENERAL, COVERS)
    c = res["compressed"]
    assert c["targets"] == ["finding-1", "finding-2"]
    assert c["gates"] == 2 and c["gates_bonus"] is True
    assert c["question"] == "question-q"
    assert (c["rules"], c["specifics"]) == (1, 2)


def test_a_target_whose_filename_is_not_a_legal_id_is_refused_not_raised(graph):
    """`commit` promises a refusal an agent can read rather than a traceback."""
    compressible_graph(graph, n=1)
    (graph.root / "nodes" / "Finding-A.md").write_text(
        "---\nid: Finding-A\ntype: finding\nstatus: alive\nlinks:\n"
        "  - {rel: prov:wasDerivedFrom, to: question-q}\n---\n\n# A\n", encoding="utf-8")
    res = commit(graph.root, "finding-g",
                 "type: finding\nstatus: alive\nlinks:\n"
                 "  - {rel: npx:supersedes, to: Finding-A}\n"
                 "  - {rel: npx:supersedes, to: finding-1}\n"
                 "  - {rel: kn:survivedGate, to: gate-a}",
                 "# G\n\n## Covers\n- Finding-A: a\n- finding-1: b\n")
    assert res["status"] == "REJECTED"
    assert "not a valid node id" in res["reason"]
    assert not (graph.root / "nodes" / "finding-g.md").exists()


def test_a_single_replacement_reports_one_line_worth(graph):
    compressible_graph(graph, n=2)
    one = ("type: finding\nstatus: alive\nlinks:\n"
           "  - {rel: npx:supersedes, to: finding-1}\n"
           "  - {rel: kn:survivedGate, to: gate-a}")
    res = commit(graph.root, "finding-r", one, "# R\n\n## Covers\n- finding-1: replaced\n")
    assert res["compressed"]["targets"] == ["finding-1"]
    assert load(graph.root)["finding-1"].status == "superseded"


def test_a_graph_without_superseded_status_refuses_the_compression(graph):
    graph.rules("""\
name: t
statuses: [open, alive, dead]
node_types: [question, finding, gate]
rules: []
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
    res = commit(graph.root, "finding-g", GENERAL, COVERS)
    assert res["status"] == "REJECTED"
    assert not (graph.root / "nodes" / "finding-g.md").exists()
    nodes = load(graph.root)
    assert nodes["finding-1"].status == "alive" and nodes["finding-2"].status == "alive"


SECTION_RULES = """\
name: t
statuses: [open, alive, dead, superseded]
node_types: [question, finding, gate]
rules:
  - id: superseded-explains-itself
    when_status: superseded
    require_sections: [Why it was superseded]
    message: A superseded claim must say why.
"""


THIRD_PARTY_RULES = """\
name: t
statuses: [open, alive, dead, superseded]
node_types: [question, finding, gate, note]
rules:
  - id: needs-two-alive-supports
    when_type: note
    require_edge_target: {rel: mp:supports, status: alive, min: 2}
    message: A note needs two alive supports.
"""


def test_a_third_party_node_broken_by_the_flip_refuses_the_compression(graph):
    compressible_graph(graph, n=2)
    graph.rules(THIRD_PARTY_RULES)
    graph.node("note-x", "id: note-x\ntype: note\nstatus: open\nlinks:\n"
                         "  - {rel: mp:supports, to: finding-1}\n"
                         "  - {rel: mp:supports, to: finding-2}", "# X\n")
    res = commit(graph.root, "finding-g", GENERAL, COVERS)
    assert res["status"] == "REJECTED"
    assert any(v["rule"] == "needs-two-alive-supports" and v["node"] == "note-x"
              for v in res["violations"])
    assert not (graph.root / "nodes" / "finding-g.md").exists()
    nodes = load(graph.root)
    assert nodes["finding-1"].status == "alive" and nodes["finding-2"].status == "alive"


PRINCIPLE_RULES = """\
name: t
statuses: [open, alive, dead, superseded]
node_types: [question, finding, gate, principle]
compressible: [finding, principle]
rules:
  - id: compress-before-you-accumulate
    max_alive: {type: finding, per: question, count: 4}
    message: Compress first.
"""



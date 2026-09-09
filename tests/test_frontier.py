"""What should I work on next?"""
import pytest

from conftest import compressible_graph

from knoten.core import frontier, load

DEAD_WITH_OFFER = """\
id: hyp-dead
type: hypothesis
status: dead
"""

BODY_WITH_OFFER = """\
# A claim

## Why it died
Within noise.

## What would reopen this
A task where the aggregation does real work.
"""


@pytest.fixture
def g(graph):
    graph.node("hyp-open", "id: hyp-open\ntype: hypothesis\nstatus: open", "# Untested\n")
    graph.node("hyp-dead", DEAD_WITH_OFFER, BODY_WITH_OFFER)
    graph.node("gate-used", "id: gate-used\ntype: gate\nstatus: active", "# Gate A\n")
    graph.node("gate-idle", "id: gate-idle\ntype: gate\nstatus: active", "# Gate B\n")
    graph.node("hyp-killed", "id: hyp-killed\ntype: hypothesis\nstatus: dead\nlinks:\n"
                             "  - {rel: kn:killedByGate, to: gate-used}", "# Killed\n")
    graph.node("hyp-unchecked-b", "id: hyp-unchecked-b\ntype: hypothesis\nstatus: alive",
              "# Nobody has checked this\n")
    graph.node("finding-unchecked-a", "id: finding-unchecked-a\ntype: finding\nstatus: alive",
              "# Nor this\n")
    graph.node("finding-checked", "id: finding-checked\ntype: finding\nstatus: alive\nlinks:\n"
                                  "  - {rel: kn:survivedGate, to: gate-used}", "# Checked\n")
    return graph


def test_open_claims_are_the_first_bucket(g):
    assert [n.id for n in frontier(load(g.root))["open"]] == ["hyp-open"]


def test_a_dead_claim_that_said_what_would_reopen_it_is_reopenable(g):
    out = frontier(load(g.root))["reopenable"]
    assert [n.id for n, _ in out] == ["hyp-dead"]
    assert out[0][1].startswith("A task where the aggregation")


def test_a_gate_nothing_has_been_through_is_surfaced(g):
    assert [n.id for n in frontier(load(g.root))["untested_gates"]] == ["gate-idle"]


def test_alive_claims_with_no_gate_are_unchecked_in_id_order(g):
    assert [n.id for n in frontier(load(g.root))["unchecked"]] == \
        ["finding-unchecked-a", "hyp-unchecked-b"]


# ------------------------------------------------------------------ surfaces


def test_the_cli_prints_all_buckets(g, monkeypatch, capsys):
    from knoten.cli import main
    monkeypatch.chdir(g.root)
    assert main(["frontier"]) == 0
    out = capsys.readouterr().out
    assert "hyp-open" in out
    assert "UNCHECKED" in out and "hyp-unchecked-b" in out
    assert "hyp-dead" in out and "A task where" in out
    assert "gate-idle" in out


def test_three_alive_findings_sharing_a_gate_or_a_tag_are_a_cluster(graph):
    compressible_graph(graph, n=3, gates=1, tag="lr", start=0)
    f = frontier(load(graph.root))
    shared = {tuple(c["shared"].items())[0] for c in f["compressible"]}
    assert shared == {("gate", "gate-a"), ("tag", "lr")}
    assert all(c["ids"] == ["finding-0", "finding-1", "finding-2"] for c in f["compressible"])
    assert all(c["question"] == "question-q" for c in f["compressible"])


def test_clusters_are_ordered_biggest_first_and_superseded_nodes_are_out(graph):
    compressible_graph(graph, n=4, gates=1, tag="lr", start=0)
    graph.node("finding-3", "id: finding-3\ntype: finding\nstatus: alive\ntags: [batch]\nlinks:\n"
                            "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
                            "  - {rel: kn:survivedGate, to: gate-a}", "# 3\n")
    graph.node("finding-0", "id: finding-0\ntype: finding\nstatus: superseded\ntags: [lr]\nlinks:\n"
                            "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
                            "  - {rel: kn:survivedGate, to: gate-a}", "# 0\n")
    clusters = frontier(load(graph.root))["compressible"]
    assert [(c["shared"], c["ids"]) for c in clusters] == [
        ({"gate": "gate-a"}, ["finding-1", "finding-2", "finding-3"])]      # lr has 2 left


def test_unrooted_findings_never_cluster(graph):
    compressible_graph(graph, n=0, gates=1)
    for i in range(3):
        graph.node(f"finding-{i}", f"id: finding-{i}\ntype: finding\nstatus: alive\ntags: [lr]", f"# {i}\n")
    assert frontier(load(graph.root))["compressible"] == []



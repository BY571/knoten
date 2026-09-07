"""`knoten index` — the whole graph as one line per node."""
import pytest

from conftest import compressible_graph

from knoten import ops
from knoten.core import find_root


def index(**args):
    return ops.index(find_root(), **args)


@pytest.fixture(autouse=True)
def cwd(graph, monkeypatch):
    monkeypatch.chdir(graph.root)
    (graph.root / "graph.yaml").write_text(
        "name: t\nnode_types: [hypothesis, gate]\n"
        "statuses: [open, alive, dead, active]\nrules: []\n", encoding="utf-8")
    graph.node("hyp-alpha", "id: hyp-alpha\ntype: hypothesis\nstatus: open\n"
                            "tags: [decoding]", "# Alpha beats greedy\n")
    graph.node("hyp-beta", "id: hyp-beta\ntype: hypothesis\nstatus: dead\n"
                           "tags: [prompting]", "# Beta improves accuracy\n")
    graph.node("gate-cost", "id: gate-cost\ntype: gate\nstatus: active",
               "# Gate: compute-matched baseline\n")
    return graph


def test_a_row_carries_the_claim_not_just_the_id(cwd):
    """An id alone cannot be judged for relatedness. The H1 IS the claim."""
    row = next(r for r in (index())["nodes"] if r["id"] == "hyp-alpha")

    assert row["title"] == "Alpha beats greedy"
    assert row["verdict"] == "open"       # not a verdict yet, so the raw status
    assert row["type"] == "hypothesis"
    assert row["tags"] == ["decoding"]


def test_index_reports_the_graphs_declared_tags(cwd):
    """The agent must know which tags it can filter on before it can filter."""
    (cwd.root / "graph.yaml").write_text(
        "name: t\ntags: [decoding, prompting]\nrules: []\n", encoding="utf-8")

    assert (index())["declared_tags"] == ["decoding", "prompting"]


def test_truncation_is_loud(cwd):
    for i in range(30):
        cwd.node(f"hyp-{i:03d}", f"id: hyp-{i:03d}\ntype: hypothesis\nstatus: open",
                 f"# claim {i}\n")
    res = index(limit=5)
    assert len(res["nodes"]) == 5
    assert res["total"] == 33
    assert res["truncated"] is True
    assert "narrow" in res["note"].lower()


def _layered(graph):
    compressible_graph(graph, n=3, gates=1)
    for i in (1, 2):
        graph.node(f"finding-{i}", f"id: finding-{i}\ntype: finding\nstatus: superseded\nlinks:\n"
                                   "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
                                   "  - {rel: kn:survivedGate, to: gate-a}", f"# {i}\n")
    graph.node("finding-g", "id: finding-g\ntype: finding\nstatus: alive\nlinks:\n"
                            "  - {rel: npx:supersedes, to: finding-1}\n"
                            "  - {rel: npx:supersedes, to: finding-2}\n"
                            "  - {rel: kn:survivedGate, to: gate-a}",
               "# G\n\n## Covers\n- finding-1: a\n- finding-2: b\n")


def test_superseded_nodes_are_hidden_by_default_and_counted(graph):
    _layered(graph)
    p = index()
    # The autouse `cwd` fixture already wrote hyp-alpha/hyp-beta/gate-cost to this same
    # tmp_path before `_layered` ran; `_layered` only overwrites graph.yaml, so those
    # three node FILES are still on disk and `load()` reads every file in nodes/
    # regardless of what graph.yaml currently declares. None of them is superseded, so
    # they survive the hiding step alongside the four nodes this test is actually about.
    assert [n["id"] for n in p["nodes"]] == [
        "finding-g", "finding-3", "gate-a", "gate-cost", "hyp-alpha", "hyp-beta", "question-q"]
    assert p["hidden"] == 2 and p["total"] == 7


def test_a_general_node_is_listed_first_with_the_verdict_rule(graph):
    _layered(graph)
    rows = index()["nodes"]
    assert rows[0]["id"] == "finding-g" and rows[0]["verdict"] == "rule"
    assert rows[1]["verdict"] == "ALIVE"

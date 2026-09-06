"""`knoten index` — the whole graph as one line per node.

The agent surface answered "has this been tried?" and nothing else. It could not answer
"what is still open?", and a broad `knoten query` returned every matching node in full:
on a 500-node graph that was ~83k tokens in one response, so the tool got LESS usable
the more it accumulated, which is backwards for a thing whose purpose is to accumulate.

One line per node is ~15 tokens. A tag-filtered slice of a 5k-node graph fits in a single
call, and the agent — which is already an LLM — does the semantic matching itself.
"""
import pytest

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


def test_index_lists_every_node(cwd):
    res = index()

    assert {r["id"] for r in res["nodes"]} == {"hyp-alpha", "hyp-beta", "gate-cost"}


def test_a_row_carries_the_claim_not_just_the_id(cwd):
    """An id alone cannot be judged for relatedness. The H1 IS the claim."""
    row = next(r for r in (index())["nodes"] if r["id"] == "hyp-alpha")

    assert row["title"] == "Alpha beats greedy"
    assert row["verdict"] == "open"       # not a verdict yet, so the raw status
    assert row["type"] == "hypothesis"
    assert row["tags"] == ["decoding"]


def test_a_row_carries_the_verdict_for_a_settled_claim(cwd):
    row = next(r for r in (index())["nodes"] if r["id"] == "hyp-beta")

    assert row["verdict"] == "DEAD"


def test_index_filters_by_status(cwd):
    """"What is still open?" — unanswerable before, because query needed a search term
    and `status` is not prose."""
    res = index(status=["open"])

    assert [r["id"] for r in res["nodes"]] == ["hyp-alpha"]


def test_index_filters_by_tag(cwd):
    res = index(tags=["prompting"])

    assert [r["id"] for r in res["nodes"]] == ["hyp-beta"]


def test_index_filters_by_type(cwd):
    res = index(type=["gate"])

    assert [r["id"] for r in res["nodes"]] == ["gate-cost"]


def test_index_reports_the_graphs_declared_tags(cwd):
    """The agent must know which tags it can filter on before it can filter."""
    (cwd.root / "graph.yaml").write_text(
        "name: t\ntags: [decoding, prompting]\nrules: []\n", encoding="utf-8")

    assert (index())["declared_tags"] == ["decoding", "prompting"]


def test_truncation_is_loud(cwd):
    """A silent cap reads as "that is the whole graph" — the same false-negative as the
    AND-query bug, arriving by a different route."""
    for i in range(30):
        cwd.node(f"hyp-{i:03d}", f"id: hyp-{i:03d}\ntype: hypothesis\nstatus: open",
                 f"# claim {i}\n")

    res = index(limit=5)

    assert len(res["nodes"]) == 5
    assert res["total"] == 33
    assert res["truncated"] is True
    assert "narrow" in res["note"].lower()


def test_an_untruncated_index_says_so(cwd):
    res = index()

    assert res["truncated"] is False
    assert res["total"] == 3


def test_index_filters_on_an_arbitrary_frontmatter_field(cwd):
    """"Re-open everything that died of a weak baseline" is a query if the cause is a
    field, and a re-read of every post-mortem if it is prose."""
    cwd.node("hyp-w", "id: hyp-w\ntype: hypothesis\nstatus: dead\ncause: weak_baseline",
             "# Weak\n")
    cwd.node("hyp-n", "id: hyp-n\ntype: hypothesis\nstatus: dead\ncause: no_signal",
             "# None\n")

    res = index(where={"cause": ["weak_baseline"]})

    assert [r["id"] for r in res["nodes"]] == ["hyp-w"]


def _layered(graph):
    (graph.root / "graph.yaml").write_text(
        "name: t\nnode_types: [question, finding, gate]\n"
        "statuses: [open, alive, superseded, active]\nrules: []\n", encoding="utf-8")
    graph.node("question-q", "id: question-q\ntype: question\nstatus: open", "# Q\n")
    graph.node("gate-h", "id: gate-h\ntype: gate\nstatus: active", "# H\n")
    for i in (1, 2):
        graph.node(f"finding-{i}", f"id: finding-{i}\ntype: finding\nstatus: superseded\nlinks:\n"
                                   "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
                                   "  - {rel: kn:survivedGate, to: gate-h}", f"# {i}\n")
    graph.node("finding-3", "id: finding-3\ntype: finding\nstatus: alive\nlinks:\n"
                            "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
                            "  - {rel: kn:survivedGate, to: gate-h}", "# 3\n")
    graph.node("finding-g", "id: finding-g\ntype: finding\nstatus: alive\nlinks:\n"
                            "  - {rel: npx:supersedes, to: finding-1}\n"
                            "  - {rel: npx:supersedes, to: finding-2}\n"
                            "  - {rel: kn:survivedGate, to: gate-h}",
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
        "finding-g", "finding-3", "gate-cost", "gate-h", "hyp-alpha", "hyp-beta", "question-q"]
    assert p["hidden"] == 2 and p["total"] == 7


def test_all_shows_them_and_so_does_asking_for_the_status(graph):
    _layered(graph)

    assert {n["id"] for n in index(all=True)["nodes"]} >= {"finding-1", "finding-2"}
    assert index(all=True)["hidden"] == 0
    assert [n["id"] for n in index(status=["superseded"])["nodes"]] == ["finding-1", "finding-2"]


def test_a_general_node_is_listed_first_with_the_verdict_rule(graph):
    _layered(graph)

    rows = index()["nodes"]

    assert rows[0]["id"] == "finding-g" and rows[0]["verdict"] == "rule"
    assert rows[1]["verdict"] == "ALIVE"

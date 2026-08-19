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

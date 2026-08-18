"""When did we learn this?

Nothing stamped a node, so the graph had no time axis: no recency order, no "what did we
learn this week", no way to spot a hypothesis that has been `open` for four months. Git
knows when the FILE changed, which is not when the CLAIM did — a typo fix and a status
flip from open to dead are the same event to git — and reading it costs one subprocess
per node.
"""
from datetime import date

import pytest

from knoten.cli import main
from knoten.core import load, retrieve, today
from knoten.update import update


def test_new_stamps_the_day_it_was_opened(graph, monkeypatch):
    monkeypatch.chdir(graph.root)

    main(["new", "hypothesis", "hyp-x"])

    assert load(graph.root)["hyp-x"].frontmatter["created"] == date.today().isoformat()


def test_a_date_stays_a_string(graph, monkeypatch):
    """PyYAML 1.1 resolves an unquoted date to `datetime.date`. core._Loader drops that
    resolver on purpose; a stamp that comes back as a date object would break the
    lexicographic `--since` compare and JSON serialisation."""
    monkeypatch.chdir(graph.root)

    main(["new", "hypothesis", "hyp-x"])

    assert isinstance(load(graph.root)["hyp-x"].frontmatter["created"], str)


def test_update_stamps_the_day_the_claim_moved(graph):
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: open", "# A claim\n")

    update(graph.root, "hyp-x", status="dead")

    assert load(graph.root)["hyp-x"].frontmatter["updated"] == today()


def test_update_leaves_the_creation_date_alone(graph):
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: open\ncreated: 2020-01-01",
               "# A claim\n")

    update(graph.root, "hyp-x", status="dead")

    assert load(graph.root)["hyp-x"].frontmatter["created"] == "2020-01-01"


def test_since_keeps_only_what_moved_after_that_day(graph):
    graph.node("hyp-old", "id: hyp-old\ntype: hypothesis\nstatus: open\ncreated: 2020-01-01")
    graph.node("hyp-new", "id: hyp-new\ntype: hypothesis\nstatus: open\ncreated: 2026-08-01")

    hits = retrieve(load(graph.root), None, since="2026-01-01")

    assert [n.id for n in hits] == ["hyp-new"]


def test_a_node_updated_after_the_cutoff_counts_even_if_created_before(graph):
    """"What changed this week" means the claim moved, not that it was born."""
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead\n"
                        "created: 2020-01-01\nupdated: 2026-08-01")

    assert [n.id for n in retrieve(load(graph.root), None, since="2026-01-01")] == ["hyp-x"]


def test_an_unstamped_node_is_excluded_by_since(graph):
    """Old nodes predate stamping. `--since` is a question about time, and a node with no
    time cannot answer it — better absent than silently assumed recent."""
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: open")

    assert retrieve(load(graph.root), None, since="2020-01-01") == []


def test_cli_index_since(graph, monkeypatch, capsys):
    monkeypatch.chdir(graph.root)
    graph.node("hyp-old", "id: hyp-old\ntype: hypothesis\nstatus: open\ncreated: 2020-01-01",
               "# Old\n")
    graph.node("hyp-new", "id: hyp-new\ntype: hypothesis\nstatus: open\ncreated: 2026-08-01",
               "# New\n")

    main(["index", "--since", "2026-01-01"])
    out = capsys.readouterr().out

    assert "hyp-new" in out
    assert "hyp-old" not in out


# ------------------------------------------------------------------ agent surface

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_commit_stamps_a_node_the_agent_wrote(graph, monkeypatch):
    import json

    from knoten.mcp_server import call_tool
    monkeypatch.chdir(graph.root)
    monkeypatch.delenv("KNOTEN_GRAPH", raising=False)

    res = json.loads((await call_tool("knoten_commit", {
        "id": "hyp-x", "frontmatter": "type: hypothesis\nstatus: dead",
        "body": "# A claim\n"}))[0].text)

    assert res["status"] == "COMMITTED"
    assert load(graph.root)["hyp-x"].frontmatter["created"] == today()


async def test_commit_respects_a_date_the_author_supplied(graph, monkeypatch):
    from knoten.mcp_server import call_tool
    monkeypatch.chdir(graph.root)
    monkeypatch.delenv("KNOTEN_GRAPH", raising=False)

    await call_tool("knoten_commit", {
        "id": "hyp-x", "frontmatter": "type: hypothesis\nstatus: dead\ncreated: 2019-05-05",
        "body": "# A claim\n"})

    assert load(graph.root)["hyp-x"].frontmatter["created"] == "2019-05-05"

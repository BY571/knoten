"""When did we learn this?"""
from datetime import date

from knoten.cli import main
from knoten.commit import commit
from knoten.core import load, retrieve, today
from knoten.update import update


def test_new_stamps_the_day_it_was_opened(graph, monkeypatch):
    monkeypatch.chdir(graph.root)
    main(["new", "hypothesis", "hyp-x"])
    assert load(graph.root)["hyp-x"].frontmatter["created"] == date.today().isoformat()


def test_update_stamps_the_day_the_claim_moved(graph):
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: open", "# A claim\n")
    update(graph.root, "hyp-x", status="dead")
    assert load(graph.root)["hyp-x"].frontmatter["updated"] == today()


def test_since_keeps_only_what_moved_after_that_day(graph):
    graph.node("hyp-old", "id: hyp-old\ntype: hypothesis\nstatus: open\ncreated: 2020-01-01")
    graph.node("hyp-new", "id: hyp-new\ntype: hypothesis\nstatus: open\ncreated: 2026-08-01")
    hits = retrieve(load(graph.root), None, since="2026-01-01")
    assert [n.id for n in hits] == ["hyp-new"]


def test_an_unstamped_node_is_excluded_by_since(graph):
    """Old nodes predate stamping."""
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: open")
    assert retrieve(load(graph.root), None, since="2020-01-01") == []


# ------------------------------------------------------- writing a node from Python

def test_commit_stamps_a_node_written_programmatically(graph):
    """The stamp belongs to `commit`, not to whatever called it."""
    res = commit(graph.root, "hyp-x", "type: hypothesis\nstatus: dead", "# A claim\n")

    assert res["status"] == "COMMITTED"
    assert load(graph.root)["hyp-x"].frontmatter["created"] == today()



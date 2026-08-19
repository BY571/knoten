"""Filing a claim.

This lived inside a transport layer, which meant two things: 49 lines of domain logic sat
in the wrong place, and creating a node programmatically required that transport's SDK — an
optional dependency for a transport you may not be using. `attach` and `update` never had
that problem; they live in their own modules and lock themselves.
"""

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

def test_a_valid_node_is_written(graph):
    graph.rules(RULES).node("gate-new", "id: gate-new\ntype: gate\nstatus: open")

    res = commit(graph.root, "hyp-x", ALIVE_CITING, "# A claim\n")

    assert res["status"] == "COMMITTED"
    assert load(graph.root)["hyp-x"].status == "alive"

def test_a_violating_node_never_reaches_disk(graph):
    graph.rules(RULES)

    res = commit(graph.root, "hyp-x", "type: hypothesis\nstatus: alive", "# A claim\n")

    assert res["status"] == "REJECTED"
    assert not (graph.root / "nodes" / "hyp-x.md").exists()

def test_commit_validates_the_graph_as_it_is_when_the_lock_is_held(graph, monkeypatch):
    """The graph was read BEFORE the lock was taken and validated against that stale
    snapshot — the same read-modify-write bug that `attach` was fixed for one commit
    earlier, reintroduced one function over.

    Reproduced with threads: agent B commits `gate-new`, agent A commits a claim citing
    it, and A is rejected with "gate-new does not exist" while gate-new.md is on disk.
    Here that race is made deterministic — a peer lands its node in the window between
    entering commit and acquiring the lock.
    """
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


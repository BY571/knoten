"""The gates, before the experiment rather than after it.

An agent met a gate by being REJECTED by it, once the experiment had already run and the
result could no longer be recorded. SPEC §1 is blunt that the gates are the reusable
asset — "the reusable asset turned out to be the seven method nodes, not any strategy" —
so they belong in front of the work, as a specification, not behind it as a punishment.
"""
import pytest


pytest.importorskip("knoten.mcp_server",
                    reason="the agent server needs mcp>=2; the rest of the suite "
                           "does not")
from knoten.cli import main
from knoten.core import gates, load
from knoten import mcp_server as M

GATE = """\
id: method-compute-matched
type: method
status: active
"""

GATE_BODY = """\
# Gate: compute-matched baseline

## The rule
Compare at an equal token budget.

## Why it exists
Most "technique X improves accuracy" results are really "X spends more tokens".
"""


@pytest.fixture
def g(graph):
    graph.node("method-compute-matched", GATE, GATE_BODY)
    graph.node("method-idle", "id: method-idle\ntype: method\nstatus: active", "# Gate B\n")
    graph.node("hyp-killed", "id: hyp-killed\ntype: hypothesis\nstatus: dead\nlinks:\n"
                             "  - {rel: kn:killedByGate, to: method-compute-matched}",
               "# Killed\n")
    graph.node("hyp-lived", "id: hyp-lived\ntype: hypothesis\nstatus: alive\nlinks:\n"
                            "  - {rel: kn:survivedGate, to: method-compute-matched}",
               "# Lived\n")
    return graph


def test_a_gate_reports_what_it_killed_and_what_survived_it(g):
    by_id = {n.id: (killed, survived) for n, killed, survived in gates(load(g.root))}

    assert by_id["method-compute-matched"] == (["hyp-killed"], ["hyp-lived"])


def test_a_gate_nothing_went_through_reports_empty(g):
    by_id = {n.id: (killed, survived) for n, killed, survived in gates(load(g.root))}

    assert by_id["method-idle"] == ([], [])


def test_only_method_nodes_are_gates(g):
    assert "hyp-killed" not in [n.id for n, _, _ in gates(load(g.root))]


# ------------------------------------------------------------------ surfaces



def test_the_agent_gets_the_rule_and_the_reason_up_front(g, monkeypatch):
    """Knowing a gate exists is not enough to design an experiment that passes it. The
    agent needs what to run, and why the check is there at all."""
    monkeypatch.chdir(g.root)
    monkeypatch.delenv("KNOTEN_GRAPH", raising=False)

    res = M.knoten_gates()
    gate = next(x for x in res["gates"] if x["id"] == "method-compute-matched")

    assert gate["rule"].startswith("Compare at an equal token budget")
    assert "spends more tokens" in gate["why_it_exists"]
    assert gate["killed"] == ["hyp-killed"]
    assert gate["survived"] == ["hyp-lived"]


def test_the_cli_lists_gates_with_their_record(g, monkeypatch, capsys):
    monkeypatch.chdir(g.root)

    assert main(["gates"]) == 0
    out = capsys.readouterr().out

    assert "method-compute-matched" in out
    assert "Compare at an equal token budget" in out
    assert "method-idle" in out

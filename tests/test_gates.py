"""The gates, before the experiment rather than after it.

An agent met a gate by being REJECTED by it, once the experiment had already run and the
result could no longer be recorded. SPEC §1 is blunt that the gates are the reusable
asset — "the reusable asset turned out to be the seven method nodes, not any strategy" —
so they belong in front of the work, as a specification, not behind it as a punishment.
"""
import pytest

from knoten import ops
from knoten.cli import main
from knoten.core import gates, load
from knoten.validate import check

GATE = """\
id: gate-compute-matched
type: gate
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
    graph.node("gate-compute-matched", GATE, GATE_BODY)
    graph.node("gate-idle", "id: gate-idle\ntype: gate\nstatus: active", "# Gate B\n")
    graph.node("hyp-killed", "id: hyp-killed\ntype: hypothesis\nstatus: dead\nlinks:\n"
                             "  - {rel: kn:killedByGate, to: gate-compute-matched}",
               "# Killed\n")
    graph.node("hyp-lived", "id: hyp-lived\ntype: hypothesis\nstatus: alive\nlinks:\n"
                            "  - {rel: kn:survivedGate, to: gate-compute-matched}",
               "# Lived\n")
    return graph


def test_a_gate_reports_what_it_killed_and_what_survived_it(g):
    by_id = {n.id: (killed, survived) for n, killed, survived in gates(load(g.root))}

    assert by_id["gate-compute-matched"] == (["hyp-killed"], ["hyp-lived"])


def test_a_gate_nothing_went_through_reports_empty(g):
    by_id = {n.id: (killed, survived) for n, killed, survived in gates(load(g.root))}

    assert by_id["gate-idle"] == ([], [])


def test_only_method_nodes_are_gates(g):
    assert "hyp-killed" not in [n.id for n, _, _ in gates(load(g.root))]


# ------------------------------------------------------------------ surfaces



def test_the_agent_gets_the_rule_and_the_reason_up_front(g):
    """Knowing a gate exists is not enough to design an experiment that passes it. The
    agent needs what to run, and why the check is there at all."""
    res = ops.gates(g.root)
    gate = next(x for x in res["gates"] if x["id"] == "gate-compute-matched")

    assert gate["rule"].startswith("Compare at an equal token budget")
    assert "spends more tokens" in gate["why_it_exists"]
    assert gate["killed"] == ["hyp-killed"]
    assert gate["survived"] == ["hyp-lived"]


def test_the_cli_lists_gates_with_their_record(g, monkeypatch, capsys):
    monkeypatch.chdir(g.root)

    assert main(["gates"]) == 0
    out = capsys.readouterr().out

    assert "gate-compute-matched" in out
    assert "Compare at an equal token budget" in out
    assert "gate-idle" in out


# --------------------------------------------------- the type is `gate`, not `method`

GATE_NODE = "id: gate-cost-hurdle\ntype: gate\nstatus: active"


def test_a_node_typed_gate_is_a_gate(graph):
    """`method` meant the bar a claim must survive, which is the opposite of what the
    English word suggests — graphs in the wild ended up with ids like
    `gate-cost-net-of-costs`, the type name and the concept fighting for one slot."""
    graph.node("gate-cost-hurdle", GATE_NODE, "# Gate\n")

    assert [g.id for g, _, _ in gates(load(graph.root))] == ["gate-cost-hurdle"]


def test_a_node_cited_as_a_gate_but_typed_otherwise_is_reported(graph):
    """The rename's whole risk: a graph written before it keeps `type: gate`, and
    `knoten gates` quietly returns nothing. Silence is the one failure mode a
    falsification tool must not have, so the mismatch is a violation."""
    graph.node("method-old", "id: method-old\ntype: method\nstatus: active", "# Gate\n")
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: alive\nlinks:\n"
                        "  - {rel: kn:survivedGate, to: method-old}", "# A claim\n")

    rules = [v.rule for v in check(load(graph.root), graph.root)]

    assert "not-a-gate" in rules

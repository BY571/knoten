"""The gates, before the experiment rather than after it."""
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


# ------------------------------------------------------------------ surfaces


def test_the_agent_gets_the_rule_and_the_reason_up_front(g):
    """Knowing a gate exists is not enough to design an experiment that passes it."""
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

def test_a_node_cited_as_a_gate_but_typed_otherwise_is_reported(graph):
    """The rename's whole risk."""
    graph.node("method-old", "id: method-old\ntype: method\nstatus: active", "# Gate\n")
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: alive\nlinks:\n"
                        "  - {rel: kn:survivedGate, to: method-old}", "# A claim\n")
    rules = [v.rule for v in check(load(graph.root), graph.root)]
    assert "not-a-gate" in rules


def test_a_graph_with_its_own_word_for_the_bar_is_left_alone(graph):
    """The migration check must not become the core inventing vocabulary."""
    graph.rules("name: t\nnode_types: [hypothesis, criterion]\nrules: []\n")
    graph.node("crit-costs", "id: crit-costs\ntype: criterion\nstatus: active", "# Bar\n")
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: alive\nlinks:\n"
                        "  - {rel: kn:survivedGate, to: crit-costs}", "# A claim\n")
    assert [v.rule for v in check(load(graph.root), graph.root)] == []

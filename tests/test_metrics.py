"""A number the graph is trying to move, read along the time axis.

Every other view reads the graph along its edges. This one asks what those cannot: is it
working, and which run moved it. The property under test throughout is that a point is
read IN ORDER, against the best result that came BEFORE it.
"""
import json

import pytest

from knoten import viz
from knoten.cli import main
from knoten.core import GraphError, load, metric
from knoten.validate import load_config

YAML = """\
name: t
node_types: [question, experiment, finding, gate]
statuses: [open, alive, dead, retracted, superseded, active]
metrics:
  reward: {goal: %s}
"""


def series(graph, values, goal="max", links=None):
    """`values` as one experiment each, a day apart, oldest first. `links` gives a node
    the `rel=to` edges whose lineage is under test."""
    graph.rules(YAML % goal)
    for i, v in enumerate(values):
        edges = (links or {}).get(f"exp-{i}", [])
        graph.node(f"exp-{i}",
                   f"id: exp-{i}\ntype: experiment\nstatus: alive\n"
                   f"created: 2026-01-{i + 1:02d}\n"
                   + ("links:\n" + "".join(f"  - {{rel: {e.split('=')[0]}, "
                                           f"to: {e.split('=')[1]}}}\n" for e in edges)
                      if edges else "")
                   + f"results:\n  reward: {v}\n", f"# run {i}\n")
    return graph


def test_a_metric_knoten_cannot_read_is_refused(graph):
    """A `goal: minimise` nobody understands would default to `max` and rank a loss
    upside down. Config that enforces nothing is decoration."""
    for decl in ("metrics: reward", "metrics: {}", "metrics:\n  reward: {goal: minimise}",
                 "metrics:\n  reward: {goal: min, direction: down}",
                 "metrics:\n  reward: [min]", "metrics:\n  Reward: {goal: max}"):
        graph.rules(f"name: t\n{decl}\n")
        with pytest.raises(GraphError, match="(?i)metric"):
            load_config(graph.root)
    graph.rules("name: t\nmetrics:\n  reward: {}\n")
    assert load_config(graph.root)["metrics"] == {"reward": {}}      # goal defaults to max


def test_delta_is_measured_against_the_best_earlier_point(graph):
    """A run landing between two better ones has moved nothing. Measured against its
    immediate predecessor, 10 -> 4 -> 6 would report the 6 as a gain."""
    out = metric(load(series(graph, [10, 4, 6]).root), "reward", "max")
    assert [p["id"] for p in out] == ["exp-0", "exp-1", "exp-2"]
    assert [p["delta"] for p in out] == [None, -6, -4]
    assert [p["best"] for p in out] == [True, False, False]


def test_a_min_goal_reads_the_same_series_the_other_way(graph):
    """Loss, tokens and latency are as common as accuracy, so the direction is declared
    rather than assumed."""
    out = metric(load(series(graph, [10, 4, 6], goal="min").root), "reward", "min")
    assert [p["delta"] for p in out] == [None, -6, 2]
    assert [p["best"] for p in out] == [True, True, False]


def test_builds_on_is_derived_from_the_edges_the_author_already_wrote(graph):
    """Through a node carrying no number of its own, and never backwards: a result cannot
    stand on one that had not happened yet. A gate edge is not lineage."""
    series(graph, [1, 2, 3], links={"exp-1": ["prov:wasDerivedFrom=find-mid"],
                                    "exp-2": ["kn:survivedGate=exp-0"]})
    graph.node("find-mid", "id: find-mid\ntype: finding\nstatus: alive\nlinks:\n"
                           "  - {rel: kn:followsFrom, to: exp-0}")
    out = metric(load(graph.root), "reward")
    assert [p["builds_on"] for p in out] == [[], ["exp-0"], []]


def test_the_list_names_every_declared_metric_and_the_run_that_set_the_record(
        graph, monkeypatch, capsys):
    monkeypatch.chdir(graph.root)
    series(graph, [5, 9])
    assert main(["metric"]) == 0
    out = capsys.readouterr().out
    assert "reward (max)" in out and "best 9" in out and "exp-1" in out and "2 points" in out


def test_one_metric_prints_a_row_per_point(graph, monkeypatch, capsys):
    """`baseline` rather than `+0` on the first: it moved nothing because there was
    nothing to move it against. And 9, not 9.0."""
    monkeypatch.chdir(graph.root)
    series(graph, [5, 9], links={"exp-1": ["prov:wasDerivedFrom=exp-0"]})
    assert main(["metric", "reward", "--json"]) == 0
    assert [p["value"] for p in json.loads(capsys.readouterr().out)["points"]] == [5, 9]
    assert main(["metric", "reward"]) == 0
    out = capsys.readouterr().out
    assert "baseline" in out and "+4" in out and "builds on exp-0" in out
    assert "9.0" not in out


def test_a_metric_nobody_declared_is_refused_by_name(graph, monkeypatch, capsys):
    """Naming the declared ones IS the refusal: a typo is otherwise indistinguishable
    from a metric nothing has recorded yet."""
    monkeypatch.chdir(graph.root)
    series(graph, [5])
    assert main(["metric", "rewrad"]) == 1
    err = capsys.readouterr().err
    assert "rewrad" in err and "reward" in err


def test_the_frontier_header_and_the_page_carry_the_same_best_point(
        graph, monkeypatch, capsys):
    """One number, three surfaces. The page's strip was once drawn without the clause the
    CLI header printed, off the very same `shape`."""
    monkeypatch.chdir(graph.root)
    series(graph, [5, 9])
    main(["frontier"])
    assert "reward: best 9 (exp-1)" in capsys.readouterr().out
    p = viz.payload(graph.root)
    assert p["shape"]["metrics"] == [{"name": "reward", "goal": "max", "count": 2,
                                      "best": 9, "best_id": "exp-1"}]
    assert [q["value"] for q in p["metrics"][0]["points"]] == [5, 9]
    html = viz.render(graph.root)
    for needle in ["id=v-metrics", "id=charts", "function chart(m)",
                   "best ${m.best} (${m.best_id})"]:
        assert needle in html, needle

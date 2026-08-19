"""--json must emit exactly the dict ops returns. That equality is what stops the CLI
and the MCP server from drifting, which is this repo's most repeated bug."""
import json

import pytest

from knoten import ops
from knoten.cli import main


@pytest.fixture
def g(graph):
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead\ntags: [decoding]",
               "# A claim\n")
    return graph


@pytest.mark.parametrize("argv,op", [
    (["index", "--json"], lambda r: ops.index(r)),
    (["query", "claim", "--json"], lambda r: ops.query(r, "claim")),
    (["frontier", "--json"], lambda r: ops.frontier(r)),
    (["gates", "--json"], lambda r: ops.gates(r)),
    (["validate", "--json"], lambda r: ops.validate(r)),
    (["show", "hyp-x", "--json"], lambda r: ops.get(r, "hyp-x")),
    (["path", "hyp-x", "hyp-x", "--json"], lambda r: ops.path(r, "hyp-x", "hyp-x")),
    # `hyp-x` is already dead, so re-applying `--status dead` is a real write (it still
    # re-stamps `updated` and validates) rather than the "nothing to change" refusal a
    # second, no-op invocation of ops.update would hit — the comparison call below has
    # to do the same work the CLI just did, not skip it.
    (["update", "hyp-x", "--status", "dead", "--json"],
     lambda r: ops.update(r, "hyp-x", status="dead")),
])
def test_json_output_equals_the_ops_dict(g, monkeypatch, capsys, argv, op):
    monkeypatch.chdir(g.root)

    main(argv)

    assert json.loads(capsys.readouterr().out) == op(g.root)


def test_prose_is_the_default(g, monkeypatch, capsys):
    monkeypatch.chdir(g.root)

    main(["index"])
    out = capsys.readouterr().out

    assert "hyp-x" in out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


def test_show_error_is_stderr_in_prose_and_stdout_in_json(graph, monkeypatch, capsys):
    """Same failure, one stream per mode: a human reading prose gets the error where
    every other command puts one (stderr); a machine reading --json needs the
    structured payload where it always looks (stdout)."""
    monkeypatch.chdir(graph.root)

    assert main(["show", "nope"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert "nope" in err
    assert err.startswith("knoten: ")     # every other CLI error carries this prefix

    assert main(["show", "nope", "--json"]) == 1
    out, err = capsys.readouterr()
    assert err == ""
    assert json.loads(out)["error"]


def test_update_rejection_json_equals_the_ops_dict(g, monkeypatch, capsys):
    """The bug this branch exists to eliminate: `update`'s refusal used to be built
    twice, and the CLI's copy had no `hint` where MCP's did. Pin both the shape AND
    that a refusal never touches the file, on either surface."""
    g.rules("name: t\nstatuses: [open, dead]\nnode_types: [hypothesis]\nrules: []\n")
    monkeypatch.chdir(g.root)

    code = main(["update", "hyp-x", "--status", "bogus", "--json"])
    out = capsys.readouterr().out

    assert code == 1
    assert json.loads(out) == ops.update(g.root, "hyp-x", status="bogus")
    assert json.loads(out)["hint"]


def test_query_prose_carries_the_untested_caveat(graph, monkeypatch, capsys):
    """A zero-hit query's payload carries the guard against the one failure knoten
    exists to prevent — a false "untested". Losing it in prose, the surface SKILL.md
    tells an agent to prefer, would silently reintroduce that failure."""
    monkeypatch.chdir(graph.root)

    main(["query", "nothing-matches-this"])
    out = capsys.readouterr().out

    assert "NOT proof the idea is untested" in out


def test_index_prose_carries_the_truncation_note(graph, monkeypatch, capsys):
    monkeypatch.chdir(graph.root)
    for i in range(5):
        graph.node(f"hyp-{i}", f"id: hyp-{i}\ntype: hypothesis\nstatus: open", "# c\n")

    main(["index", "--limit", "2"])
    out = capsys.readouterr().out

    assert "Narrow with tags/status/type" in out


def test_frontier_prose_carries_the_judgement_note(graph, monkeypatch, capsys):
    monkeypatch.chdir(graph.root)

    main(["frontier"])
    out = capsys.readouterr().out

    assert "knoten does not, because that is the research" in out


def test_gates_prose_carries_the_never_applied_note(graph, monkeypatch, capsys):
    monkeypatch.chdir(graph.root)
    graph.node("gate-x", "id: gate-x\ntype: gate\nstatus: active", "# gate\n")

    main(["gates"])
    out = capsys.readouterr().out

    assert "never been applied" in out


def test_show_prose_reports_attachment_size_and_missing(graph, monkeypatch, capsys):
    monkeypatch.chdir(graph.root)
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead\n"
                        "attachments:\n  - plot.png\n  - gone.png")
    graph.attachment("hyp-x", "plot.png", content="x" * 2048)

    main(["show", "hyp-x"])
    out = capsys.readouterr().out

    assert "attachments/hyp-x/plot.png" in out and "2.0 KB" in out
    assert "attachments/hyp-x/gone.png" in out and "MISSING" in out


def test_query_also_line_keeps_non_gate_related_nodes(graph, monkeypatch, capsys):
    """`related_gates` is the MCP contract (type == GATE_TYPE); the CLI's "also:" line
    needs everything else that matched but isn't a claim, or a source node like this one
    silently vanishes from the one place a human sees it."""
    monkeypatch.chdir(graph.root)
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead", "# A claim\n")
    graph.node("src-paper", "id: src-paper\ntype: source\nstatus: open",
               "# A claim from paper\n")

    main(["query", "claim"])
    out = capsys.readouterr().out

    assert "also: src-paper" in out


def test_query_reports_the_gates_that_matched(graph, monkeypatch):
    """`related_gates` had no assertion anywhere in the suite, and the type rename walked
    straight past the one place `ops.py` hardcoded the old string — so the key went
    silently empty on every renamed graph. That is the same silent absence `not-a-gate`
    was added to prevent, one module over, and only a test can hold it."""
    monkeypatch.chdir(graph.root)
    graph.node("gate-costs", "id: gate-costs\ntype: gate\nstatus: active", "# Gate: costs\n")
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead", "# Costs sink it\n")

    assert ops.query(graph.root, "costs")["related_gates"] == ["gate-costs"]

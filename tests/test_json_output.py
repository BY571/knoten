"""--json must emit exactly the dict ops returns."""
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


def test_show_error_is_stderr_in_prose_and_stdout_in_json(graph, monkeypatch, capsys):
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
    g.rules("name: t\nstatuses: [open, dead]\nnode_types: [hypothesis]\nrules: []\n")
    monkeypatch.chdir(g.root)
    code = main(["update", "hyp-x", "--status", "bogus", "--json"])
    out = capsys.readouterr().out
    assert code == 1
    assert json.loads(out) == ops.update(g.root, "hyp-x", status="bogus")
    assert json.loads(out)["hint"]


def test_query_prose_carries_the_untested_caveat(graph, monkeypatch, capsys):
    monkeypatch.chdir(graph.root)
    main(["query", "nothing-matches-this"])
    out = capsys.readouterr().out
    assert "NOT proof the idea is untested" in out


def test_show_prose_reports_attachment_size_and_missing(graph, monkeypatch, capsys):
    monkeypatch.chdir(graph.root)
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead\n"
                        "attachments:\n  - plot.png\n  - gone.png")
    graph.attachment("hyp-x", "plot.png", content="x" * 2048)
    main(["show", "hyp-x"])
    out = capsys.readouterr().out
    assert "attachments/hyp-x/plot.png" in out and "2.0 KB" in out
    assert "attachments/hyp-x/gone.png" in out and "MISSING" in out


def test_query_caps_its_own_response_and_says_so(graph):
    for i in range(60):
        graph.node(f"hyp-{i:03d}", f"id: hyp-{i:03d}\ntype: hypothesis\nstatus: dead",
                   f"# decoding experiment {i}\n")
    res = ops.query(graph.root, "decoding")
    assert res["total"] == 60
    assert len(res["claims"]) < 60
    assert res["truncated"] is True
    assert "whole graph" in res["note"]

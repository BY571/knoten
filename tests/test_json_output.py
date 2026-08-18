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

    assert main(["show", "nope", "--json"]) == 1
    out, err = capsys.readouterr()
    assert err == ""
    assert json.loads(out)["error"]


def test_query_also_line_keeps_non_method_related_nodes(graph, monkeypatch, capsys):
    """`related_methods` is the MCP contract (type == method); the CLI's "also:" line
    needs everything else that matched but isn't a claim, or a source node like this one
    silently vanishes from the one place a human sees it."""
    monkeypatch.chdir(graph.root)
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead", "# A claim\n")
    graph.node("src-paper", "id: src-paper\ntype: source\nstatus: open",
               "# A claim from paper\n")

    main(["query", "claim"])
    out = capsys.readouterr().out

    assert "also: src-paper" in out

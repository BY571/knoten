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

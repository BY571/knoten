"""Every question the graph answers, as a dict. One implementation; the CLI renders it,
--json dumps it, the MCP tool serialises it. These lived twice before and drifted."""
from knoten import ops


def test_index_returns_the_same_shape_the_mcp_tool_did(graph):
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead\ntags: [decoding]",
               "# A claim\n")

    res = ops.index(graph.root)

    assert res["total"] == 1
    assert res["truncated"] is False
    assert res["nodes"] == [{"id": "hyp-x", "type": "hypothesis", "verdict": "DEAD",
                             "tags": ["decoding"], "title": "A claim"}]
    assert res["declared_tags"] == []

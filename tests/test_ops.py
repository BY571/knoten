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


def test_get_reports_an_attachments_size_from_disk(graph):
    """`show` used to print size / MISSING from the filesystem directly — additive here
    so both surfaces (not just the CLI) can see it, not just a bare path string."""
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead\n"
                        "attachments:\n  - plot.png\n  - gone.png")
    graph.attachment("hyp-x", "plot.png", content="x" * 2048)

    res = ops.get(graph.root, "hyp-x")

    by_path = {a["path"]: a for a in res["attachment_files"]}
    assert by_path["attachments/hyp-x/plot.png"]["size_kb"] == 2.0
    assert by_path["attachments/hyp-x/gone.png"]["missing"] is True

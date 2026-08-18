"""The MCP surface is the one place an autonomous agent writes to the filesystem.
It takes an LLM-authored id and turns it into a path."""
import json

import pytest

from knoten.mcp_server import call_tool

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def commit(nid, frontmatter, body="# body\n"):
    out = await call_tool("knoten_commit", {"id": nid, "frontmatter": frontmatter, "body": body})
    return json.loads(out[0].text)


@pytest.fixture(autouse=True)
def cwd(graph, monkeypatch):
    monkeypatch.chdir(graph.root)
    monkeypatch.delenv("KNOTEN_GRAPH", raising=False)
    return graph


async def test_commit_refuses_an_id_that_escapes_the_graph(cwd, tmp_path):
    """Finding 7: `root/nodes/{id}.md` with an LLM-supplied id. An id of
    `../../../pwned` wrote outside the graph entirely."""
    res = await commit("../../../pwned", "id: x\ntype: hypothesis\nstatus: dead")

    assert res["status"] == "REJECTED"
    assert "id" in res["reason"].lower()
    assert not (tmp_path.parent / "pwned.md").exists()


@pytest.mark.parametrize("nid", ["../evil", "sub/dir", "with space", "UPPER", "", "."])
async def test_commit_only_accepts_kebab_case_ids(cwd, nid):
    res = await commit(nid, "id: x\ntype: hypothesis\nstatus: dead")

    assert res["status"] == "REJECTED"


async def test_commit_does_not_touch_disk_when_the_node_is_invalid(cwd):
    """Finding 8: commit wrote the file, THEN validated, THEN unlinked. The
    README promises it 'validates before writing'. Now it actually does."""
    res = await commit("hyp-unchallenged", "id: hyp-unchallenged\ntype: hypothesis\nstatus: alive")

    assert res["status"] == "REJECTED"
    assert res["violations"][0]["rule"] == "live-claims-must-cite-their-gates"
    assert not (cwd.root / "nodes" / "hyp-unchallenged.md").exists()


async def test_commit_writes_a_valid_node(cwd):
    cwd.node("method-gate", "id: method-gate\ntype: method")

    res = await commit(
        "hyp-ok",
        "id: hyp-ok\ntype: hypothesis\nstatus: alive\nlinks:\n  - {rel: kn:survivedGate, to: method-gate}",
    )

    assert res["status"] == "COMMITTED"
    assert (cwd.root / "nodes" / "hyp-ok.md").exists()


async def test_commit_rejects_malformed_yaml_without_writing(cwd):
    res = await commit("hyp-bad", "id: hyp-bad\n  oops: bad indent")

    assert res["status"] == "REJECTED"
    assert not (cwd.root / "nodes" / "hyp-bad.md").exists()


async def test_commit_refuses_to_overwrite(cwd):
    cwd.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead")

    res = await commit("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead")

    assert res["status"] == "REJECTED"
    assert "supersede" in res["reason"].lower()


async def test_query_reports_the_cause_of_death(cwd):
    cwd.node("method-gate", "id: method-gate\ntype: method")
    cwd.node(
        "hyp-x",
        "id: hyp-x\ntype: hypothesis\nstatus: dead\nlinks:\n  - {rel: kn:killedByGate, to: method-gate}",
        body="# x\n## Why it died\nThe gain was compute, not method.\n",
    )

    out = await call_tool("knoten_query", {"query": "hyp-x"})
    res = json.loads(out[0].text)

    (claim,) = res["claims"]
    assert claim["verdict"] == "DEAD"
    assert claim["killed_by"] == ["method-gate"]
    assert "compute, not method" in claim["why_it_died"]


async def test_get_surfaces_that_a_claim_was_retracted(cwd):
    """We reported what a node RETRACTS and never that it WAS retracted, so an agent asking
    "has this been tried?" about a withdrawn claim was told the claim, not the withdrawal."""
    cwd.node("method-gate", "id: method-gate\ntype: method")
    cwd.node("hyp-wrong", "id: hyp-wrong\ntype: hypothesis\nstatus: retracted")
    cwd.node("ret-oops", "id: ret-oops\ntype: hypothesis\nstatus: alive\nlinks:\n"
                         "  - {rel: npx:retracts, to: hyp-wrong}\n"
                         "  - {rel: kn:survivedGate, to: method-gate}")

    out = await call_tool("knoten_get", {"id": "hyp-wrong"})
    res = json.loads(out[0].text)

    assert res["retracted_by"] == ["ret-oops"]
    assert "ret-oops" in res["warning"]



async def test_query_caps_its_own_response_and_says_so(cwd):
    """A broad query on a 500-node graph returned every match in full — ~83k tokens in
    one response. The tool got LESS usable the more the graph accumulated, which is
    backwards for a thing whose whole purpose is to accumulate. Cap it, rank it, and
    never let the cap be silent."""
    for i in range(60):
        cwd.node(f"hyp-{i:03d}", f"id: hyp-{i:03d}\ntype: hypothesis\nstatus: dead",
                 f"# decoding experiment {i}\n")

    out = await call_tool("knoten_query", {"query": "decoding"})
    res = json.loads(out[0].text)

    assert res["total"] == 60
    assert len(res["claims"]) < 60
    assert res["truncated"] is True
    assert "knoten_index" in res["note"]


async def test_query_ranks_the_closest_claim_first(cwd):
    cwd.node("hyp-far", "id: hyp-far\ntype: hypothesis\nstatus: dead", "# decoding\n")
    cwd.node("hyp-near", "id: hyp-near\ntype: hypothesis\nstatus: dead",
             "# decoding with majority vote sampling\n")

    res = json.loads((await call_tool(
        "knoten_query", {"query": "majority vote sampling"}))[0].text)

    assert res["claims"][0]["id"] == "hyp-near"

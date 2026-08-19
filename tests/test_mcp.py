"""The MCP surface is the one place an autonomous agent writes to the filesystem.
It takes an LLM-authored id and turns it into a path."""
import json

import pytest

pytest.importorskip("knoten.mcp_server",
                    reason="the agent server needs mcp>=2; the rest of the suite "
                           "does not")

from knoten import mcp_server as M
from knoten.core import load


def commit(nid, frontmatter, body="# body\n"):
    return M.knoten_commit(id=nid, frontmatter=frontmatter, body=body)


@pytest.fixture(autouse=True)
def cwd(graph, monkeypatch):
    monkeypatch.chdir(graph.root)
    monkeypatch.delenv("KNOTEN_GRAPH", raising=False)
    return graph


def test_commit_refuses_an_id_that_escapes_the_graph(cwd, tmp_path):
    """Finding 7: `root/nodes/{id}.md` with an LLM-supplied id. An id of
    `../../../pwned` wrote outside the graph entirely."""
    res = commit("../../../pwned", "id: x\ntype: hypothesis\nstatus: dead")

    assert res["status"] == "REJECTED"
    assert "id" in res["reason"].lower()
    assert not (tmp_path.parent / "pwned.md").exists()


@pytest.mark.parametrize("nid", ["../evil", "sub/dir", "with space", "UPPER", "", "."])
def test_commit_only_accepts_kebab_case_ids(cwd, nid):
    res = commit(nid, "id: x\ntype: hypothesis\nstatus: dead")

    assert res["status"] == "REJECTED"


def test_commit_does_not_touch_disk_when_the_node_is_invalid(cwd):
    """Finding 8: commit wrote the file, THEN validated, THEN unlinked. The
    README promises it 'validates before writing'. Now it actually does."""
    res = commit("hyp-unchallenged", "id: hyp-unchallenged\ntype: hypothesis\nstatus: alive")

    assert res["status"] == "REJECTED"
    assert res["violations"][0]["rule"] == "live-claims-must-cite-their-gates"
    assert not (cwd.root / "nodes" / "hyp-unchallenged.md").exists()


def test_commit_writes_a_valid_node(cwd):
    cwd.node("gate-cost", "id: gate-cost\ntype: gate")

    res = commit(
        "hyp-ok",
        "id: hyp-ok\ntype: hypothesis\nstatus: alive\nlinks:\n  - {rel: kn:survivedGate, to: gate-cost}",
    )

    assert res["status"] == "COMMITTED"
    assert (cwd.root / "nodes" / "hyp-ok.md").exists()


def test_commit_rejects_malformed_yaml_without_writing(cwd):
    res = commit("hyp-bad", "id: hyp-bad\n  oops: bad indent")

    assert res["status"] == "REJECTED"
    assert not (cwd.root / "nodes" / "hyp-bad.md").exists()


def test_commit_refuses_to_overwrite(cwd):
    cwd.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead")

    res = commit("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead")

    assert res["status"] == "REJECTED"
    assert "supersede" in res["reason"].lower()


def test_query_reports_the_cause_of_death(cwd):
    cwd.node("gate-cost", "id: gate-cost\ntype: gate")
    cwd.node(
        "hyp-x",
        "id: hyp-x\ntype: hypothesis\nstatus: dead\nlinks:\n  - {rel: kn:killedByGate, to: gate-cost}",
        body="# x\n## Why it died\nThe gain was compute, not method.\n",
    )

    res = M.knoten_query(query="hyp-x")

    (claim,) = res["claims"]
    assert claim["verdict"] == "DEAD"
    assert claim["killed_by"] == ["gate-cost"]
    assert "compute, not method" in claim["why_it_died"]


def test_get_surfaces_that_a_claim_was_retracted(cwd):
    """We reported what a node RETRACTS and never that it WAS retracted, so an agent asking
    "has this been tried?" about a withdrawn claim was told the claim, not the withdrawal."""
    cwd.node("gate-cost", "id: gate-cost\ntype: gate")
    cwd.node("hyp-wrong", "id: hyp-wrong\ntype: hypothesis\nstatus: retracted")
    cwd.node("ret-oops", "id: ret-oops\ntype: hypothesis\nstatus: alive\nlinks:\n"
                         "  - {rel: npx:retracts, to: hyp-wrong}\n"
                         "  - {rel: kn:survivedGate, to: gate-cost}")

    res = M.knoten_get(id="hyp-wrong")

    assert res["retracted_by"] == ["ret-oops"]
    assert "ret-oops" in res["warning"]



def test_query_caps_its_own_response_and_says_so(cwd):
    """A broad query on a 500-node graph returned every match in full — ~83k tokens in
    one response. The tool got LESS usable the more the graph accumulated, which is
    backwards for a thing whose whole purpose is to accumulate. Cap it, rank it, and
    never let the cap be silent."""
    for i in range(60):
        cwd.node(f"hyp-{i:03d}", f"id: hyp-{i:03d}\ntype: hypothesis\nstatus: dead",
                 f"# decoding experiment {i}\n")

    res = M.knoten_query(query="decoding")

    assert res["total"] == 60
    assert len(res["claims"]) < 60
    assert res["truncated"] is True
    # Surface-neutral: one dict serves the CLI and MCP, so its prose must not name
    # either surface's command by name — describe the action instead.
    assert "knoten_index" not in res["note"]
    assert "whole graph" in res["note"]


def test_query_ranks_the_closest_claim_first(cwd):
    cwd.node("hyp-far", "id: hyp-far\ntype: hypothesis\nstatus: dead", "# decoding\n")
    cwd.node("hyp-near", "id: hyp-near\ntype: hypothesis\nstatus: dead",
             "# decoding with majority vote sampling\n")

    res = M.knoten_query(query="majority vote sampling")

    assert res["claims"][0]["id"] == "hyp-near"


# ---------------------------------------------------------------- knoten_update


def update(**args):
    return M.knoten_update(**args)


def test_the_loop_can_close_what_it_opened(cwd):
    """open -> run -> record the verdict. Step three had no tool at all: knoten_commit
    refuses to overwrite, so an agent could open a hypothesis and never close it."""
    cwd.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: open", "# A claim\n")

    res = update(id="hyp-x", status="dead", append="## Why it died\nnoise\n")

    assert res["status"] == "UPDATED"
    assert res["node_status"] == "dead"
    assert load(cwd.root)["hyp-x"].status == "dead"


def test_an_update_that_breaks_a_rule_comes_back_as_json(cwd):
    """The gate survives the new write path, and an MCP server answers rather than
    exploding."""
    cwd.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead", "# A claim\n")

    res = update(id="hyp-x", status="alive")

    assert res["status"] == "REJECTED"
    assert "live-claims-must-cite-their-gates" in json.dumps(res)
    assert load(cwd.root)["hyp-x"].status == "dead"


def test_updating_an_unknown_node_is_reported_not_raised(cwd):
    assert (update(id="hyp-nope", status="dead"))["status"] == "REJECTED"


# ---------------------------------------------------------------- duplicate warning


def test_commit_warns_when_the_new_claim_resembles_a_settled_one(cwd):
    """A loop running for weeks WILL re-propose an idea it already settled, worded
    differently, under a new id. The graph then holds two answers to one question and the
    second has no post-mortem."""
    cwd.node("hyp-self-consistency", "id: hyp-self-consistency\ntype: hypothesis\n"
                                     "status: dead",
             "# Self-consistency majority vote beats greedy decoding\n\n"
             "## Why it died\nThe gain was compute, not method.\n")

    res = commit("hyp-sample-and-vote", "type: hypothesis\nstatus: open",
                       "# Majority vote over sampled chains beats greedy decoding\n")

    assert res["status"] == "COMMITTED"          # a warning, never a block
    assert [s["id"] for s in res["similar"]] == ["hyp-self-consistency"]
    assert res["similar"][0]["verdict"] == "DEAD"
    assert "supersede" in res["warning"]


def test_an_unrelated_claim_gets_no_warning(cwd):
    cwd.node("hyp-self-consistency", "id: hyp-self-consistency\ntype: hypothesis\n"
                                     "status: dead", "# Self-consistency beats greedy\n")

    res = commit("hyp-tokeniser", "type: hypothesis\nstatus: open",
                       "# A byte-level tokeniser lowers perplexity\n")

    assert "similar" not in res


def test_one_shared_word_is_not_a_duplicate(cwd):
    """The warning is worth nothing if it fires on every commit, so it takes two shared
    title words rather than one.

    Two IS a loose bar — "dropout improves accuracy" and "warmup improves accuracy" would
    trip it — and that is deliberate: a false positive costs the agent one line of JSON
    it can dismiss, while a false negative costs a duplicated experiment. The asymmetry
    says lean permissive. Anything tighter (idf over titles, overlap ratios) misbehaves
    on the small graphs where duplicates start appearing."""
    cwd.node("hyp-a", "id: hyp-a\ntype: hypothesis\nstatus: dead",
             "# Dropout lowers perplexity\n")

    res = commit("hyp-b", "type: hypothesis\nstatus: open",
                       "# Warmup improves accuracy on reasoning\n")

    assert "similar" not in res


def test_an_unsettled_claim_is_not_reported_as_prior_art(cwd):
    """`open` is not an answer. Warning about one would tell the agent the question is
    closed when it is exactly what is still being asked."""
    cwd.node("hyp-open", "id: hyp-open\ntype: hypothesis\nstatus: open",
             "# Majority vote over sampled chains beats greedy decoding\n")

    res = commit("hyp-new", "type: hypothesis\nstatus: open",
                       "# Majority vote over sampled chains beats greedy decoding\n")

    assert "similar" not in res

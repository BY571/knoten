"""`knoten new`, a query that tokenizes, and retractions that actually surface."""
import json

import pytest

from knoten.cli import main
from knoten.core import load
from knoten.validate import check

pytestmark_any = None


# ---------------------------------------------------------------- knoten new

def test_new_scaffolds_a_node_that_already_passes_the_rules(graph, monkeypatch, capsys):
    """The happy path was: hand-write frontmatter, get rejected, guess, retry. The failure
    mode this tool exists to prevent was caused by FRICTION, so friction on the write path
    is the thing to attack hardest."""
    monkeypatch.chdir(graph.root)
    graph.node("method-gate", "id: method-gate\ntype: method")

    assert main(["new", "hypothesis", "hyp-my-idea"]) == 0

    n = load(graph.root)["hyp-my-idea"]
    assert n.type == "hypothesis"
    assert n.status == "open"           # not yet alive: it has survived nothing
    assert check(load(graph.root), graph.root) == []


def test_a_dead_node_is_scaffolded_with_whatever_the_rules_demand(graph, monkeypatch):
    """Nothing here is knoten's opinion — it reads THIS graph's rules and pre-fills exactly
    what they require, so the author writes prose instead of rediscovering the rule."""
    graph.rules("""\
rules:
  - id: dead-claims-must-say-why
    when_status: dead
    require_sections: Why it died, What would reopen this
    require_result_min: {n_independent: 30}
    message: The post-mortem IS the asset.
""")
    monkeypatch.chdir(graph.root)

    main(["new", "hypothesis", "hyp-dead", "--status", "dead"])
    text = graph.read("hyp-dead")

    assert "## Why it died" in text
    assert "## What would reopen this" in text
    assert "n_independent: TODO" in text


def test_new_refuses_to_overwrite(graph, monkeypatch):
    monkeypatch.chdir(graph.root)
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead")

    assert main(["new", "hypothesis", "hyp-x"]) == 1


def test_new_refuses_an_id_that_is_not_kebab_case(graph, monkeypatch):
    monkeypatch.chdir(graph.root)

    assert main(["new", "hypothesis", "../evil"]) == 1
    assert not (graph.root.parent / "evil.md").exists()


# ---------------------------------------------------------------- query

def test_query_matches_across_the_separator(graph, monkeypatch, capsys):
    """`knoten query "self consistency"` found NOTHING, because the node is
    `self-consistency`. A naive substring match makes the tool's headline question fail on
    a space."""
    monkeypatch.chdir(graph.root)
    graph.node("hyp-self-consistency", "id: hyp-self-consistency\ntype: hypothesis\nstatus: dead")

    main(["query", "self consistency"])

    assert "hyp-self-consistency" in capsys.readouterr().out


def test_query_requires_all_tokens(graph, monkeypatch, capsys):
    monkeypatch.chdir(graph.root)
    graph.node("hyp-a", "id: hyp-a\ntype: hypothesis\nstatus: dead", body="# about decoding\n")
    graph.node("hyp-b", "id: hyp-b\ntype: hypothesis\nstatus: dead", body="# about prompting\n")

    main(["query", "decoding prompting"])
    out = capsys.readouterr().out

    assert "0 claim(s)" in out


# ---------------------------------------------------------------- retractions

def retracted_graph(graph):
    graph.node("hyp-wrong", "id: hyp-wrong\ntype: hypothesis\nstatus: retracted")
    graph.node("ret-oops", "id: ret-oops\ntype: hypothesis\nstatus: alive\nlinks:\n"
                           "  - {rel: npx:retracts, to: hyp-wrong}\n"
                           "  - {rel: kn:survivedGate, to: method-gate}")
    graph.node("method-gate", "id: method-gate\ntype: method")
    return graph


def test_query_says_a_claim_was_retracted_by_another_node(graph, monkeypatch, capsys):
    """`_summarise` reported what a node RETRACTS but never that it WAS retracted. An
    agent asking "has this been tried?" about a withdrawn claim was told the verdict and
    not the withdrawal — and SPEC calls retraction the most valuable node type."""
    monkeypatch.chdir(graph.root)
    retracted_graph(graph)

    main(["query", "hyp-wrong"])
    out = capsys.readouterr().out

    assert "ret-oops" in out


@pytest.mark.anyio
async def test_mcp_get_surfaces_the_retraction(graph, monkeypatch, anyio_backend):
    from knoten.mcp_server import call_tool

    monkeypatch.chdir(graph.root)
    monkeypatch.delenv("KNOTEN_GRAPH", raising=False)
    retracted_graph(graph)

    out = await call_tool("knoten_get", {"id": "hyp-wrong"})
    res = json.loads(out[0].text)

    assert res["retracted_by"] == ["ret-oops"]


@pytest.fixture
def anyio_backend():
    return "asyncio"

"""What should I work on next?

The graph could answer "has this been tried?" and nothing else. `## What would reopen
this` is called non-negotiable in SPEC §5 because it turns a dead end into a standing
offer — but an offer nobody re-reads is just a note, and re-reading every post-mortem to
find the ones the world has caught up with is exactly the friction that kills a research
log.
"""
import json

import pytest

from knoten.core import frontier, load
from knoten.mcp_server import call_tool

DEAD_WITH_OFFER = """\
id: hyp-dead
type: hypothesis
status: dead
"""

BODY_WITH_OFFER = """\
# A claim

## Why it died
Within noise.

## What would reopen this
A task where the aggregation does real work.
"""


@pytest.fixture
def g(graph):
    graph.node("hyp-open", "id: hyp-open\ntype: hypothesis\nstatus: open", "# Untested\n")
    graph.node("hyp-dead", DEAD_WITH_OFFER, BODY_WITH_OFFER)
    graph.node("method-used", "id: method-used\ntype: method\nstatus: active", "# Gate A\n")
    graph.node("method-idle", "id: method-idle\ntype: method\nstatus: active", "# Gate B\n")
    graph.node("hyp-killed", "id: hyp-killed\ntype: hypothesis\nstatus: dead\nlinks:\n"
                             "  - {rel: kn:killedByGate, to: method-used}", "# Killed\n")
    return graph


def test_open_claims_are_the_first_bucket(g):
    assert [n.id for n in frontier(load(g.root))["open"]] == ["hyp-open"]


def test_a_dead_claim_that_said_what_would_reopen_it_is_reopenable(g):
    out = frontier(load(g.root))["reopenable"]

    assert [n.id for n, _ in out] == ["hyp-dead"]
    assert out[0][1].startswith("A task where the aggregation")


def test_a_dead_claim_with_no_standing_offer_is_not_reopenable(g):
    """Nothing to act on. Listing it would be noise on the one screen that must stay
    short enough to read."""
    assert "hyp-killed" not in [n.id for n, _ in frontier(load(g.root))["reopenable"]]


def test_a_gate_nothing_has_been_through_is_surfaced(g):
    """A gate that has never fired is either useless or never applied, and both are worth
    knowing. It is free to compute — the back-links already exist."""
    assert [n.id for n in frontier(load(g.root))["untested_gates"]] == ["method-idle"]


def test_a_gate_that_killed_something_is_not_untested(g):
    assert "method-used" not in [n.id for n in frontier(load(g.root))["untested_gates"]]


# ------------------------------------------------------------------ surfaces

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_the_agent_surface_returns_all_three_buckets(g, monkeypatch):
    monkeypatch.chdir(g.root)
    monkeypatch.delenv("KNOTEN_GRAPH", raising=False)

    res = json.loads((await call_tool("knoten_frontier", {}))[0].text)

    assert [r["id"] for r in res["open"]] == ["hyp-open"]
    assert res["reopenable"][0]["reopen_if"].startswith("A task where")
    assert [r["id"] for r in res["untested_gates"]] == ["method-idle"]


def test_the_cli_prints_all_three_buckets(g, monkeypatch, capsys):
    from knoten.cli import main
    monkeypatch.chdir(g.root)

    assert main(["frontier"]) == 0
    out = capsys.readouterr().out

    assert "hyp-open" in out
    assert "hyp-dead" in out and "A task where" in out
    assert "method-idle" in out

"""The tool descriptions are the only instructions an agent reliably reads.

They grew one feature at a time, and it showed: four of eight tools opened with a
competing all-caps imperative — "CALL THIS BEFORE STARTING ANY INVESTIGATION", "CALL THIS
BEFORE YOU DESIGN AN EXPERIMENT", "CALL THIS WHEN CHOOSING WHAT TO WORK ON", "USE THIS TO
ASK…". Four shouted priorities give an agent the same ordering signal as none.

The loop belongs in the server's `instructions`, which the client reads once at the
handshake. A tool description says what the tool does and where it sits.
"""
import pytest

from knoten import mcp_server

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_every_tool_has_a_description():
    assert all(t.description for t in await mcp_server.app.list_tools())


async def test_the_server_explains_the_loop_once():
    text = mcp_server.app.instructions or ""

    assert "knoten" in text
    assert len(text) > 200


async def test_every_tool_appears_in_the_loop_instructions():
    """A tool nobody is told to call is a tool nobody calls. This is the check that fails
    when the next feature lands and the workflow blurb is not updated with it."""
    text = mcp_server.app.instructions or ""

    missing = [t.name for t in await mcp_server.app.list_tools() if t.name not in text]

    assert not missing, f"not placed in the workflow: {missing}"


async def test_only_one_tool_claims_to_be_the_starting_point():
    """The failure this file exists for. Emphasis only works if it is scarce."""
    shouty = [t.name for t in await mcp_server.app.list_tools()
              if "BEFORE STARTING" in (t.description or "")
              or "START HERE" in (t.description or "")]

    assert len(shouty) <= 1, f"{shouty} all claim to go first"


async def test_query_does_not_promise_more_than_keyword_search_can_do():
    """It used to say it "Prevents redoing work that is already dead." It cannot, alone —
    an idea worded differently from the node that killed it will not match, which is why
    knoten_index exists. A description that oversells is how an agent stops checking."""
    desc = next(t.description for t in await mcp_server.app.list_tools()
                if t.name == "knoten_query")

    assert "Prevents redoing work that is already dead" not in desc
    assert "knoten_index" in desc


async def test_a_tool_can_actually_be_called_through_the_server(graph, monkeypatch):
    """The other tests call the tool functions directly, which is simpler and faster but
    never touches registration or argument validation. This one goes through the real MCP
    dispatch, so a signature pydantic cannot build a schema from fails here rather than in
    somebody's editor."""
    monkeypatch.chdir(graph.root)
    monkeypatch.delenv("KNOTEN_GRAPH", raising=False)
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead", "# A claim\n")

    res = await mcp_server.app.call_tool("knoten_query", {"query": "claim"})

    assert not res.is_error
    assert "hyp-x" in res.content[0].text


async def test_a_required_argument_is_enforced_by_the_schema():
    """Argument validation is the SDK's job now — there is no hand-written inputSchema to
    disagree with the signature. This pins that it is actually happening."""
    with pytest.raises(Exception):
        await mcp_server.app.call_tool("knoten_query", {})

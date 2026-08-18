"""MCP server — the reason this project exists.

A knowledge base that depends on someone REMEMBERING to write to it will be empty in six
months. So make the graph reachable from inside the work, not alongside it:

    knoten_index()        BEFORE starting   -> don't redo dead work
    knoten_commit(node)   AFTER finishing   -> the graph writes itself

Every tool here is a plain function: its name is the tool name, its docstring is the
description the agent reads, and its annotated signature IS the input schema. There is no
second copy of any of that to drift out of sync, and returning a dict is enough — the SDK
serialises it.

Run:
    knoten-mcp                       # serves the graph found from $PWD
    KNOTEN_GRAPH=/path knoten-mcp    # or point it explicitly
"""
from __future__ import annotations

import functools
import inspect
import os
from pathlib import Path
from typing import Annotated

try:
    from mcp.server import MCPServer
    from pydantic import Field
except ImportError as e:  # pragma: no cover
    try:
        from importlib.metadata import version
        have = version("mcp")
    except Exception:
        have = None
    # ImportError, NOT SystemExit. SystemExit is a BaseException, so pytest cannot demote
    # it to a collection error: on a machine with mcp 1.x, `pytest` aborted the ENTIRE run
    # with INTERNALERROR and ran none of the tests that never touch MCP. The friendly exit
    # belongs in main(), where a human is reading stderr.
    raise ImportError(
        f"knoten-mcp needs mcp>=2 (you have {have}). Upgrade with:\n\n"
        f"    pip install -U 'knoten[mcp]'\n"
        if have else
        "knoten-mcp needs the `mcp` extra:\n\n    pip install 'knoten[mcp]'\n"
    ) from e

from . import attachments, ops
from .commit import commit as commit_node
from .core import ID_RE, GraphError, find_root
from .update import update as update_node

INSTRUCTIONS = """\
knoten is a research graph that remembers what did NOT work. Each node is a claim; a dead
claim carries why it died and what would bring it back.

The loop, in order:

  1. knoten_frontier — what is worth doing next: work left open, dead ends whose stated
     reopen condition may now hold, and gates nothing has been through. A dead end with a
     standing offer is a cheaper experiment than a new idea; the design is already written.
  2. knoten_index / knoten_query — has this been tried? `index` lists the whole graph one
     line per node so you can spot work done in DIFFERENT WORDS; `query` is keyword search,
     faster when your idea has a distinctive name and blind to paraphrase.
  3. knoten_get — the full node for anything that looks close: post-mortem, results, and
     the path of the script that produced them.
  4. knoten_gates — what a result must survive here. Read it BEFORE designing the
     experiment: a claim cannot be filed as alive without citing a gate it survived, so
     meeting the gate at commit time means the compute is already spent.
  5. knoten_commit — file the claim when the work concludes, INCLUDING when it fails. Use
     knoten_update instead if you opened the node earlier and are now closing it.
  6. knoten_attach — the script that ran it and the plot that shows it. A claim nobody can
     re-run is a claim nobody trusts in six months.

knoten_path answers "how did we get from A to B?"; knoten_validate runs the graph's rules.

Writes are gated: commit and update validate against the graph's own declared rules and
refuse on violation. The refusal is the feature. Fix the node and call again.
"""

app = MCPServer("knoten", instructions=INSTRUCTIONS)


def tool(fn):
    """Register a function as a knoten tool.

    An MCP server must ANSWER, never explode: a broken graph.yaml, or a directory passed
    to knoten_attach, has to come back as JSON the agent can act on rather than a
    traceback it can only give up on.

    `cleandoc` because the docstring becomes the description verbatim, and an agent should
    not have to read our source indentation.
    """
    @functools.wraps(fn)
    def guarded(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except GraphError as e:
            return {"error": str(e)}
        except OSError as e:
            return {"error": f"{type(e).__name__}: {e}"}

    guarded.__doc__ = inspect.cleandoc(fn.__doc__ or "")
    app.tool()(guarded)
    return guarded


# Argument types shared by more than one tool. Declared once, described once.
NodeId = Annotated[str, Field(description="node id, kebab-case, e.g. hyp-self-consistency")]
Tags = Annotated[list[str] | None, Field(description="keep nodes carrying any of these tags")]
Statuses = Annotated[list[str] | None, Field(description="keep nodes with any of these statuses")]
Types = Annotated[list[str] | None, Field(description="keep nodes of any of these types")]
Where = Annotated[dict | None, Field(
    description='keep nodes whose frontmatter field is one of these values, '
                'e.g. {"cause": ["weak_baseline"]}')]
Since = Annotated[str | None, Field(
    description="YYYY-MM-DD — only nodes created or updated on/after this day")]


def _root() -> Path:
    if env := os.environ.get("KNOTEN_GRAPH"):
        return Path(env).expanduser()
    return find_root()


# ------------------------------------------------------------ 1. what to work on


@tool
def knoten_frontier() -> dict:
    """Use when choosing what to work on next. Three buckets: claims left open, dead
    claims that stated what would reopen them, and gates no claim has ever been through.
    A dead end with a standing offer is a cheaper experiment than a new idea, because the
    design is already written down.
    """
    return ops.frontier(_root())


# ------------------------------------------------------------ 2. has this been tried


@tool
def knoten_index(
    query: Annotated[str | None,
                     Field(description="optional — rank the rows by relevance to this")] = None,
    tags: Tags = None,
    status: Statuses = None,
    type: Types = None,
    where: Where = None,
    since: Since = None,
    limit: Annotated[int | None, Field(description=f"default {ops.INDEX_LIMIT}")] = None,
) -> dict:
    """START HERE when you have an idea and need to know whether it is new. The whole
    graph, one line per node: id, type, verdict, tags, claim. Read the lines and judge
    relatedness yourself — this is the only way to find work already done in DIFFERENT
    WORDS, which keyword search cannot do. Also answers 'what is still open?'
    (status=['open']). Narrow a large graph with tags / status / type / since before
    reading it.
    """
    return ops.index(_root(), query=query, tags=tags, status=status, type=type,
                     where=where, since=since, limit=limit)


@tool
def knoten_query(query: Annotated[str, Field(description="term, tag, or topic")]) -> dict:
    """Keyword search over the graph, ranked by relevance. Quicker than knoten_index when
    your idea has a distinctive name. It matches WORDS, NOT MEANING: a node phrased
    differently will not appear, so an empty result means 'no keyword match' and NOT
    'never tried' — confirm with knoten_index before concluding an idea is new. Returns
    verdicts (ALIVE/DEAD/RETRACTED), the gates that killed or validated each claim, why it
    died, and what would reopen it.
    """
    return ops.query(_root(), query)


# ------------------------------------------------------------ 3. read one node


@tool
def knoten_get(id: NodeId) -> dict:
    """Fetch one node in full: post-mortem, reproduction recipe, and the paths of any
    attached scripts or plots (read them with your file tools to re-run or inspect the
    experiment).
    """
    return ops.get(_root(), id)


# ------------------------------------------------------------ 4. what must it survive


@tool
def knoten_gates() -> dict:
    """Read this BEFORE you design an experiment. The methodological gates every claim
    here is held to: what to run, why the check exists, and what each has killed or
    validated. A claim cannot be filed as alive without citing a gate it survived, so
    meeting a gate at commit time means the compute is already spent on a result that
    cannot be recorded.
    """
    return ops.gates(_root())


# ------------------------------------------------------------ 5. record the outcome


@tool
def knoten_commit(
    id: NodeId,
    frontmatter: Annotated[str, Field(
        description="YAML frontmatter body (no --- fences). Must include type and status. "
                    "Alive claims need a kn:survivedGate link.")],
    body: Annotated[str, Field(
        description="Markdown. Dead/retracted nodes MUST contain '## Why it died' and "
                    "'## What would reopen this'.")],
) -> dict:
    """Add a node to the graph. VALIDATES FIRST and REFUSES on rule violation — e.g. a
    claim marked 'alive' that cites no gate it survived. Call this when an investigation
    concludes, INCLUDING when it fails. A dead hypothesis with a documented cause of death
    is the most valuable node in the graph. Also reports settled claims the new node
    resembles, so the same question is not filed twice.
    """
    return commit_node(_root(), id, frontmatter, body)


@tool
def knoten_update(
    id: NodeId,
    status: Annotated[str | None, Field(
        description="the new status, e.g. dead. Must be one the graph declares.")] = None,
    append: Annotated[str | None, Field(
        description="markdown appended to the body — this is where '## Why it died' and "
                    "'## What would reopen this' go.")] = None,
    results: Annotated[dict | None, Field(
        description="result keys to add. An existing key cannot be changed to a different "
                    "value.")] = None,
    links: Annotated[list[dict] | None, Field(
        description="edges to add, e.g. [{rel: kn:killedByGate, to: method-x}]")] = None,
) -> dict:
    """Move a node through its lifecycle and append to it: open -> alive / dead /
    retracted. CALL THIS WHEN AN EXPERIMENT YOU OPENED FINISHES, especially when it fails
    — a hypothesis left 'open' forever is a ghost on every future frontier. Appends only:
    it cannot rewrite prose or overwrite a result that was already recorded (retract or
    supersede the node for that). VALIDATES FIRST and REFUSES on rule violation, same as
    knoten_commit.
    """
    try:
        now = update_node(_root(), id, status=status, results=results,
                          links=links, append=append)
    except GraphError as e:
        return {"status": "REJECTED", "node": id, "reason": str(e),
                "hint": "Fix it and update again. The gate is the point."}
    return {"status": "UPDATED", "node": id, "node_status": now,
            "next": "git add + commit to version this."}


# ------------------------------------------------------------ 6. leave the evidence


@tool
def knoten_attach(
    id: NodeId,
    files: Annotated[list[str], Field(description="paths to the files to attach")],
) -> dict:
    """Attach files you produced to a node — the script that ran the experiment, the plot
    that shows the result. Call this after knoten_commit. A claim you cannot re-run is a
    claim nobody will trust in six months; the attachment IS the reproduction. Images are
    embedded in the node body so they render on GitHub. Write the file to disk first, then
    pass its path.
    """
    if not ID_RE.match(id or ""):
        return {"status": "REJECTED", "node": id,
                "reason": "invalid id — use kebab-case, e.g. hyp-self-consistency."}
    try:
        res = attachments.attach(_root(), id, files)
    except (GraphError, OSError) as e:
        return {"status": "REJECTED", "node": id, "reason": str(e)}
    return {"status": "ATTACHED", "node": id,
            "attached": [f"attachments/{id}/{n}" for n in res.added],
            "embedded": res.embedded, "warnings": res.warnings,
            "next": "git add + commit to version this."}


# ------------------------------------------------------------ anytime


@tool
def knoten_path(
    start: Annotated[str, Field(description="node id to start from")],
    end: Annotated[str, Field(description="node id to reach")],
) -> dict:
    """Show the research path between two nodes — how did we get from A to B?"""
    return ops.path(_root(), start, end)


@tool
def knoten_validate() -> dict:
    """Run the graph's own declared rules over every node."""
    return ops.validate(_root())


def main() -> None:
    """The console-script entry point. Import errors reach a human here, as a clean
    message on stderr rather than a traceback — see the ImportError above for why the
    module itself must not exit."""
    import asyncio

    asyncio.run(app.run_stdio_async())


if __name__ == "__main__":
    main()

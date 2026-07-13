"""MCP server — the reason this project exists.

A knowledge base that depends on someone REMEMBERING to write to it will be empty in six
months. So make the graph reachable from inside the work, not alongside it:

    knoten_query("has anyone tested X?")   BEFORE starting  -> don't redo dead work
    knoten_commit(node)                    AFTER finishing   -> the graph writes itself

`knoten_commit` validates the candidate in memory and refuses on violation; nothing
reaches disk until it is clean.

Run:
    knoten-mcp                       # serves the graph found from $PWD
    KNOTEN_GRAPH=/path knoten-mcp    # or point it explicitly
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool
except ImportError as e:  # pragma: no cover
    raise SystemExit(
        "knoten-mcp needs the `mcp` extra:\n\n    pip install 'knoten[mcp]'\n"
    ) from e

from .core import (ID_RE, VERDICT, GraphError, Node, backlink, find_root, matches,
                   parse_text, section, shortest_path)
from .core import load as load_graph
from .validate import check

app = Server("knoten")


def _root() -> Path:
    if env := os.environ.get("KNOTEN_GRAPH"):
        return Path(env).expanduser()
    return find_root()


def _summarise(n: Node) -> dict:
    out = {"id": n.id, "type": n.type, "verdict": VERDICT.get(n.status, n.status or "-")}
    for rel, key in [("kn:killedByGate", "killed_by"), ("kn:survivedGate", "survived_gates"),
                     ("npx:retracts", "retracts"), ("kn:blockedBy", "blocked_by")]:
        if ts := [l["to"] for l in n.links if l["rel"] == rel]:
            out[key] = ts
    if why := section(n.body, "Why it died"):
        out["why_it_died"] = why[:400]
    if reopen := section(n.body, "What would reopen this"):
        out["what_would_reopen_this"] = reopen[:400]
    if n.results:
        out["results"] = n.results
    if n.repro:
        out["repro"] = n.repro
    if n.attachments:
        out["attachments"] = [f"attachments/{n.id}/{a}" for a in n.attachments]
    return out


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="knoten_query",
            description=(
                "Search the research graph. CALL THIS BEFORE STARTING ANY INVESTIGATION. "
                "Returns matching hypotheses with their verdict (ALIVE/DEAD/RETRACTED), "
                "which methodological gates killed or validated them, WHY they died, and "
                "WHAT WOULD REOPEN THEM. Prevents redoing work that is already dead."),
            inputSchema={"type": "object", "properties": {
                "query": {"type": "string", "description": "term, tag, or topic"}},
                "required": ["query"]},
        ),
        Tool(
            name="knoten_get",
            description=("Fetch one node in full: post-mortem, reproduction recipe, and the "
                         "paths of any attached scripts or plots (read them with your file "
                         "tools to re-run or inspect the experiment)."),
            inputSchema={"type": "object", "properties": {
                "id": {"type": "string"}}, "required": ["id"]},
        ),
        Tool(
            name="knoten_commit",
            description=(
                "Add a node to the graph. VALIDATES FIRST and REFUSES on rule violation — "
                "e.g. a claim marked 'alive' that cites no gate it survived. Call this when "
                "an investigation concludes, INCLUDING when it fails. A dead hypothesis with "
                "a documented cause of death is the most valuable node in the graph."),
            inputSchema={"type": "object", "properties": {
                "id": {"type": "string",
                       "description": "kebab-case, e.g. hyp-my-idea. Lowercase letters, "
                                      "digits, - and _ only."},
                "frontmatter": {"type": "string",
                                "description": "YAML frontmatter body (no --- fences). Must "
                                               "include type and status. Alive claims need a "
                                               "kn:survivedGate link."},
                "body": {"type": "string",
                         "description": "Markdown. Dead/retracted nodes MUST contain "
                                        "'## Why it died' and '## What would reopen this'."},
            }, "required": ["id", "frontmatter", "body"]},
        ),
        Tool(
            name="knoten_path",
            description="Show the research path between two nodes — how did we get from A to B?",
            inputSchema={"type": "object", "properties": {
                "from": {"type": "string"}, "to": {"type": "string"}},
                "required": ["from", "to"]},
        ),
        Tool(
            name="knoten_validate",
            description="Run the graph's own declared rules over every node.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


def _commit(root: Path, nodes: dict[str, Node], args: dict) -> dict:
    """Validate the candidate in memory. Nothing touches disk until it is clean."""
    nid = args["id"]
    if not ID_RE.match(nid or ""):
        return {"status": "REJECTED", "node": nid,
                "reason": "invalid id — use kebab-case (lowercase letters, digits, - and _), "
                          "e.g. hyp-self-consistency. An id becomes a filename."}
    path = root / "nodes" / f"{nid}.md"
    if path.exists():
        return {"status": "REJECTED", "node": nid,
                "reason": f"'{nid}' already exists. Supersede or retract it instead of "
                          "overwriting — corrections are nodes, not edits."}

    text = f"---\n{args['frontmatter'].strip()}\n---\n\n{args['body'].strip()}\n"
    try:
        candidate = parse_text(text, nid)
    except GraphError as e:
        return {"status": "REJECTED", "node": nid, "reason": str(e)}

    if errs := [e for e in check(backlink({**nodes, nid: candidate}), root) if e.node == nid]:
        return {"status": "REJECTED", "node": nid,
                "violations": [{"rule": e.rule, "message": e.message} for e in errs],
                "hint": "Fix the violations and commit again. The gate is the point."}

    path.write_text(text, encoding="utf-8")
    return {"status": "COMMITTED", "node": nid, "path": f"nodes/{nid}.md",
            "graph_size": len(nodes) + 1,
            "next": "git add + commit to version this."}


@app.call_tool()
async def call_tool(name: str, args: dict) -> list[TextContent]:
    def ok(payload) -> list[TextContent]:
        return [TextContent(type="text", text=json.dumps(payload, indent=2, default=str))]

    try:
        root = _root()
        nodes = load_graph(root)
    except GraphError as e:
        return ok({"error": str(e)})

    if name == "knoten_query":
        hits = [n for n in nodes.values() if matches(n, args["query"])]
        claims = [_summarise(n) for n in hits if n.status in VERDICT]
        return ok({"query": args["query"], "claims": claims,
                   "related_methods": [n.id for n in hits if n.type == "method"],
                   "note": ("Claims marked DEAD or RETRACTED have already been tested. "
                            "Read 'what_would_reopen_this' before re-running them.")
                   if claims else "No prior work found. This appears untested."})

    if name == "knoten_get":
        n = nodes.get(args["id"])
        if not n:
            return ok({"error": f"no node '{args['id']}'", "available": sorted(nodes)})
        return ok({**_summarise(n), "frontmatter": n.frontmatter,
                   "links": n.links, "backlinks": n.backlinks, "body": n.body})

    if name == "knoten_commit":
        return ok(_commit(root, nodes, args))

    if name == "knoten_path":
        a, b = args["from"], args["to"]
        p = shortest_path(nodes, a, b)
        if p is None:
            return ok({"path": None, "note": f"no path {a} -> {b}"})
        # WITH the relation on each hop. Without it an agent learns two nodes are
        # connected but not HOW, which is useless for reasoning about falsification.
        return ok({"path": [{"node": nid, "via": rel} if rel else {"node": nid}
                            for nid, rel in p],
                   "hops": len(p) - 1})

    if name == "knoten_validate":
        errs = check(nodes, root)
        return ok({"nodes": len(nodes), "valid": not errs,
                   "violations": [{"node": e.node, "rule": e.rule, "message": e.message}
                                  for e in errs]})

    return ok({"error": f"unknown tool {name}"})


def main() -> None:
    import asyncio

    async def run():
        async with stdio_server() as (r, w):
            await app.run(r, w, app.create_initialization_options())

    asyncio.run(run())


if __name__ == "__main__":
    main()

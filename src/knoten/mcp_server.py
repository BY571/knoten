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
from pathlib import Path

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool
except ImportError as e:  # pragma: no cover
    raise SystemExit(
        "knoten-mcp needs the `mcp` extra:\n\n    pip install 'knoten[mcp]'\n"
    ) from e

from . import attachments
from .update import update as update_node
from .core import (ID_RE, VERDICT, GraphError, Node, backlink, find_root, node_path,
                   parse_text, retrieve, section, shortest_path)
from .core import load as load_graph
from .validate import check, load_config

app = Server("knoten")

# A row is ~45 tokens once JSON key overhead is counted, so 200 rows is ~9k — readable
# in one go, where the same graph's broad query was 83k. It is a CAP, never a silent one:
# `truncated` and `total` always say what was left out.
INDEX_LIMIT = 200

# A query returns FULL summaries (post-mortem, results, repro) — a few hundred tokens
# each. Twenty is a read; sixty is a context flood that buries the top hit.
QUERY_LIMIT = 20


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
    # A claim someone later WITHDREW is invisible unless we say so: we reported what a node
    # retracts, never that it WAS retracted.
    for rel, key in [("npx:retractedBy", "retracted_by"), ("npx:supersededBy", "superseded_by")]:
        if ts := [b["to"] for b in n.backlinks if b["rel"] == rel]:
            out[key] = ts
            out["warning"] = (f"This claim was {key.replace('_', ' ')} {', '.join(ts)}. "
                              f"Read that node before relying on this one.")
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
            name="knoten_index",
            description=(
                "The whole graph, one line per node: id, type, verdict, tags, claim. USE "
                "THIS TO ASK 'have we done anything LIKE this?' — read the lines and judge "
                "relatedness yourself; a differently-worded idea will not be found by "
                "keyword search. Also answers 'what is still open?' (status=['open']). "
                "Narrow a large graph with tags/status/type before reading it."),
            inputSchema={"type": "object", "properties": {
                "query": {"type": "string",
                          "description": "optional — rank the rows by relevance to this"},
                "tags": {"type": "array", "items": {"type": "string"},
                         "description": "keep nodes carrying any of these tags"},
                "status": {"type": "array", "items": {"type": "string"}},
                "type": {"type": "array", "items": {"type": "string"}},
                "where": {"type": "object",
                          "description": "keep nodes whose frontmatter field is one of "
                                         "these values, e.g. "
                                         "{\"cause\": [\"weak_baseline\"]}"},
                "limit": {"type": "integer", "description": f"default {INDEX_LIMIT}"},
            }},
        ),
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
            name="knoten_update",
            description=(
                "Move a node through its lifecycle and append to it: open -> alive / dead "
                "/ retracted. CALL THIS WHEN AN EXPERIMENT YOU OPENED FINISHES, especially "
                "when it fails — a hypothesis left 'open' forever is a ghost on every "
                "future frontier. Appends only: it cannot rewrite prose or overwrite a "
                "result that was already recorded (retract or supersede the node for "
                "that). VALIDATES FIRST and REFUSES on rule violation, same as "
                "knoten_commit."),
            inputSchema={"type": "object", "properties": {
                "id": {"type": "string"},
                "status": {"type": "string",
                           "description": "the new status, e.g. dead. Must be one the "
                                          "graph declares."},
                "append": {"type": "string",
                           "description": "markdown appended to the body — this is where "
                                          "'## Why it died' and '## What would reopen "
                                          "this' go."},
                "results": {"type": "object",
                            "description": "result keys to add. An existing key cannot be "
                                           "changed to a different value."},
                "links": {"type": "array", "items": {"type": "object"},
                          "description": "edges to add, e.g. "
                                         "[{rel: kn:killedByGate, to: method-x}]"},
            }, "required": ["id"]},
        ),
        Tool(
            name="knoten_attach",
            description=(
                "Attach files you produced to a node — the script that ran the experiment, "
                "the plot that shows the result. Call this after knoten_commit. A claim you "
                "cannot re-run is a claim nobody will trust in six months; the attachment IS "
                "the reproduction. Images are embedded in the node body so they render on "
                "GitHub. Write the file to disk first, then pass its path."),
            inputSchema={"type": "object", "properties": {
                "id": {"type": "string", "description": "the node to attach to"},
                "files": {"type": "array", "items": {"type": "string"},
                          "description": "paths to the files to attach"},
            }, "required": ["id", "files"]},
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
    try:
        path = node_path(root, nid)
    except GraphError as e:
        return {"status": "REJECTED", "node": nid, "reason": str(e)}
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


def ok(payload) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(payload, indent=2, default=str))]


@app.call_tool()
async def call_tool(name: str, args: dict) -> list[TextContent]:
    """An MCP server must ANSWER, never explode: a broken graph.yaml or a directory passed
    to knoten_attach must come back as JSON the agent can act on, not a traceback."""
    try:
        return await _dispatch(name, args)
    except GraphError as e:
        return ok({"error": str(e)})
    except OSError as e:
        return ok({"error": f"{type(e).__name__}: {e}"})


async def _dispatch(name: str, args: dict) -> list[TextContent]:
    root = _root()
    nodes = load_graph(root)

    if name == "knoten_index":
        hits = retrieve(nodes, args.get("query"), tags=args.get("tags"),
                        status=args.get("status"), type=args.get("type"),
                        where=args.get("where"))
        limit = max(1, int(args.get("limit") or INDEX_LIMIT))
        rows = [{"id": n.id, "type": n.type,
                 "verdict": VERDICT.get(n.status, n.status or "-"),
                 "tags": n.tags, "title": n.title} for n in hits[:limit]]
        out = {"total": len(hits), "truncated": len(hits) > limit, "nodes": rows,
               "declared_tags": [str(t) for t in (load_config(root).get("tags") or [])]}
        if out["truncated"]:
            # A silent cap reads as "that is the whole graph" — the same false negative
            # the AND-query bug produced, arriving by a different route.
            out["note"] = (f"Showing {limit} of {len(hits)}. Narrow with tags/status/type, "
                           f"or pass a query to rank by relevance, before concluding "
                           f"anything about what is NOT here.")
        return ok(out)

    if name == "knoten_query":
        hits = retrieve(nodes, args["query"])
        claims = [n for n in hits if n.status in VERDICT]      # already relevance-ranked
        shown = claims[:QUERY_LIMIT]
        out = {"query": args["query"], "total": len(claims),
               "truncated": len(claims) > QUERY_LIMIT,
               "claims": [_summarise(n) for n in shown],
               "related_methods": [n.id for n in hits if n.type == "method"]}
        if out["truncated"]:
            out["note"] = (f"Showing the {QUERY_LIMIT} closest of {len(claims)} matching "
                           f"claims. Use knoten_index for the full picture in one line "
                           f"per node.")
        elif claims:
            out["note"] = ("Claims marked DEAD or RETRACTED have already been tested. "
                           "Read 'what_would_reopen_this' before re-running them.")
        else:
            # Keyword search cannot find an idea phrased in words the node never used.
            # Saying "untested" here without that caveat is how an agent re-runs a dead
            # experiment — the exact failure this tool exists to prevent.
            out["note"] = ("No keyword match. This is NOT proof the idea is untested — a "
                           "differently-worded node will not match. Call knoten_index "
                           "and read the claims yourself before concluding it is new.")
        return ok(out)

    if name == "knoten_get":
        n = nodes.get(args["id"])
        if not n:
            return ok({"error": f"no node '{args['id']}'", "available": sorted(nodes)})
        return ok({**_summarise(n), "frontmatter": n.frontmatter,
                   "links": n.links, "backlinks": n.backlinks, "body": n.body})

    if name == "knoten_commit":
        return ok(_commit(root, nodes, args))

    if name == "knoten_update":
        try:
            now = update_node(root, args["id"], status=args.get("status"),
                              results=args.get("results"), links=args.get("links"),
                              append=args.get("append"))
        except GraphError as e:
            return ok({"status": "REJECTED", "node": args["id"], "reason": str(e),
                       "hint": "Fix it and update again. The gate is the point."})
        return ok({"status": "UPDATED", "node": args["id"], "node_status": now,
                   "next": "git add + commit to version this."})

    if name == "knoten_attach":
        nid = args["id"]
        if not ID_RE.match(nid or ""):
            return ok({"status": "REJECTED", "node": nid,
                       "reason": "invalid id — use kebab-case, e.g. hyp-self-consistency."})
        try:
            res = attachments.attach(root, nid, args["files"])
        except (GraphError, OSError) as e:
            return ok({"status": "REJECTED", "node": nid, "reason": str(e)})
        return ok({"status": "ATTACHED", "node": nid,
                   "attached": [f"attachments/{nid}/{n}" for n in res.added],
                   "embedded": res.embedded, "warnings": res.warnings,
                   "next": "git add + commit to version this."})

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

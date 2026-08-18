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
import re
from pathlib import Path
from typing import Annotated

try:
    from mcp.server import MCPServer
    from pydantic import Field
except ImportError as e:  # pragma: no cover
    raise SystemExit(
        "knoten-mcp needs the `mcp` extra:\n\n    pip install 'knoten[mcp]'\n"
    ) from e

from . import attachments
from .core import (GATE_SECTIONS, ID_RE, VERDICT, GraphError, Node, backlink, fields,
                   find_root, frontier, gates, graph_lock, node_path, parse_text, retrieve,
                   section, shortest_path, today, write_atomic)
from .core import load as load_graph
from .update import update as update_node
from .validate import check, load_config

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

# A row is ~45 tokens once JSON key overhead is counted, so 200 rows is ~9k — readable
# in one go, where the same graph's broad query was 83k. It is a CAP, never a silent one:
# `truncated` and `total` always say what was left out.
INDEX_LIMIT = 200

# A query returns FULL summaries (post-mortem, results, repro) — a few hundred tokens
# each. Twenty is a read; sixty is a context flood that buries the top hit.
QUERY_LIMIT = 20


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


def _graph() -> tuple[Path, dict[str, Node]]:
    root = _root()
    return root, load_graph(root)


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


# ------------------------------------------------------------ 1. what to work on


@tool
def knoten_frontier() -> dict:
    """Use when choosing what to work on next. Three buckets: claims left open, dead
    claims that stated what would reopen them, and gates no claim has ever been through.
    A dead end with a standing offer is a cheaper experiment than a new idea, because the
    design is already written down.
    """
    f = frontier(_graph()[1])
    return {
        "open": [{"id": n.id, "title": n.title} for n in f["open"]],
        "reopenable": [{"id": n.id, "title": n.title, "reopen_if": offer}
                       for n, offer in f["reopenable"]],
        "untested_gates": [{"id": n.id, "title": n.title} for n in f["untested_gates"]],
        "note": ("A reopenable claim states its own condition. Judge whether it holds now "
                 "— knoten does not, because that is the research."),
    }


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
    limit: Annotated[int | None, Field(description=f"default {INDEX_LIMIT}")] = None,
) -> dict:
    """START HERE when you have an idea and need to know whether it is new. The whole
    graph, one line per node: id, type, verdict, tags, claim. Read the lines and judge
    relatedness yourself — this is the only way to find work already done in DIFFERENT
    WORDS, which keyword search cannot do. Also answers 'what is still open?'
    (status=['open']). Narrow a large graph with tags / status / type / since before
    reading it.
    """
    root, nodes = _graph()
    hits = retrieve(nodes, query, tags=tags, status=status, type=type,
                    where=where, since=since)
    cap = max(1, int(limit or INDEX_LIMIT))
    out = {
        "total": len(hits),
        "truncated": len(hits) > cap,
        "nodes": [{"id": n.id, "type": n.type,
                   "verdict": VERDICT.get(n.status, n.status or "-"),
                   "tags": n.tags, "title": n.title} for n in hits[:cap]],
        "declared_tags": [str(t) for t in (load_config(root).get("tags") or [])],
    }
    if out["truncated"]:
        # A silent cap reads as "that is the whole graph" — the same false negative the
        # AND-query bug produced, arriving by a different route.
        out["note"] = (f"Showing {cap} of {len(hits)}. Narrow with tags/status/type, or "
                       f"pass a query to rank by relevance, before concluding anything "
                       f"about what is NOT here.")
    return out


@tool
def knoten_query(query: Annotated[str, Field(description="term, tag, or topic")]) -> dict:
    """Keyword search over the graph, ranked by relevance. Quicker than knoten_index when
    your idea has a distinctive name. It matches WORDS, NOT MEANING: a node phrased
    differently will not appear, so an empty result means 'no keyword match' and NOT
    'never tried' — confirm with knoten_index before concluding an idea is new. Returns
    verdicts (ALIVE/DEAD/RETRACTED), the gates that killed or validated each claim, why it
    died, and what would reopen it.
    """
    hits = retrieve(_graph()[1], query)
    claims = [n for n in hits if n.status in VERDICT]      # already relevance-ranked
    out = {"query": query, "total": len(claims),
           "truncated": len(claims) > QUERY_LIMIT,
           "claims": [_summarise(n) for n in claims[:QUERY_LIMIT]],
           "related_methods": [n.id for n in hits if n.type == "method"]}
    if out["truncated"]:
        out["note"] = (f"Showing the {QUERY_LIMIT} closest of {len(claims)} matching "
                       f"claims. Use knoten_index for the full picture in one line per "
                       f"node.")
    elif claims:
        out["note"] = ("Claims marked DEAD or RETRACTED have already been tested. Read "
                       "'what_would_reopen_this' before re-running them.")
    else:
        # Keyword search cannot find an idea phrased in words the node never used. Saying
        # "untested" here without that caveat is how an agent re-runs a dead experiment —
        # the exact failure this tool exists to prevent.
        out["note"] = ("No keyword match. This is NOT proof the idea is untested — a "
                       "differently-worded node will not match. Call knoten_index and "
                       "read the claims yourself before concluding it is new.")
    return out


# ------------------------------------------------------------ 3. read one node


@tool
def knoten_get(id: NodeId) -> dict:
    """Fetch one node in full: post-mortem, reproduction recipe, and the paths of any
    attached scripts or plots (read them with your file tools to re-run or inspect the
    experiment).
    """
    nodes = _graph()[1]
    if not (n := nodes.get(id)):
        return {"error": f"no node '{id}'", "available": sorted(nodes)}
    return {**_summarise(n), "frontmatter": n.frontmatter,
            "links": n.links, "backlinks": n.backlinks, "body": n.body}


# ------------------------------------------------------------ 4. what must it survive


@tool
def knoten_gates() -> dict:
    """Read this BEFORE you design an experiment. The methodological gates every claim
    here is held to: what to run, why the check exists, and what each has killed or
    validated. A claim cannot be filed as alive without citing a gate it survived, so
    meeting a gate at commit time means the compute is already spent on a result that
    cannot be recorded.
    """
    rule, why = GATE_SECTIONS
    out = []
    for n, killed, survived in gates(_graph()[1]):
        row = {"id": n.id, "title": n.title, "killed": killed, "survived": survived}
        if r := section(n.body, rule):
            row["rule"] = r
        if w := section(n.body, why):
            row["why_it_exists"] = w
        out.append(row)
    return {"gates": out,
            "note": ("Design the experiment to pass these. A gate with nothing in "
                     "`killed` or `survived` has never been applied.")}


# ------------------------------------------------------------ 5. record the outcome


def _similar(nodes: dict[str, Node], candidate: Node, keep: int = 3) -> list[dict]:
    """Settled claims that look like the same question, worded differently.

    A warning and never a block: two claims can be genuinely close and genuinely
    different — a compute-matched rerun of a dead idea IS a new claim, and that is the
    whole point of a gate. Refusing would make the tool wrong in the interesting case and
    push the agent to route around it.

    Settled claims only — `open` is not an answer, and reporting one would tell the agent
    the question is closed when it is exactly what is still being asked. Plus at least two
    shared title words, so a single shared "accuracy" does not fire.

    Two is a loose bar on purpose. A false positive costs one line of JSON the agent can
    dismiss; a false negative costs a duplicated experiment, which is the failure this
    whole tool exists to prevent. The asymmetry says lean permissive.
    """
    mine = fields(candidate)[0]
    out = []
    for n in retrieve(nodes, candidate.title or candidate.id):
        if n.status not in VERDICT or len(mine & fields(n)[0]) < 2:
            continue
        row = {"id": n.id, "verdict": VERDICT[n.status], "title": n.title}
        if why := section(n.body, "Why it died"):
            row["why_it_died"] = why[:200]
        out.append(row)
    return out[:keep]


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
    root, nodes = _graph()
    with graph_lock(root):
        try:
            path = node_path(root, id)
        except GraphError as e:
            return {"status": "REJECTED", "node": id, "reason": str(e)}
        if path.exists():
            return {"status": "REJECTED", "node": id,
                    "reason": f"'{id}' already exists. Supersede or retract it instead of "
                              "overwriting — corrections are nodes, not edits."}

        fm = frontmatter.strip()
        # Stamped unless the author said otherwise. A graph with no time axis cannot
        # answer "what did we learn this week" or spot a hypothesis open since March.
        if not re.search(r"^created:", fm, re.M):
            fm += f"\ncreated: {today()}"
        text = f"---\n{fm}\n---\n\n{body.strip()}\n"

        try:
            candidate = parse_text(text, id)
        except GraphError as e:
            return {"status": "REJECTED", "node": id, "reason": str(e)}

        if errs := [e for e in check(backlink({**nodes, id: candidate}), root)
                    if e.node == id]:
            return {"status": "REJECTED", "node": id,
                    "violations": [{"rule": e.rule, "message": e.message} for e in errs],
                    "hint": "Fix the violations and commit again. The gate is the point."}

        write_atomic(path, text)

    out = {"status": "COMMITTED", "node": id, "path": f"nodes/{id}.md",
           "graph_size": len(nodes) + 1,
           "next": "git add + commit to version this."}
    if similar := _similar(nodes, candidate):
        out["similar"] = similar
        out["warning"] = (
            f"This resembles {len(similar)} settled claim(s). If it is the same question, "
            f"supersede or retract that node (npx:supersedes / npx:retracts) rather than "
            f"leaving two answers in the graph.")
    return out


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
    p = shortest_path(_graph()[1], start, end)
    if p is None:
        return {"path": None, "note": f"no path {start} -> {end}"}
    # WITH the relation on each hop. Without it an agent learns two nodes are connected
    # but not HOW, which is useless for reasoning about falsification.
    return {"path": [{"node": nid, "via": rel} if rel else {"node": nid}
                     for nid, rel in p],
            "hops": len(p) - 1}


@tool
def knoten_validate() -> dict:
    """Run the graph's own declared rules over every node."""
    root, nodes = _graph()
    errs = check(nodes, root)
    return {"nodes": len(nodes), "valid": not errs,
            "violations": [{"node": e.node, "rule": e.rule, "message": e.message}
                           for e in errs]}


def main() -> None:
    import asyncio

    asyncio.run(app.run_stdio_async())


if __name__ == "__main__":
    main()

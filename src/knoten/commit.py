"""Filing a new claim.

The gate is the point: the candidate is parsed and rule-checked IN MEMORY, and nothing
reaches the filesystem until it is clean. An agent cannot record a shiny result that cites
no test it survived.

This lived inside the MCP server, which put the domain logic in the transport layer and —
worse — made writing a node from Python require that server's SDK, an optional dependency for
a transport you may not be using. `attach` and `update` never had that problem.
"""
from __future__ import annotations

import re
from pathlib import Path

from .core import (VERDICT, GraphError, Node, backlink, fields, graph_lock, load,
                   node_path, parse_text, retrieve, section, today, write_atomic)
from .validate import check


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


def commit(root: Path, nid: str, frontmatter: str, body: str) -> dict:
    """Write a new node, or report why it cannot be written. Never raises for a bad
    candidate — the caller is usually an agent, and a refusal it can read and act on beats
    a traceback it can only give up on."""
    with graph_lock(root):
        # Loaded INSIDE the lock. Read outside it, the snapshot goes stale the moment a
        # peer commits, and a claim citing the gate that peer just created is rejected for
        # a dangling edge to a node already on disk.
        nodes = load(root)
        try:
            path = node_path(root, nid)
        except GraphError as e:
            return {"status": "REJECTED", "node": nid, "reason": str(e)}
        if path.exists():
            return {"status": "REJECTED", "node": nid,
                    "reason": f"'{nid}' already exists. Supersede or retract it instead of "
                              "overwriting — corrections are nodes, not edits."}

        fm = frontmatter.strip()
        # Stamped unless the author said otherwise. A graph with no time axis cannot
        # answer "what did we learn this week" or spot a hypothesis open since March.
        if not re.search(r"^created:", fm, re.M):
            fm += f"\ncreated: {today()}"
        text = f"---\n{fm}\n---\n\n{body.strip()}\n"

        try:
            candidate = parse_text(text, nid)
        except GraphError as e:
            return {"status": "REJECTED", "node": nid, "reason": str(e)}

        if errs := [e for e in check(backlink({**nodes, nid: candidate}), root)
                    if e.node == nid]:
            return {"status": "REJECTED", "node": nid,
                    "violations": [{"rule": e.rule, "message": e.message} for e in errs],
                    "hint": "Fix the violations and commit again. The gate is the point."}

        write_atomic(path, text)

    out = {"status": "COMMITTED", "node": nid, "path": f"nodes/{nid}.md",
           "graph_size": len(nodes) + 1,
           "next": "git add + commit to version this."}
    if similar := _similar(nodes, candidate):
        out["similar"] = similar
        out["warning"] = (
            f"This resembles {len(similar)} settled claim(s). If it is the same question, "
            f"supersede or retract that node (npx:supersedes / npx:retracts) rather than "
            f"leaving two answers in the graph.")
    return out

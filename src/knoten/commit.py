"""Filing a new claim.

The gate is the point: the candidate is parsed and rule-checked IN MEMORY, and nothing
reaches the filesystem until it is clean. An agent cannot record a shiny result that cites
no test it survived.
"""
from __future__ import annotations

import re
from pathlib import Path

from .core import (VERDICT, GraphError, Node, fields, graph_lock, load,
                   node_path, parse_text, retrieve, section, supersedes, today,
                   write_atomic)
from .update import compression_report, refused, superseded_candidates, superseded_texts


def _similar(nodes: dict[str, Node], candidate: Node, keep: int = 3) -> list[dict]:
    """Settled claims that look like the same question, worded differently. A warning and
    never a block: a compute-matched rerun of a dead idea IS a new claim. Settled only
    (`open` is not an answer), plus two shared title words so one shared "accuracy" does
    not fire -- loose on purpose, since a false negative costs a duplicated experiment."""
    mine = fields(candidate)[0]
    # Never what this candidate just superseded: those resemble it by construction.
    own = set(supersedes(candidate))
    out = []
    for n in retrieve(nodes, candidate.title or candidate.id):
        if n.id in own or n.status not in VERDICT or len(mine & fields(n)[0]) < 2:
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
        # INSIDE the lock: read outside it, the snapshot goes stale the moment a peer
        # commits, and a claim citing that peer's new gate is rejected as dangling.
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

        targets = supersedes(candidate)

        # The whole post-write graph is validated BEFORE anything is written, so a
        # target that fails once it is `superseded`, or a third node the flip breaks,
        # refuses the commit rather than raising midway through the write. `cands` parses
        # the SAME strings written below, so what is validated is what lands. Both calls
        # turn a target id into a path and raise on an illegal one, which `commit` owes
        # the caller as a readable refusal rather than a traceback.
        try:
            texts = superseded_texts(root, nodes, candidate) if targets else {}
            cands = {nid: candidate, **superseded_candidates(root, texts)}
        except GraphError as e:
            return {"status": "REJECTED", "node": nid, "reason": str(e)}
        if errs := refused(nodes, cands, root, bool(targets)):
            # Each violation names its own node: a flip can break a node that is neither
            # the general node nor one of its targets.
            return {"status": "REJECTED", "node": nid,
                    "violations": [{"node": e.node, "rule": e.rule, "message": e.message}
                                   for e in errs],
                    "hint": "Fix the violations and commit again. The gate is the point."}

        write_atomic(path, text)
        for tid, ttext in texts.items():
            write_atomic(node_path(root, tid), ttext)
        flipped = list(texts.keys())

        report = compression_report(root, candidate, flipped) if targets else None

    out = {"status": "COMMITTED", "node": nid, "path": f"nodes/{nid}.md",
           "graph_size": len(nodes) + 1,
           "next": "git add + commit to version this."}
    if report is not None:
        out["compressed"] = report
    if similar := _similar(nodes, candidate):
        out["similar"] = similar
        out["warning"] = (
            f"This resembles {len(similar)} settled claim(s). If it is the same question, "
            f"supersede or retract that node (npx:supersedes / npx:retracts) rather than "
            f"leaving two answers in the graph.")
    return out

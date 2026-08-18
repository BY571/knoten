"""Every question the graph answers, as a dict.

One implementation per operation. The CLI renders these dicts as prose, `--json` dumps
them, and the MCP tools serialise them — so the three surfaces cannot drift, which they
did repeatedly when the read paths were written twice.
"""
from __future__ import annotations

from pathlib import Path

from .core import (GATE_SECTIONS, VERDICT, Node, frontier as _frontier,
                   gates as _gates, load, retrieve, section, shortest_path)
from .validate import check, load_config

# A row is ~45 tokens once JSON key overhead is counted, so 200 rows is ~9k — readable
# in one go, where the same graph's broad query was 83k. It is a CAP, never a silent one:
# `truncated` and `total` always say what was left out.
INDEX_LIMIT = 200

# A query returns FULL summaries (post-mortem, results, repro) — a few hundred tokens
# each. Twenty is a read; sixty is a context flood that buries the top hit.
QUERY_LIMIT = 20


def summarise(n: Node) -> dict:
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


def frontier(root: Path) -> dict:
    f = _frontier(load(root))
    return {
        "open": [{"id": n.id, "title": n.title} for n in f["open"]],
        "reopenable": [{"id": n.id, "title": n.title, "reopen_if": offer}
                       for n, offer in f["reopenable"]],
        "untested_gates": [{"id": n.id, "title": n.title} for n in f["untested_gates"]],
        "note": ("A reopenable claim states its own condition. Judge whether it holds now "
                 "— knoten does not, because that is the research."),
    }


def index(root: Path, query=None, tags=None, status=None, type=None,
          where=None, since=None, limit=None) -> dict:
    nodes = load(root)
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


def query(root: Path, term: str) -> dict:
    hits = retrieve(load(root), term)
    claims = [n for n in hits if n.status in VERDICT]      # already relevance-ranked
    out = {"query": term, "total": len(claims),
           "truncated": len(claims) > QUERY_LIMIT,
           "claims": [summarise(n) for n in claims[:QUERY_LIMIT]],
           # MCP contract — do not narrow, an agent tool call is keyed on this shape.
           "related_methods": [n.id for n in hits if n.type == "method"],
           # Everything else that matched but isn't a claim: sources, open work, whatever
           # types this graph declares. The CLI's "also:" line used to show these before
           # it was rewired onto this dict; losing them was silent narrowing, not a fix.
           "related": [n.id for n in hits if n.status not in VERDICT]}
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


def get(root: Path, nid: str) -> dict:
    nodes = load(root)
    if not (n := nodes.get(nid)):
        return {"error": f"no node '{nid}'", "available": sorted(nodes)}
    return {**summarise(n), "frontmatter": n.frontmatter,
            "links": n.links, "backlinks": n.backlinks, "body": n.body}


def gates(root: Path) -> dict:
    rule, why = GATE_SECTIONS
    out = []
    for n, killed, survived in _gates(load(root)):
        row = {"id": n.id, "title": n.title, "killed": killed, "survived": survived}
        if r := section(n.body, rule):
            row["rule"] = r
        if w := section(n.body, why):
            row["why_it_exists"] = w
        out.append(row)
    return {"gates": out,
            "note": ("Design the experiment to pass these. A gate with nothing in "
                     "`killed` or `survived` has never been applied.")}


def validate(root: Path) -> dict:
    nodes = load(root)
    errs = check(nodes, root)
    return {"nodes": len(nodes), "valid": not errs,
            "violations": [{"node": e.node, "rule": e.rule, "message": e.message}
                           for e in errs]}


def path(root: Path, start: str, end: str) -> dict:
    p = shortest_path(load(root), start, end)
    if p is None:
        return {"path": None, "note": f"no path {start} -> {end}"}
    # WITH the relation on each hop. Without it an agent learns two nodes are connected
    # but not HOW, which is useless for reasoning about falsification.
    return {"path": [{"node": nid, "via": rel} if rel else {"node": nid}
                     for nid, rel in p],
            "hops": len(p) - 1}

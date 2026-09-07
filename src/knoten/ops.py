"""Every question the graph answers, as a dict.

One implementation per operation. The CLI renders these dicts as prose and `--json` dumps
them verbatim, so the two renderings cannot disagree.
"""
from __future__ import annotations

from pathlib import Path

from .core import (GATE_SECTIONS, GATE_TYPE, VERDICT, GraphError, Node,
                   compressible_types, frontier as _frontier, gates as _gates, is_general,
                   load, metric as _metric, metrics_declared, metrics_summary, retrieve,
                   section, shape, shortest_path, supersedes)
from .update import update_with_report
from .validate import check, load_config

# ~45 tokens a row, so 200 rows is ~9k: readable in one go. A CAP, never a silent one --
# `truncated` and `total` always say what was left out.
INDEX_LIMIT = 200

# A query returns FULL summaries — a few hundred tokens each. Twenty is a read; sixty is
# a context flood that buries the top hit.
QUERY_LIMIT = 20


def summarise(n: Node) -> dict:
    out = {"id": n.id, "type": n.type, "verdict": VERDICT.get(n.status, n.status or "-")}
    for rel, key in [("kn:killedByGate", "killed_by"), ("kn:survivedGate", "survived_gates"),
                     ("npx:retracts", "retracts"), ("kn:blockedBy", "blocked_by")]:
        if ts := [l["to"] for l in n.links if l["rel"] == rel]:
            out[key] = ts
    # A claim someone later WITHDREW is invisible unless we say so.
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
    nodes = load(root)
    cfg = load_config(root)
    f = _frontier(nodes, compressible_types(cfg))
    s = shape(nodes, cfg)
    s["clusters"] = len(f["compressible"])
    return {
        "shape": s,
        "compressible": f["compressible"],
        "open": [{"id": n.id, "title": n.title} for n in f["open"]],
        "unchecked": [{"id": n.id, "title": n.title} for n in f["unchecked"]],
        "reopenable": [{"id": n.id, "title": n.title, "reopen_if": offer}
                       for n, offer in f["reopenable"]],
        "untested_gates": [{"id": n.id, "title": n.title} for n in f["untested_gates"]],
        "note": ("A reopenable claim states its own condition. Judge whether it holds now "
                 "— knoten does not, because that is the research."),
    }


def metrics(root: Path, name: str | None = None) -> dict:
    """Where a declared number stands: every metric at a glance, or one of them in full.

    A metric is not a new kind of node. It is `results:` read along the time axis, so
    nothing has to be written twice, and a graph that declares none pays nothing.
    """
    nodes, cfg = load(root), load_config(root)
    declared = metrics_declared(cfg)
    if name is None:
        rows = []
        for row in metrics_summary(nodes, cfg):
            best = nodes.get(row["best_id"]) if row["best_id"] else None
            rows.append({**row, "best_created":
                         str(best.frontmatter.get("created") or "") if best else None})
        return {"metrics": rows,
                "note": ("Read the best point and what it built on before choosing what "
                         "to try. knoten does not judge whether the move was worth it.")}
    if name not in declared:
        # Naming the declared ones is the whole refusal: a typo'd metric is otherwise
        # indistinguishable from one nothing has recorded yet.
        return {"error": (f"no metric '{name}' declared in graph.yaml"
                          + (f". Declared: {', '.join(sorted(declared))}" if declared else
                             ". This graph declares none; add `metrics:` to graph.yaml")),
                "declared": sorted(declared)}
    goal = declared[name]
    points = _metric(nodes, name, goal)
    # The best point is whichever row `metrics_summary` named, looked up here rather than
    # decided again: a header that disagrees with the strip above it is two answers.
    top = next(r for r in metrics_summary(nodes, cfg) if r["name"] == name)
    return {"name": name, "goal": goal, "points": points,
            "best": next((p for p in points if p["id"] == top["best_id"]), None)}


def index(root: Path, query=None, tags=None, status=None, type=None,
          where=None, since=None, limit=None, all=False) -> dict:
    nodes = load(root)
    hits = retrieve(nodes, query, tags=tags, status=status, type=type,
                    where=where, since=since)
    # The layer compression retired: hidden unless asked for, so the graph reads as small
    # as it has become; never silently, so the footer counts it.
    hidden = 0
    if not all and not status:
        kept = [n for n in hits if n.status != "superseded"]
        hidden, hits = len(hits) - len(kept), kept
    # A general node is the graph's top layer: it goes first, and says so.
    hits = [n for n in hits if is_general(n)] + [n for n in hits if not is_general(n)]
    cap = max(1, int(limit or INDEX_LIMIT))
    out = {
        "total": len(hits),
        "hidden": hidden,
        "truncated": len(hits) > cap,
        "nodes": [{"id": n.id, "type": n.type,
                   "verdict": "rule" if is_general(n) else VERDICT.get(n.status, n.status or "-"),
                   "tags": n.tags, "title": n.title} for n in hits[:cap]],
        "declared_tags": [str(t) for t in (load_config(root).get("tags") or [])],
    }
    if out["truncated"]:
        # A silent cap reads as "that is the whole graph": the same false negative.
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
           # Do not narrow: agents key on this shape, and a dropped key is invisible to
           # the caller until something it needed is quietly missing.
           "related_gates": [n.id for n in hits if n.type == GATE_TYPE],
           # Everything else that matched but is not a claim: sources, open work.
           "related": [n.id for n in hits if n.status not in VERDICT]}
    if out["truncated"]:
        out["note"] = (f"Showing the {QUERY_LIMIT} closest of {len(claims)} matching "
                       f"claims. List the whole graph, one line per node, and read it "
                       f"yourself for the full picture.")
    elif claims:
        out["note"] = ("Claims marked DEAD or RETRACTED have already been tested. Read "
                       "'what_would_reopen_this' before re-running them.")
    else:
        # Keyword search cannot find an idea phrased in words the node never used, and
        # saying "untested" without that caveat is how a dead experiment gets re-run.
        out["note"] = ("No keyword match. This is NOT proof the idea is untested — a "
                       "differently-worded node will not match. List the whole graph, "
                       "one line per node, and read the claims yourself before "
                       "concluding it is new.")
    return out


def get(root: Path, nid: str) -> dict:
    nodes = load(root)
    if not (n := nodes.get(nid)):
        return {"error": f"no node '{nid}'", "available": sorted(nodes)}
    out = {**summarise(n), "frontmatter": n.frontmatter,
           "links": n.links, "backlinks": n.backlinks, "body": n.body}
    if supersedes(n):
        out["covers"] = section(n.body, "Covers")
    if n.attachments:
        # A path string alone cannot say whether the file is still there.
        details = []
        for a in n.attachments:
            p = root / "attachments" / nid / a
            row = {"path": f"attachments/{nid}/{a}"}
            if p.exists():
                row["size_kb"] = round(p.stat().st_size / 1024, 1)
            else:
                row["missing"] = True
            details.append(row)
        out["attachment_files"] = details
    return out


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
    # WITH the relation on each hop: without it an agent learns two nodes are connected
    # but not HOW, which is useless for reasoning about falsification.
    return {"path": [{"node": nid, "via": rel} if rel else {"node": nid}
                     for nid, rel in p],
            "hops": len(p) - 1}


def update(root: Path, nid: str, status: str | None = None, results: dict | None = None,
          links: list | None = None, append: str | None = None,
          fields: dict | None = None) -> dict:
    """Move a node through its lifecycle, or report why it was refused — ONE shape for
    both outcomes, mirroring `commit()`'s convention."""
    try:
        now, report = update_with_report(root, nid, status=status, results=results,
                                         links=links, append=append, fields=fields)
    except GraphError as e:
        return {"status": "REJECTED", "node": nid, "reason": str(e),
                "hint": "Fix it and update again. The gate is the point."}
    out = {"status": "UPDATED", "node": nid, "node_status": now,
           "next": "git add + commit to version this."}
    if report:
        out["compressed"] = report
    return out

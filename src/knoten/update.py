"""Moving a node through its own lifecycle: open -> alive / dead / retracted / superseded.

The `superseded` arrow is the one the engine walks by itself: an update that leaves the
node superseding others flips every one of those targets in the SAME validated write, so
the general node and the specifics it retires are never on disk in disagreement.

What bounds an edit is the graph's own declared rules, not a list of things this module
refuses: the amended candidate goes through the same in-memory validation `knoten commit`
uses, and never reaches disk if it fails. `fields` therefore sets any top-level key, with
`results` the one exception — a number you already published is a different kind of claim
from a label. Git holds the before and after.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from .core import (FM_RE, GraphError, Node, backlink, graph_lock, load, node_path,
                   parse_text, question_of, shape, split, supersedes, today, write_atomic)
from .validate import check, load_config

# A key we re-emit; everything else keeps its original text, comments included.
_BLOCK = re.compile(r"^(\w[\w-]*):", re.M)


def _spans(fm: str) -> dict[str, tuple[int, int]]:
    """Line span of each top-level key. Anything not re-emitted is copied verbatim, which
    is how a hand-written comment survives an update."""
    lines = fm.splitlines()
    starts = [(i, m.group(1)) for i, l in enumerate(lines) if (m := _BLOCK.match(l))]
    out = {}
    for n, (i, key) in enumerate(starts):
        out[key] = (i, starts[n + 1][0] if n + 1 < len(starts) else len(lines))
    return out


def _dump(key: str, value) -> list[str]:
    text = yaml.safe_dump({key: value}, sort_keys=False, default_flow_style=False,
                          allow_unicode=True).rstrip("\n")
    return text.splitlines()


def _rewrite(fm: str, changed: dict) -> str:
    """Replace only the keys we changed. Order is preserved; a key that is new goes last."""
    lines, spans = fm.splitlines(), _spans(fm)
    for key, value in changed.items():
        if key in spans:
            i, j = spans[key]
            lines[i:j] = _dump(key, value)
            spans = _spans("\n".join(lines))        # spans shift under us
        else:
            lines += _dump(key, value)
    return "\n".join(lines)


def _candidate(text: str, nid: str, name: str, status, results, links, append, fields) -> str:
    """The rewritten file text for `nid`, given its current on-disk `text`. Pure: the
    caller decides what happens with the result. Raises GraphError for a request that
    changes nothing, or a `results` clash — the two failures about the request itself."""
    if not any([status, results, links, append, fields]):
        raise GraphError(f"'{nid}': nothing to change — pass status, results, links, "
                         f"fields or append.")

    fm_text, body = FM_RE.match(text).groups()
    fm, _ = split(text, name)

    changed = {"updated": today()}
    if status:
        changed["status"] = status
    if links:
        changed["links"] = (fm.get("links") or []) + list(links)
    if results:
        have = fm.get("results") or {}
        # A number that was already recorded is part of the claim. Changing it is not a
        # lifecycle move, it is a rewrite of the record — that is what retraction is for.
        if clash := [k for k, v in results.items() if k in have and have[k] != v]:
            raise GraphError(
                f"'{nid}': {', '.join(sorted(clash))} already recorded with a different "
                f"value. Retract or supersede the node rather than rewriting a result.")
        changed["results"] = {**have, **results}

    if fields:
        # Applied LAST, so `fields={"status": ...}` beats the `status` argument and
        # `fields={"updated": ...}` beats the stamp just computed: "sets any top-level
        # key" would be a lie if another argument could quietly win. What stops a broken
        # node is the graph's own rules below, not an allow-list here.
        changed.update(fields)

    out = f"---\n{_rewrite(fm_text, changed)}\n---\n{body}"
    if append:
        out = out.rstrip("\n") + "\n\n" + append.strip("\n") + "\n"
    return out


SUPERSEDED_SECTION = "## Superseded"


def _with_note(text: str, note: str) -> str:
    """The flip's note, under a heading of its own: appended bare it joined whatever
    section the body ended with, and under `## Result` it read as part of the result. A
    body that already carries the heading gets one more line inside it."""
    m = re.search(rf"^{re.escape(SUPERSEDED_SECTION)}\s*$", text, re.M)
    if not m:
        return text.rstrip("\n") + f"\n\n{SUPERSEDED_SECTION}\n{note}\n"
    nxt = re.compile(r"^##+ ", re.M).search(text, m.end())
    cut = nxt.start() if nxt else len(text)
    head, tail = text[:cut].rstrip("\n"), text[cut:]
    return f"{head}\n{note}\n" + (f"\n{tail}" if tail else "")


def superseded_texts(root: Path, nodes: dict[str, Node], node: Node) -> dict[str, str]:
    """The rewritten file text for every target `node` supersedes that exists and is not
    already superseded: `status: superseded`, with the note `superseded by <node.id> on
    <today>` under a `## Superseded` heading. Pure — reads the graph, writes nothing."""
    out = {}
    stamp = today()
    for tid in supersedes(node):
        t = nodes.get(tid)
        if t is None or t.status == "superseded":
            continue
        nf = node_path(root, tid)
        out[tid] = _with_note(
            _candidate(nf.read_text(encoding="utf-8"), tid, nf.name, status="superseded",
                       results=None, links=None, append=None, fields=None),
            f"superseded by {node.id} on {stamp}")
    return out


def superseded_candidates(root: Path, texts: dict[str, str]) -> dict[str, Node]:
    """What the graph looks like once the flip lands, so it can be validated BEFORE any
    of it is written. Parses the given `texts` rather than recomputing them, so what is
    validated is byte-for-byte what `write_atomic` puts on disk."""
    return {tid: parse_text(text, tid, node_path(root, tid).name)
            for tid, text in texts.items()}


def refused(nodes: dict[str, Node], cands: dict[str, Node], root: Path,
           cascade: bool) -> list:
    """Violations that must block writing `cands` (the candidate for the node just
    written, plus every superseded target, when this write flips any).

    Without `cascade` — a plain update that touches only its own node — the bar is the
    node's own violations, same as always: a dependant that now fails because the claim
    it rested on changed status is `validate`'s job to report, not this call's to veto.
    `knoten update --status dead` on a node others depend on must still succeed; the
    graph now has a violation, and that is what `validate` is for.

    With `cascade` — a write that flips targets — the bar widens to anything that
    appears anywhere in the graph that was not there before: a target that only fails
    once it is `superseded`, or a third node whose own rule depended on a target staying
    alive, must refuse the write here rather than land it and let `validate` discover the
    breakage after the fact. That is the whole point of flipping status INSIDE the same
    validated write as the edge that causes it.
    """
    merged = backlink({**nodes, **cands})
    after = check(merged, root)
    if not cascade:
        return [e for e in after if e.node in cands]
    before = check(backlink(nodes), root)
    return [e for e in after if e.node in cands or e not in before]


def update_with_report(root: Path, nid: str, status: str | None = None,
                       results: dict | None = None, links: list | None = None,
                       append: str | None = None,
                       fields: dict | None = None) -> tuple[str, dict | None]:
    """Append to a node, move its status, set its fields — and, when the update adds an
    `npx:supersedes` edge, flip every target to `superseded` in the same validated write.
    The whole post-write graph is validated BEFORE anything reaches disk. Raises
    GraphError, having written nothing.

    Returns (the status the node now carries, what a compression freed or None)."""
    with graph_lock(root):
        nf = node_path(root, nid)
        if not nf.exists():
            raise GraphError(f"no node '{nid}'")
        text = nf.read_text(encoding="utf-8")
        out = _candidate(text, nid, nf.name, status, results, links, append, fields)
        candidate = parse_text(out, nid, nf.name)

        nodes = load(root)
        # Read off the CANDIDATE, never off the `links` argument: `fields={"links": [...]}`
        # sets the edge just as truly.
        targets = supersedes(candidate)
        # Only an ALIVE node retires anything: re-flipping for a node being retracted
        # would re-retire the very targets the author revived in order to retract it. The
        # cascade still widens on `targets`, since that move can leave them orphaned.
        retires = targets if candidate.status == "alive" else []
        texts = superseded_texts(root, nodes, candidate) if retires else {}
        cands = {nid: candidate, **superseded_candidates(root, texts)}

        if errs := refused(nodes, cands, root, bool(targets)):
            raise GraphError("; ".join(f"{e.node}: [{e.rule}] {e.message}" for e in errs))

        write_atomic(nf, out)
        for tid, ttext in texts.items():
            write_atomic(node_path(root, tid), ttext)

        report = compression_report(root, candidate, list(texts.keys())) if retires else None
        return candidate.status, report


def update(root: Path, nid: str, status: str | None = None, results: dict | None = None,
           links: list | None = None, append: str | None = None,
           fields: dict | None = None) -> str:
    """Append to a node, move its status, set its fields. Raises GraphError, having
    written nothing.

    Returns the status the node now carries.
    """
    return update_with_report(root, nid, status, results, links, append, fields)[0]


def compression_report(root: Path, node: Node, flipped: list[str]) -> dict | None:
    """What a compression freed, in the numbers the agent can act on."""
    targets = supersedes(node)
    if not targets:
        return None
    nodes = backlink(load(root))
    faced = {l["to"] for l in node.links if l["rel"] == "kn:survivedGate"}
    most = max((len({l["to"] for l in nodes[t].links if l["rel"] == "kn:survivedGate"})
                for t in targets if t in nodes), default=0)
    s = shape(nodes, load_config(root))
    # What the TARGETS are typed, not the general node: a `principle` over `finding`
    # targets has compressed findings.
    target_types = {nodes[t].type for t in targets if t in nodes}
    return {"targets": targets, "flipped": flipped, "gates": len(faced),
            "gates_bonus": len(faced) > most, "question": question_of(nodes, node.id),
            "rules": s["rules"], "specifics": s["specifics"],
            "type": "/".join(sorted(target_types)) if target_types else "finding"}

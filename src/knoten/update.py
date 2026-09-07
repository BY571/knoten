"""Moving a node through its own lifecycle: open -> alive / dead / retracted / superseded.

The `superseded` arrow is the one the engine walks by itself: an update that leaves the
node superseding others flips every one of those targets in the SAME validated write, so
the general node and the specifics it retires are never on disk in disagreement.

`knoten commit` refuses to overwrite a node, which is right — a correction to a claim is
a new node, not an edit. But that left the lifecycle SPEC §3 draws with no way to walk it:
an agent could open a hypothesis and never close it. Its only outs were writing the file
directly, which bypasses every gate, or a second node leaving the first `open` forever.

What bounds an edit is the graph's own declared rules, not a list of things this module
refuses: the amended candidate goes through the same in-memory validation `knoten commit`
uses, and never reaches disk if it fails. `fields` therefore sets any top-level key,
including one already recorded — `results` is the exception, guarded because a number you
already published is a different kind of claim from a label. Git holds the before and
after; that is what living in git buys.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from .core import (FM_RE, GraphError, Node, backlink, counted, graph_lock, load,
                   node_path, parse_text, question_of, shape, split, supersedes, today,
                   write_atomic, _csv)
from .validate import Violation, budget_message, check, load_config

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
    """The rewritten file text for `nid`, given its current on-disk `text`. Pure — parses
    nothing, validates nothing, writes nothing; the caller decides what happens with the
    result. Raises GraphError for a request that changes nothing, or a `results` clash —
    the two failures that are about the request itself, not about the graph it lands in.
    """
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
        # No allow-list and no immutability guard: `fields` sets any top-level key to any
        # value, including one already recorded. What stops a broken node is the same
        # thing that stops one from `commit` — the whole candidate is parsed and run
        # through the graph's own rules below, and never reaches disk if it fails.
        #
        # Applied LAST, so `fields={"status": ...}` beats the `status` argument and
        # `fields={"updated": ...}` beats the stamp this call just computed. Deliberate:
        # "sets any top-level key" would be a lie if another argument could quietly win.
        changed.update(fields)

    out = f"---\n{_rewrite(fm_text, changed)}\n---\n{body}"
    if append:
        out = out.rstrip("\n") + "\n\n" + append.strip("\n") + "\n"
    return out


SUPERSEDED_SECTION = "## Superseded"


def _with_note(text: str, note: str) -> str:
    """The flip's note, under a heading of its own. Appended bare it joined whatever
    section the target's body happened to end with — under `## Result` it read as part of
    the result, which is exactly the kind of quiet rewrite of a claim this tool refuses.
    A body that already carries the heading (a node superseded, revived, superseded again)
    gets one more line inside it rather than a second heading."""
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
    """The parsed candidate for every text `superseded_texts` produced: what the graph
    looks like once the flip lands, so it can be validated BEFORE any of it is written.
    Parses the given `texts` rather than recomputing them, so what gets validated here is
    byte-for-byte what `write_atomic` puts on disk afterwards."""
    return {tid: parse_text(text, tid, node_path(root, tid).name)
            for tid, text in texts.items()}


def _over_budget(nodes: dict[str, Node], after: dict[str, Node], cands: dict[str, Node],
                 cfg: dict) -> list[Violation]:
    """Budget violations this WRITE is responsible for: a group it makes bigger and leaves
    over the cap.

    Not `validate`'s blame, which names the newest nodes past the cap. Blame is fine for
    display and wrong as a gate twice over: an author who writes `created: 2001-01-01`
    puts the blame on somebody else and walks through a full cap, and a partial
    compression that takes a group from six to four under a cap of three would be refused
    for leaving it over — while making it strictly better, which is the move the whole
    rule exists to buy.
    """
    out = []
    for r in cfg.get("rules", []):
        spec = r.get("max_alive")
        if not spec:
            continue
        types, cap, per = _csv(spec["type"]), spec["count"], spec.get("per", "question")
        was = {k: len(v) for k, v in counted(nodes, tuple(types), per).items()}
        msg = str(r.get("message", r["id"])).strip()
        for key, members in sorted(counted(after, tuple(types), per).items()):
            if len(members) <= was.get(key, 0) or len(members) <= cap:
                continue
            # Blamed on what is being written, not on whoever is newest: this refusal is
            # about the write, and the write is the only thing its author can change.
            inside = sorted(n.id for n in members if n.id in cands)
            out.append(Violation(inside[0] if inside else next(iter(cands)), r["id"],
                                 budget_message(msg, len(members), types, key, cap)))
    return out


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
    once it is `superseded` (a `graph.yaml` whose `statuses:` lacks it; a `when_status:
    superseded` rule), or a third node whose own rule depended on a target staying
    alive, must refuse the write here, not land it and let `validate` discover the
    breakage after the fact — the whole point of flipping status INSIDE the same
    validated write as the edge that causes it.

    The budget is the exception to both, and is measured on the delta instead: whose name
    a cap lands on shifts with every write, so neither "on my node" nor "new since
    before" can decide whether THIS write is the one that overspent.
    """
    cfg = load_config(root)
    caps = {r["id"] for r in cfg.get("rules", []) if "max_alive" in r}
    merged = backlink({**nodes, **cands})
    out = _over_budget(nodes, merged, cands, cfg)
    after = [e for e in check(merged, root) if e.rule not in caps]
    if not cascade:
        return out + [e for e in after if e.node in cands]
    before = [e for e in check(backlink(nodes), root) if e.rule not in caps]
    return out + [e for e in after if e.node in cands or e not in before]


def update_with_report(root: Path, nid: str, status: str | None = None,
                       results: dict | None = None, links: list | None = None,
                       append: str | None = None,
                       fields: dict | None = None) -> tuple[str, dict | None]:
    """Append to a node, move its status, set its fields — and, when the update adds an
    `npx:supersedes` edge, flip every target to `superseded` in the same validated write.
    The whole post-write graph is validated BEFORE anything reaches disk, so a target that
    cannot survive being `superseded`, or a third node the flip breaks, refuses the whole
    write rather than raising with the general node already on disk. Raises GraphError,
    having written nothing.

    Returns (the status the node now carries, what a compression freed — or None when
    this update did not add a `npx:supersedes` edge).
    """
    with graph_lock(root):
        nf = node_path(root, nid)
        if not nf.exists():
            raise GraphError(f"no node '{nid}'")
        text = nf.read_text(encoding="utf-8")
        out = _candidate(text, nid, nf.name, status, results, links, append, fields)
        candidate = parse_text(out, nid, nf.name)

        nodes = load(root)
        # Read off the CANDIDATE, never off the `links` argument: `fields={"links": [...]}`
        # sets the edge just as truly, and a supersession that flips nothing leaves the
        # general node claiming to have retired a finding the graph still counts.
        targets = supersedes(candidate)
        # Only a node that is ALIVE retires anything. A general node being retracted still
        # declares its edges, and re-flipping there would re-retire the very targets the
        # author revived in order to retract it -- `knoten update finding-g --status
        # retracted` could then never succeed. The cascade still widens on `targets`
        # though: this status move is exactly what can leave them orphaned.
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
    cfg = load_config(root)
    faced = {l["to"] for l in node.links if l["rel"] == "kn:survivedGate"}
    most = max((len({l["to"] for l in nodes[t].links if l["rel"] == "kn:survivedGate"})
                for t in targets if t in nodes), default=0)
    q = question_of(nodes, node.id)
    s = shape(nodes, cfg)
    # Matched on what the TARGETS are typed, not what the general node is typed: a
    # `principle` general node over `finding` targets must find the finding budget, not
    # come up empty looking for a (nonexistent) principle one.
    target_types = {nodes[t].type for t in targets if t in nodes}
    slot = next((b for b in s["budget"]
                if b["question"] == q and target_types & set(b["type"].split("/"))), None)
    return {"targets": targets, "flipped": flipped, "gates": len(faced),
            "gates_bonus": len(faced) > most, "question": q,
            "free": slot["free"] if slot else None, "count": slot["count"] if slot else None,
            "rules": s["rules"], "specifics": s["specifics"],
            "type": "/".join(sorted(target_types)) if target_types else "finding"}

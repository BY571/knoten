"""Moving a node through its own lifecycle: open -> alive / dead / retracted.

`knoten_commit` refuses to overwrite a node, which is right — a correction to a claim is
a new node, not an edit. But that left the lifecycle SPEC §3 draws with no way to walk it:
an agent could open a hypothesis and never close it. Its only outs were writing the file
directly, which bypasses every gate, or a second node leaving the first `open` forever.

So this is deliberately not a node editor. It appends and it moves the status, and the
candidate goes through the same in-memory validation `knoten_commit` uses, so the gate
survives the new write path. Git holds the before/after; that is what living in git buys.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from .core import (FM_RE, GraphError, backlink, graph_lock, load, node_path, parse_text,
                   split, today, write_atomic)
from .validate import check

# A key we re-emit; everything else keeps its original text, comments included.
_BLOCK = re.compile(r"^(\w[\w-]*):", re.M)

# Keys `fields` will not touch. Each already has its own way in, with its own guard:
# `status` has an argument, `results` and `links` have theirs, `id` and `type` identify
# the node, and `attachments` belong to the attach path. One key with two doors is one
# key whose guards disagree.
RESERVED = {"id", "type", "status", "results", "links", "attachments",
            "created", "updated"}


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


def update(root: Path, nid: str, status: str | None = None, results: dict | None = None,
           links: list | None = None, append: str | None = None,
           fields: dict | None = None) -> str:
    """Append to a node and/or move its status. Raises GraphError, having written nothing.

    Returns the status the node now carries.
    """
    with graph_lock(root):
        return _update(root, nid, status=status, results=results, links=links,
                       append=append, fields=fields)


def _update(root: Path, nid: str, status, results, links, append, fields) -> str:
    nf = node_path(root, nid)                  # rejects a traversal before it is a path
    if not nf.exists():
        raise GraphError(f"no node '{nid}'")
    if not any([status, results, links, append, fields]):
        raise GraphError(f"'{nid}': nothing to change — pass status, results, links, "
                         f"fields or append.")

    text = nf.read_text(encoding="utf-8")
    fm_text, body = FM_RE.match(text).groups()
    fm, _ = split(text, nf.name)

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
        if bad := RESERVED & set(fields):
            raise GraphError(
                f"'{nid}': {', '.join(sorted(bad))} cannot be set with `fields` — each has "
                f"its own argument or its own command, with its own guard.")
        # Guarded like `results`: a field already on the node is part of the published
        # claim, and replacing one in place is what retraction exists to do instead.
        # Compared with str(), the way `require_field_one_of` and `--where` compare. A
        # node committed with `seed: 2` holds an int; `--field seed=2` passes a string.
        # With `!=` that refused as "a different value", so "the same value" would have
        # meant something different here than everywhere else it is decided.
        if clash := [k for k, v in fields.items() if k in fm and str(fm[k]) != str(v)]:
            raise GraphError(
                f"'{nid}': {', '.join(sorted(clash))} already recorded with a different "
                f"value. Retract or supersede the node rather than rewriting it.")
        changed.update({k: v for k, v in fields.items() if k not in fm})

    out = f"---\n{_rewrite(fm_text, changed)}\n---\n{body}"
    if append:
        out = out.rstrip("\n") + "\n\n" + append.strip("\n") + "\n"

    # Validate the candidate in memory, exactly as knoten_commit does: an invalid node
    # never reaches the filesystem, and a refused update leaves the file untouched.
    candidate = parse_text(out, nid, nf.name)
    nodes = backlink({**load(root), nid: candidate})
    if errs := [e for e in check(nodes, root) if e.node == nid]:
        raise GraphError("; ".join(f"[{e.rule}] {e.message}" for e in errs))

    write_atomic(nf, out)
    return candidate.status

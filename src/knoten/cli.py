"""knoten — a falsification-first research graph.

A graph is a FOLDER: graph.yaml (the rules) + nodes/ (the knowledge). One graph per
research topic. Each declares its own rules; the core knows nothing about any domain.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import attachments
from .core import (ID_RE, VERDICT, GraphError, find_root, load, matches, node_path, section,
                   shortest_path)
from .hook import install as install_hook
from .validate import _csv, applies, check, load_config

MARK = {"alive": "✓ ALIVE", "dead": "✗ DEAD", "retracted": "⊘ RETRACTED"}


# ---------------------------------------------------------------- read commands

def validate(root) -> int:
    nodes = load(root)
    errs = check(nodes, root)
    print(f"{len(nodes)} nodes\n")
    if not errs:
        print("  ✓ all rules pass")
        return 0
    for v in errs:
        print(f"  ✗ {v.node}\n      [{v.rule}] {v.message}")
    print(f"\n  {len(errs)} violation(s) — commit REJECTED")
    return 1


def query(root, term) -> int:
    nodes = load(root)
    hits = [n for n in nodes.values() if matches(n, term)]
    claims = [n for n in hits if n.status in VERDICT]
    print(f'"{term}" → {len(hits)} node(s), {len(claims)} claim(s)\n')
    for n in sorted(claims, key=lambda x: x.status):
        print(f"  [{MARK[n.status]}] {n.id}")
        for rel, label in [("kn:killedByGate", "killed by"), ("kn:survivedGate", "survived ")]:
            if ts := [l["to"] for l in n.links if l["rel"] == rel]:
                print(f"      {label} : {', '.join(ts)}")
        for rel, label in [("npx:retractedBy", "RETRACTED by"),
                           ("npx:supersededBy", "superseded by")]:
            if ts := [b["to"] for b in n.backlinks if b["rel"] == rel]:
                print(f"      {label} : {', '.join(ts)}")
        if reopen := section(n.body, "What would reopen this"):
            print(f"      reopen if : {reopen[:140]}…")
        print()
    if others := [n for n in hits if n.status not in VERDICT]:
        print("  also: " + ", ".join(n.id for n in others))
    return 0


def path(root, a, b) -> int:
    p = shortest_path(load(root), a, b)
    if p is None:
        print(f"no path {a} → {b}")
        return 0
    print(f"research path {a} → {b}:\n")
    for i, (nid, rel) in enumerate(p):
        print("  " * i + (f"└─ {rel} → " if rel else "") + nid)
    return 0


def hook(root, force) -> int:
    h = install_hook(root, force=force)
    print(f"  ✓ installed {h}")
    print("    `git commit` now runs `knoten validate` and refuses a broken graph.")
    return 0


def show(root, nid) -> int:
    nodes = load(root)
    n = nodes.get(nid)
    if not n:
        raise GraphError(f"no node '{nid}'")
    print(f"{n.id}  [{MARK.get(n.status, n.status or '-')}]  type={n.type}\n")
    for l in n.links:
        print(f"  {l['rel']:22} -> {l['to']}")
    for b in n.backlinks:
        print(f"  {b['rel']:22} <- {b['to']}")
    for label, d in [("repro", n.repro), ("results", n.results)]:
        if d:
            print(f"\n  {label}:")
            for k, v in d.items():
                print(f"    {k}: {v}")
    if n.attachments:
        print("\n  attachments:")
        for a in n.attachments:
            p = root / "attachments" / nid / a
            sz = f"{p.stat().st_size / 1024:.1f} KB" if p.exists() else "MISSING"
            print(f"    attachments/{nid}/{a}  ({sz})")
    return 0


# ---------------------------------------------------------------- write commands

def attach(root, nid, files) -> int:
    res = attachments.attach(root, nid, files)
    for w in res.warnings:
        print(f"  ! {w}")
    for name in res.added:
        print(f"  + attachments/{nid}/{name}")
    if res.embedded:
        print(f"  embedded {len(res.embedded)} image(s) in the node body")
    return 0


def detach(root, nid, name) -> int:
    attachments.detach(root, nid, name)
    print(f"  - detached {name} from {nid}")
    return 0


TEMPLATE_GRAPH = """\
# {name} — a knoten research graph.
#
# The core knows NOTHING about this domain. Every rule below is declared HERE, as
# data. Write a rule only when you have a corpse: a rule without a body behind it is
# just friction.
name: {name}
description: TODO — what question is this graph about?

# Enforced. A node whose type or status is not on these lists is a typo — and a claim
# with a typo'd status silently drops out of every query. Edit them for YOUR topic.
node_types: [hypothesis, experiment, finding, method, source, retraction]
statuses:   [open, alive, dead, retracted, superseded, active]

# Reused standards: mp:supports / mp:challenges (Micropublications),
#   npx:retracts / npx:supersedes (Nanopublications),
#   prov:wasDerivedFrom / prov:used (PROV-O)
# knoten adds:  kn:survivedGate  (claim -> the method it PASSED)
#               kn:killedByGate  (claim -> the method that KILLED it)
#               kn:blockedBy     (claim -> a structural wall, not a result)

rules:
  - id: live-claims-must-cite-their-gates
    when_status: alive
    when_type: hypothesis, finding
    require_edge: kn:survivedGate
    message: An unchallenged claim is not a finding, it is a hope.

  - id: dead-claims-must-say-why
    when_status: dead, retracted
    require_sections: Why it died, What would reopen this
    message: The post-mortem IS the asset — a dead end must become a standing offer.
"""

TEMPLATE_METHOD = """\
---
id: method-example-gate
type: method
status: active
---
# Gate: <the test every claim in this graph must survive>

## The rule
<what to run>

## Why it exists
<what went wrong that made this necessary>

Replace this with a real gate. Delete it if you have none yet — but you will.
"""


def new(root, ntype, nid, status) -> int:
    """Scaffold a node carrying every section and field THIS graph's rules demand.

    Nothing here is knoten's opinion — it reads the graph's own declarations. The values
    are TODO on purpose: `knoten validate` then names the ones you still owe it, so `new`
    + `validate` is a checklist rather than a guessing game.
    """
    nf = node_path(root, nid)
    if nf.exists():
        raise GraphError(f"'{nid}' already exists. Supersede or retract it — corrections "
                         f"are nodes, not edits.")

    cfg = load_config(root)
    # `new` used to skip this while knoten_commit enforced it: the same node was accepted
    # by one entry point and rejected by the other.
    for field, declared in [("type", cfg.get("node_types")), ("status", cfg.get("statuses"))]:
        value = ntype if field == "type" else status
        if declared and value not in declared:
            raise GraphError(f"{field} '{value}' is not declared in graph.yaml "
                             f"({field}s: {', '.join(map(str, declared))})")

    sections, results = [], []
    for r in cfg.get("rules", []):
        if not applies(status, ntype, r):
            continue
        sections += _csv(r.get("require_sections"))
        results += [k for k in [r.get("require_result")] if k]
        results += list(r.get("require_result_min") or {})

    fm = [f"id: {nid}", f"type: {ntype}", f"status: {status}"]
    if results:
        fm.append("results:")
        fm += [f"  {k}: TODO" for k in dict.fromkeys(results)]

    body = ["# TODO — state the claim in one line\n"]
    body += [f"## {s}\nTODO\n" for s in dict.fromkeys(sections)]

    nf.write_text("---\n" + "\n".join(fm) + "\n---\n\n" + "\n".join(body), encoding="utf-8")
    print(f"  + nodes/{nid}.md  ({ntype}, {status})")
    if wanted := [f"## {s}" for s in dict.fromkeys(sections)] + list(dict.fromkeys(results)):
        print(f"    pre-filled what THIS graph's rules require: {', '.join(wanted)}")
    return 0


def init(name) -> int:
    if not ID_RE.match(name):
        raise GraphError(f"'{name}' is not a valid graph name (use kebab-case: my-topic)")
    root = Path.cwd() / name
    if root.exists():
        raise GraphError(f"{root} already exists")
    (root / "nodes").mkdir(parents=True)
    (root / "graph.yaml").write_text(TEMPLATE_GRAPH.format(name=name), encoding="utf-8")
    (root / "nodes" / "method-example-gate.md").write_text(TEMPLATE_METHOD, encoding="utf-8")
    print(f"created graph '{name}'\n")
    print(f"  {name}/graph.yaml   <- edit the rules for THIS topic")
    print(f"  {name}/nodes/       <- one markdown file per hypothesis / method / source\n")
    print(f"  next:  cd {name} && git init && knoten hook")
    print("         (the hook makes `git commit` refuse a graph that breaks its own rules)")
    return 0


# ---------------------------------------------------------------- entry point

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="knoten", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("init", help="start a NEW graph for a topic")
    s.add_argument("name")

    sub.add_parser("validate", help="enforce THIS graph's declared rules")

    s = sub.add_parser("new", help="scaffold a node with whatever the rules demand")
    s.add_argument("type")
    s.add_argument("id")
    s.add_argument("--status", default="open")

    s = sub.add_parser("query", help='"has this been tried?" -> verdicts + causes of death')
    s.add_argument("term")

    s = sub.add_parser("path", help="how did we get from A to B?")
    s.add_argument("a")
    s.add_argument("b")

    s = sub.add_parser("hook", help="install the git pre-commit gate")
    s.add_argument("--force", action="store_true",
                   help="overwrite a pre-commit hook knoten did not write")

    s = sub.add_parser("show", help="the node, its edges and its attachments")
    s.add_argument("node")

    s = sub.add_parser("attach", help="attach a script / plot / notebook to a node")
    s.add_argument("node")
    s.add_argument("files", nargs="+")

    s = sub.add_parser("detach", help="remove one")
    s.add_argument("node")
    s.add_argument("file")

    return p


def main(argv=None) -> int:
    args = _parser().parse_args(argv if argv is not None else sys.argv[1:])
    try:
        if args.cmd is None or args.cmd == "validate":
            return validate(find_root())
        if args.cmd == "init":
            return init(args.name)

        root = find_root()
        return {
            "query":  lambda: query(root, args.term),
            "path":   lambda: path(root, args.a, args.b),
            "new":    lambda: new(root, args.type, args.id, args.status),
            "show":   lambda: show(root, args.node),
            "hook":   lambda: hook(root, args.force),
            "attach": lambda: attach(root, args.node, args.files),
            "detach": lambda: detach(root, args.node, args.file),
        }[args.cmd]()
    except GraphError as e:
        print(f"knoten: {e}", file=sys.stderr)
        return 1


def cli() -> None:
    sys.exit(main())


if __name__ == "__main__":
    cli()

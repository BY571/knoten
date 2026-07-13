"""knoten — a falsification-first research graph.

A graph is a FOLDER: graph.yaml (the rules) + nodes/ (the knowledge). One graph per
research topic. Each declares its own rules; the core knows nothing about any domain.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import defaultdict, deque
from pathlib import Path

import yaml

from .core import FM_RE, ID_RE, GraphError, _Loader, load, read_frontmatter, split
from .validate import check

MARK = {"alive": "✓ ALIVE", "dead": "✗ DEAD", "retracted": "⊘ RETRACTED"}
IMG = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
BIG = 1_000_000          # git is for code and plots, not datasets


def _root() -> Path:
    p = Path.cwd()
    for c in [p, *p.parents]:
        if (c / "graph.yaml").exists():
            return c
    raise GraphError("no graph.yaml found (run `knoten init` or cd into a graph)")


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
    t = term.lower()
    hits = [n for n in nodes.values()
            if t in n.id.lower() or t in n.body.lower()
            or t in str(n.frontmatter.get("tags", "")).lower()]
    claims = [n for n in hits if n.status in MARK]
    print(f'"{term}" → {len(hits)} node(s), {len(claims)} claim(s)\n')
    for n in sorted(claims, key=lambda x: x.status):
        print(f"  [{MARK[n.status]}] {n.id}")
        for rel, label in [("kn:killedByGate", "killed by"), ("kn:survivedGate", "survived ")]:
            if ts := [l["to"] for l in n.links if l["rel"] == rel]:
                print(f"      {label} : {', '.join(ts)}")
        if m := re.search(r"^## What would reopen this\n+(.+?)(?=\n##|\Z)", n.body, re.S | re.M):
            print(f"      reopen if : {' '.join(m.group(1).split())[:140]}…")
        print()
    if others := [n for n in hits if n.status not in MARK]:
        print("  also: " + ", ".join(n.id for n in others))
    return 0


def path(root, a, b) -> int:
    nodes = load(root)
    for nid in (a, b):
        if nid not in nodes:
            raise GraphError(f"no node '{nid}'")

    adj = defaultdict(list)
    for nid, n in nodes.items():
        for l in n.links:
            adj[nid].append((l["to"], l["rel"]))
            adj[l["to"]].append((nid, "←" + l["rel"]))

    q, seen = deque([(a, [(a, "")])]), {a}
    while q:
        cur, p = q.popleft()
        if cur == b:
            print(f"research path {a} → {b}:\n")
            for i, (nid, rel) in enumerate(p):
                print("  " * i + (f"└─ {rel} → " if rel else "") + nid)
            return 0
        for nxt, rel in adj[cur]:
            if nxt not in seen:
                seen.add(nxt)
                q.append((nxt, p + [(nxt, rel)]))
    print(f"no path {a} → {b}")
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


def build(root) -> int:
    nodes = load(root)
    out = root / ".knoten"
    out.mkdir(exist_ok=True)
    (out / "graph.json").write_text(
        json.dumps({k: v.to_dict() for k, v in nodes.items()}, indent=2, default=str),
        encoding="utf-8")
    print(f"wrote .knoten/graph.json ({len(nodes)} nodes)")
    return 0


# ---------------------------------------------------------------- write commands

def _scalar(name: str) -> str:
    """Emit a filename as YAML, quoting it unless it round-trips as the identical string.

    Filenames were written raw. `plot #1.png` became the YAML comment `plot`; `run: final
    .png` became a dict; `123` became an int. All are legal filenames.
    """
    try:
        if yaml.load(name, Loader=_Loader) == name:
            return name
    except yaml.YAMLError:
        pass
    return json.dumps(name, ensure_ascii=False)     # a JSON string is valid YAML


def _set_attachments(node_file: Path, names: list[str]) -> None:
    """Rewrite ONLY the `attachments:` block, line by line.

    We do not re-dump the frontmatter through yaml — that would reformat it and strip the
    comments a human wrote.

    The list items we must drop may be indented OR NOT: `yaml.dump` emits them at zero
    indent by default. Requiring leading whitespace orphaned them, wedged them into the
    preceding block, and left the node unparseable — which takes the whole graph down
    with it, since load() raises rather than skips.
    """
    text = node_file.read_text(encoding="utf-8")
    read_frontmatter(node_file)          # refuse to touch a node we cannot parse
    m = FM_RE.match(text)

    kept, dropping = [], False
    for line in m.group(1).splitlines():
        if re.match(r"^attachments:\s*(\[.*\])?\s*$", line):
            dropping = True
            continue
        if dropping:
            if re.match(r"^\s*-\s", line):        # indented or not
                continue
            dropping = False
        kept.append(line)

    if names:
        kept.append("attachments:")
        kept += [f"  - {_scalar(n)}" for n in sorted(names)]
    fm = "\n".join(kept).strip("\n")
    out = f"---\n{fm}\n---\n{m.group(2)}"

    split(out, node_file.name)           # never write a node we cannot read back
    node_file.write_text(out, encoding="utf-8")


def _embed(node_file: Path, nid: str, images: list[str]) -> int:
    """Images render on GitHub only if they are in the body, not just the frontmatter."""
    text = node_file.read_text(encoding="utf-8")
    added = 0
    for name in images:
        ref = f"![{name}](../attachments/{nid}/{name})"
        if ref in text:
            continue
        if "## Attachments" not in text:
            text = text.rstrip() + "\n\n## Attachments\n"
        text = text.rstrip() + f"\n\n{ref}\n"
        added += 1
    if added:
        node_file.write_text(text, encoding="utf-8")
    return added


def _drop_empty_attachments_section(node_file: Path) -> None:
    text = node_file.read_text(encoding="utf-8")
    new = re.sub(r"\n*## Attachments\s*\n+(?=(##|\Z))", "\n", text)
    if new != text:
        node_file.write_text(new.rstrip() + "\n", encoding="utf-8")


def _preflight(files) -> list[Path]:
    """Vet every file BEFORE copying any, so a bad path halfway through the list cannot
    leave the node half-attached. `exists()` is true for a directory, so check is_file."""
    srcs = [Path(f) for f in files]
    for src in srcs:
        if not src.exists():
            raise GraphError(f"no such file: {src}")
        if not src.is_file():
            raise GraphError(f"{src} is not a file")
    names = [s.name for s in srcs]
    if dupes := {n for n in names if names.count(n) > 1}:
        raise GraphError(
            f"two files share the basename {', '.join(sorted(dupes))} — they would land on "
            f"top of each other in attachments/. Rename one.")
    return srcs


def attach(root, nid, files) -> int:
    nf = root / "nodes" / f"{nid}.md"
    if not nf.exists():
        raise GraphError(f"no node '{nid}'")
    fm, _ = read_frontmatter(nf)
    have = [str(a) for a in (fm.get("attachments") or [])]
    srcs = _preflight(files)

    dest = root / "attachments" / nid
    dest.mkdir(parents=True, exist_ok=True)
    images = []
    for src in srcs:
        if (size := src.stat().st_size) > BIG:
            print(f"  ! {src.name} is {size / 1e6:.1f} MB — git is for code and plots, "
                  f"not datasets. Consider linking to it instead.")
        shutil.copy2(src, dest / src.name)
        if src.name not in have:
            have.append(src.name)
        print(f"  + attachments/{nid}/{src.name}")
        if src.suffix.lower() in IMG:
            images.append(src.name)

    _set_attachments(nf, have)
    if n := _embed(nf, nid, images):
        print(f"  embedded {n} image(s) in the node body")
    return 0


def detach(root, nid, name) -> int:
    nf = root / "nodes" / f"{nid}.md"
    if not nf.exists():
        raise GraphError(f"no node '{nid}'")
    fm, _ = read_frontmatter(nf)
    have = [str(a) for a in (fm.get("attachments") or [])]
    if name not in have:
        raise GraphError(f"'{name}' is not attached to {nid}")

    (root / "attachments" / nid / name).unlink(missing_ok=True)
    _set_attachments(nf, [a for a in have if a != name])

    text = nf.read_text(encoding="utf-8")
    nf.write_text(re.sub(rf"\n*!\[{re.escape(name)}\]\([^)]*\)\n*", "\n", text), encoding="utf-8")
    _drop_empty_attachments_section(nf)
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

node_types: [hypothesis, experiment, finding, method, source, retraction]

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
    require_sections: Why it died, reopen
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
    print(f"  next:  cd {name} && git init && knoten validate")
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

    s = sub.add_parser("query", help='"has this been tried?" -> verdicts + causes of death')
    s.add_argument("term")

    s = sub.add_parser("path", help="how did we get from A to B?")
    s.add_argument("a")
    s.add_argument("b")

    s = sub.add_parser("show", help="the node, its edges and its attachments")
    s.add_argument("node")

    s = sub.add_parser("attach", help="attach a script / plot / notebook to a node")
    s.add_argument("node")
    s.add_argument("files", nargs="+")

    s = sub.add_parser("detach", help="remove one")
    s.add_argument("node")
    s.add_argument("file")

    sub.add_parser("build", help="emit .knoten/graph.json for agents")
    return p


def main(argv=None) -> int:
    args = _parser().parse_args(argv if argv is not None else sys.argv[1:])
    try:
        if args.cmd is None or args.cmd == "validate":
            return validate(_root())
        if args.cmd == "init":
            return init(args.name)

        root = _root()
        return {
            "query":  lambda: query(root, args.term),
            "path":   lambda: path(root, args.a, args.b),
            "show":   lambda: show(root, args.node),
            "attach": lambda: attach(root, args.node, args.files),
            "detach": lambda: detach(root, args.node, args.file),
            "build":  lambda: build(root),
        }[args.cmd]()
    except GraphError as e:
        print(f"knoten: {e}", file=sys.stderr)
        return 1


def cli() -> None:
    sys.exit(main())


if __name__ == "__main__":
    cli()

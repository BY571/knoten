"""knoten — a falsification-first research graph.

    knoten init <name>            start a NEW graph for a topic
    knoten validate               enforce THIS graph's declared rules
    knoten query <term>           "has this been tried?" -> verdicts + causes of death
    knoten path A B               how did we get from A to B?
    knoten attach <node> <file>   attach a script / plot / notebook to a node
    knoten detach <node> <file>   remove one
    knoten show <node>            the node, its edges and its attachments
    knoten build                  emit .knoten/graph.json for agents

A graph is a FOLDER: graph.yaml (the rules) + nodes/ (the knowledge). One graph per
research topic. Each declares its own rules; the core knows nothing about any domain.
"""
from __future__ import annotations
import json, re, sys
from collections import defaultdict, deque
import shutil
from pathlib import Path
from .core import load
from .validate import check

MARK = {"alive": "✓ ALIVE", "dead": "✗ DEAD", "retracted": "⊘ RETRACTED"}


def _root() -> Path:
    p = Path.cwd()
    for c in [p, *p.parents]:
        if (c / "graph.yaml").exists():
            return c
    sys.exit("no graph.yaml found (run `knoten init` or cd into a graph)")


def validate(root):
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


def query(root, term):
    nodes = load(root)
    t = term.lower()
    hits = [n for n in nodes.values() if t in n.id.lower() or t in n.body.lower()
            or t in str(n.frontmatter.get("tags", "")).lower()]
    claims = [n for n in hits if n.status in ("alive", "dead", "retracted")]
    print(f'"{term}" → {len(hits)} node(s), {len(claims)} claim(s)\n')
    for n in sorted(claims, key=lambda x: x.status):
        print(f"  [{MARK.get(n.status, n.status)}] {n.id}")
        for rel, label in [("kn:killedByGate", "killed by"), ("kn:survivedGate", "survived ")]:
            ts = [l["to"] for l in n.links if l["rel"] == rel]
            if ts:
                print(f"      {label} : {', '.join(ts)}")
        if m := re.search(r"^## What would reopen this\n+(.+?)(?=\n##|\Z)", n.body, re.S | re.M):
            print(f"      reopen if : {' '.join(m.group(1).split())[:140]}…")
        print()
    if others := [n for n in hits if n not in claims]:
        print("  also: " + ", ".join(n.id for n in others))


def path(root, a, b):
    nodes = load(root)
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
            return
        for nxt, rel in adj[cur]:
            if nxt not in seen:
                seen.add(nxt); q.append((nxt, p + [(nxt, rel)]))
    print(f"no path {a} → {b}")


IMG = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
BIG = 1_000_000          # git is for code and plots, not datasets


def _set_attachments(node_file: Path, names: list[str]) -> None:
    """Rewrite the `attachments:` block in the frontmatter (add or remove)."""
    text = node_file.read_text()
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    fm, body = m.group(1), m.group(2)
    fm = re.sub(r"\nattachments:\n(  - .*\n)*", "\n", "\n" + fm).lstrip("\n")
    if names:
        fm += "\nattachments:\n" + "".join(f"  - {n}\n" for n in sorted(names))
        fm = fm.rstrip("\n")
    node_file.write_text(f"---\n{fm}\n---\n{body}")


def attach(root, nid, files):
    nf = root / "nodes" / f"{nid}.md"
    if not nf.exists():
        sys.exit(f"no node '{nid}'")
    dest = root / "attachments" / nid
    dest.mkdir(parents=True, exist_ok=True)
    nodes = load(root)
    have = list(nodes[nid].attachments)
    body_add = []
    for f in files:
        src = Path(f)
        if not src.exists():
            sys.exit(f"no such file: {f}")
        size = src.stat().st_size
        if size > BIG:
            print(f"  ! {src.name} is {size/1e6:.1f} MB — git is for code and plots, "
                  f"not datasets. Consider linking to it instead.")
        shutil.copy2(src, dest / src.name)
        if src.name not in have:
            have.append(src.name)
        print(f"  + attachments/{nid}/{src.name}")
        if src.suffix.lower() in IMG:
            body_add.append(src.name)
    _set_attachments(nf, have)

    # images: embed them so they RENDER in the node on GitHub
    if body_add:
        text = nf.read_text()
        if "## Attachments" not in text:
            text = text.rstrip() + "\n\n## Attachments\n"
        for name in body_add:
            ref = f"![{name}](../attachments/{nid}/{name})"
            if ref not in text:
                text = text.rstrip() + f"\n\n{ref}\n"
        nf.write_text(text)
        print(f"  embedded {len(body_add)} image(s) in the node body")


def detach(root, nid, name):
    nf = root / "nodes" / f"{nid}.md"
    nodes = load(root)
    if nid not in nodes:
        sys.exit(f"no node '{nid}'")
    have = [a for a in nodes[nid].attachments if a != name]
    if len(have) == len(nodes[nid].attachments):
        sys.exit(f"'{name}' is not attached to {nid}")
    f = root / "attachments" / nid / name
    if f.exists():
        f.unlink()
    _set_attachments(nf, have)
    # drop any embedded image reference
    t = nf.read_text()
    t = re.sub(rf"\n*!\[{re.escape(name)}\]\([^)]*\)\n*", "\n", t)
    nf.write_text(t)
    print(f"  - detached {name} from {nid}")


def show(root, nid):
    nodes = load(root)
    n = nodes.get(nid)
    if not n:
        sys.exit(f"no node '{nid}'")
    print(f"{n.id}  [{MARK.get(n.status, n.status or '-')}]  type={n.type}\n")
    for l in n.links:
        print(f"  {l['rel']:22} -> {l['to']}")
    for b in n.backlinks:
        print(f"  {b['rel']:22} <- {b['to']}")
    if n.results:
        print("\n  results:")
        for k, v in n.results.items():
            print(f"    {k}: {v}")
    if n.attachments:
        print("\n  attachments:")
        for a in n.attachments:
            p = root / "attachments" / nid / a
            sz = f"{p.stat().st_size/1024:.1f} KB" if p.exists() else "MISSING"
            print(f"    attachments/{nid}/{a}  ({sz})")


def build(root):
    nodes = load(root)
    out = root / ".knoten"; out.mkdir(exist_ok=True)
    (out / "graph.json").write_text(json.dumps(
        {k: v.to_dict() for k, v in nodes.items()}, indent=2))
    print(f"wrote .knoten/graph.json ({len(nodes)} nodes)")


TEMPLATE_GRAPH = """# {name} — a knoten research graph.
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
    message: The post-mortem IS the asset - a dead end must become a standing offer.
"""

TEMPLATE_METHOD = """---
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


def init(name):
    root = Path.cwd() / name
    if root.exists():
        sys.exit(f"{root} already exists")
    (root / "nodes").mkdir(parents=True)
    (root / "graph.yaml").write_text(TEMPLATE_GRAPH.format(name=name))
    (root / "nodes" / "method-example-gate.md").write_text(TEMPLATE_METHOD)
    print(f"created graph '{name}'\n")
    print(f"  {name}/graph.yaml   <- edit the rules for THIS topic")
    print(f"  {name}/nodes/       <- one markdown file per hypothesis / method / source\n")
    print("  next:  cd", name, "&& git init && knoten validate")


def main(argv=None):
    a = (argv or sys.argv[1:]) or ["validate"]
    cmd = a[0]
    if cmd == "init":
        return init(a[1] if len(a) > 1 else "research-graph")
    root = _root()
    if cmd == "validate": sys.exit(validate(root))
    elif cmd == "query":  query(root, a[1])
    elif cmd == "path":   path(root, a[1], a[2])
    elif cmd == "attach": attach(root, a[1], a[2:])
    elif cmd == "detach": detach(root, a[1], a[2])
    elif cmd == "show":   show(root, a[1])
    elif cmd == "build":  build(root)
    else: print(__doc__)


if __name__ == "__main__":
    main()

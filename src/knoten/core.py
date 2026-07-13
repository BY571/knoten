"""Core: parse markdown nodes, build the graph, generate back-links.

Deliberately dependency-light. A node is a markdown file; an edge is a typed link in
its frontmatter. Git does versioning, branching, PRs and hosting — we do not.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# Edges are declared ONCE, on the subject. Back-links are generated, never authored.
INVERSE = {
    "kn:survivedGate":     "kn:gateSurvivedBy",
    "kn:killedByGate":     "kn:gateKilled",
    "kn:blockedBy":        "kn:blocks",
    "kn:tests":            "kn:testedBy",
    "mp:supports":         "mp:supportedBy",
    "mp:challenges":       "mp:challengedBy",
    "npx:retracts":        "npx:retractedBy",
    "npx:supersedes":      "npx:supersededBy",
    "prov:wasDerivedFrom": "prov:hadDerivation",
    "prov:used":           "prov:wasUsedBy",
}

FM_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)
LINK_RE = re.compile(r"\s*-\s*\{rel:\s*([\w:]+)\s*,\s*to:\s*([\w:.-]+)")
ITEM_RE = re.compile(r"^\s{2}-\s+([^\s{].*)$")
KV_RE = re.compile(r"^(\w+):\s*(.*)$")
NESTED_RE = re.compile(r"^\s{2}(\w+):\s*(\S.*)$")


@dataclass
class Node:
    id: str
    path: str
    body: str
    frontmatter: dict = field(default_factory=dict)
    links: list = field(default_factory=list)
    results: dict = field(default_factory=dict)
    sections: list = field(default_factory=list)
    backlinks: list = field(default_factory=list)
    attachments: list = field(default_factory=list)

    @property
    def status(self) -> str:
        return str(self.frontmatter.get("status", ""))

    @property
    def type(self) -> str:
        return str(self.frontmatter.get("type", ""))

    def rels(self) -> set:
        return {l["rel"] for l in self.links}

    def to_dict(self) -> dict:
        return {"id": self.id, "path": self.path, **self.frontmatter,
                "links": self.links, "backlinks": self.backlinks,
                "results": self.results, "sections": self.sections,
                "attachments": self.attachments}


def parse(path: Path, root: Path) -> Node | None:
    m = FM_RE.match(path.read_text())
    if not m:
        return None
    fm_text, body = m.group(1), m.group(2)
    node = Node(id=path.stem, path=str(path.relative_to(root)), body=body,
                sections=re.findall(r"^##+ (.+)$", body, re.M))
    in_attach = False
    for line in fm_text.splitlines():
        if re.match(r"^attachments:\s*$", line):
            in_attach = True
            continue
        if in_attach:
            if im := ITEM_RE.match(line):
                node.attachments.append(im.group(1).strip().strip("\"'"))
                continue
            if re.match(r"^\w", line):
                in_attach = False
        if lm := LINK_RE.match(line):
            node.links.append({"rel": lm.group(1), "to": lm.group(2)})
            continue
        if km := KV_RE.match(line):
            v = km.group(2).strip()
            if v and not v.startswith("#"):
                node.frontmatter[km.group(1)] = v.strip('"').strip("[]")
            continue
        if nm := NESTED_RE.match(line):
            node.results[nm.group(1)] = nm.group(2).strip()
    return node


def load(root: Path) -> dict[str, Node]:
    nodes = {}
    for p in sorted((root / "nodes").glob("*.md")):
        if p.stem.upper() == "README":
            continue
        if n := parse(p, root):
            nodes[n.id] = n
    return _backlink(nodes)


def _backlink(nodes: dict[str, Node]) -> dict[str, Node]:
    back = defaultdict(list)
    for nid, n in nodes.items():
        for l in n.links:
            if inv := INVERSE.get(l["rel"]):
                back[l["to"]].append({"rel": inv, "to": nid})
    for nid, n in nodes.items():
        n.backlinks = back.get(nid, [])
    return nodes

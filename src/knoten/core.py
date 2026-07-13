"""Core: parse markdown nodes, build the graph, generate back-links.

A node is a markdown file; an edge is a typed link in its frontmatter. Git does
versioning, branching, PRs and hosting — we do not.

The parser's first duty is to REFUSE. A node it cannot read must raise, never be
skipped: a node that silently vanishes from the graph is worse than one that errors,
because the graph still reports itself healthy.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class GraphError(Exception):
    """The graph on disk is malformed. Always name the file."""


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
GENERATED = set(INVERSE.values())

FM_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.S)

# A node id becomes a filename. Anything else is a path traversal waiting to happen —
# and `knoten_commit` takes this straight from an LLM.
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class _Loader(yaml.SafeLoader):
    """YAML 1.2 booleans, not 1.1.

    PyYAML implements YAML 1.1, where bare `no`, `off` and `yes` resolve to booleans —
    the "Norway problem". In a research graph that silently turns a tag named `no` into
    `False`. Strip the 1.1 bool resolver and keep only true/false.
    """


_Loader.yaml_implicit_resolvers = {
    ch: [(tag, rx) for tag, rx in rs if tag != "tag:yaml.org,2002:bool"]
    for ch, rs in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Loader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


@dataclass
class Node:
    id: str
    path: str
    body: str
    frontmatter: dict = field(default_factory=dict)
    links: list = field(default_factory=list)
    results: dict = field(default_factory=dict)
    repro: dict = field(default_factory=dict)
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
                "results": self.results, "repro": self.repro,
                "sections": self.sections, "attachments": self.attachments}


def _yaml(text: str, label: str) -> dict:
    try:
        fm = yaml.load(text, Loader=_Loader)
    except yaml.YAMLError as e:
        where = f" line {e.problem_mark.line + 1}:" if getattr(e, "problem_mark", None) else ""
        raise GraphError(f"{label}:{where} invalid YAML — {getattr(e, 'problem', e)}") from e
    if fm is None:
        return {}
    if not isinstance(fm, dict):
        raise GraphError(f"{label}: frontmatter must be a mapping, got {type(fm).__name__}")
    return fm


def split(text: str, label: str) -> tuple[dict, str]:
    """(frontmatter, body). Raises GraphError — never returns a half-read node."""
    m = FM_RE.match(text)
    if not m:
        raise GraphError(f"{label}: no YAML frontmatter (expected a leading `---` block)")
    return _yaml(m.group(1), label), m.group(2)


def read_frontmatter(path: Path) -> tuple[dict, str]:
    return split(path.read_text(encoding="utf-8"), path.name)


def parse_text(text: str, nid: str, label: str | None = None) -> Node:
    """Build a Node from a string. Used by `knoten_commit` to validate a candidate
    node in memory, so an invalid node never reaches the filesystem at all."""
    label = label or f"{nid}.md"
    fm, body = split(text, label)

    links = []
    for l in fm.get("links") or []:
        if not isinstance(l, dict) or "rel" not in l or "to" not in l:
            raise GraphError(f"{label}: every link needs `rel` and `to`, got {l!r}")
        links.append({**l, "rel": str(l["rel"]), "to": str(l["to"])})

    return Node(
        id=nid,
        path=f"nodes/{nid}.md",
        body=body,
        frontmatter=fm,
        links=links,
        results=fm.get("results") or {},
        repro=fm.get("repro") or {},
        attachments=[str(a) for a in (fm.get("attachments") or [])],
        sections=re.findall(r"^##+ (.+)$", body, re.M),
    )


def parse(path: Path, root: Path) -> Node:
    n = parse_text(path.read_text(encoding="utf-8"), path.stem, path.name)
    n.path = str(path.relative_to(root))
    return n


def load(root: Path) -> dict[str, Node]:
    nodes = {}
    for p in sorted((root / "nodes").glob("*.md")):
        if p.stem.upper() == "README":
            continue
        n = parse(p, root)
        nodes[n.id] = n
    return backlink(nodes)


def backlink(nodes: dict[str, Node]) -> dict[str, Node]:
    back = defaultdict(list)
    for nid, n in nodes.items():
        for l in n.links:
            if inv := INVERSE.get(l["rel"]):
                back[l["to"]].append({"rel": inv, "to": nid})
    for nid, n in nodes.items():
        n.backlinks = back.get(nid, [])
    return nodes

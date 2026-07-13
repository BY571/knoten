"""Parse markdown nodes, build the graph, generate back-links.

A node is a markdown file; an edge is a typed link in its frontmatter. Git does
versioning, branching, PRs and hosting — we do not.

The parser's first duty is to REFUSE: a node it cannot read raises, never gets skipped.
A node that silently vanishes leaves the graph reporting itself healthy.
"""
from __future__ import annotations

import re
from collections import defaultdict, deque
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

# The claim lifecycle. A node with any other status is not a claim (a method, a source).
VERDICT = {"alive": "ALIVE", "dead": "DEAD", "retracted": "RETRACTED"}

FM_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.S)

# A node id becomes a filename. Anything else is a path traversal waiting to happen —
# and `knoten_commit` takes this straight from an LLM.
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class _Loader(yaml.SafeLoader):
    """YAML 1.2 scalars, not YAML 1.1.

    PyYAML is YAML 1.1, whose implicit typing silently rewrites `results:` — the block
    holding the numbers this tool exists to protect:

        tags: [no, off]     -> [False, False]   the "Norway problem"
        wallclock: 12:30    -> 750              sexagesimal
        seed: 042           -> 34               octal (but `08` stays a string!)
        n: 1_000            -> 1000             digit separators
        run: 2024-01-15     -> datetime.date    implicit timestamps

    Rather than patch these one at a time, swap the implicit resolvers for the 1.2 core
    schema, which has none of them. Anything we cannot confidently type stays a string —
    the safe direction, since a string fails a numeric rule loudly while a silently
    rewritten number does not.
    """


# YAML 1.2 core schema (https://yaml.org/spec/1.2.2/#103-core-schema).
_CORE = [
    ("tag:yaml.org,2002:bool",  r"^(?:true|True|TRUE|false|False|FALSE)$", list("tTfF")),
    ("tag:yaml.org,2002:int",   r"^[-+]?(?:0|[1-9][0-9]*)$"
                                r"|^0o[0-7]+$|^0x[0-9a-fA-F]+$", list("-+0123456789")),
    # A float needs a `.` or an exponent. Without that guard a bare `042` — which the int
    # resolver correctly refuses — falls through to float and becomes 42.0.
    ("tag:yaml.org,2002:float", r"^[-+]?(?:[0-9]*\.[0-9]+|[0-9]+\.[0-9]*)(?:[eE][-+]?[0-9]+)?$"
                                r"|^[-+]?[0-9]+[eE][-+]?[0-9]+$"
                                r"|^[-+]?\.(?:inf|Inf|INF)$|^\.(?:nan|NaN|NAN)$",
                                list("-+0123456789.")),
    # "" is the first-char key PyYAML uses for an empty scalar (`status:` with no value).
    ("tag:yaml.org,2002:null",  r"^(?:~|null|Null|NULL|)$", ["~", "n", "N", ""]),
]
_DROP = {t for t, _, _ in _CORE} | {"tag:yaml.org,2002:timestamp"}

_Loader.yaml_implicit_resolvers = {
    ch: [(tag, rx) for tag, rx in rs if tag not in _DROP]
    for ch, rs in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
for _tag, _pattern, _first in _CORE:
    _Loader.add_implicit_resolver(_tag, re.compile(_pattern), _first)


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


# ---------------------------------------------------------------------------------
# Shared by BOTH surfaces. These lived twice — once in cli.py, once in mcp_server.py —
# and drifted: the CLI's path printed relation labels while the MCP's did not, and a
# search fix landed in one copy and not the other.

def find_root(start: Path | None = None) -> Path:
    """The graph containing `start` (default: cwd). A graph is the folder with graph.yaml."""
    p = start or Path.cwd()
    for c in [p, *p.parents]:
        if (c / "graph.yaml").exists():
            return c
    raise GraphError("no graph.yaml found (run `knoten init` or cd into a graph)")


def section(body: str, title: str) -> str | None:
    """The prose under a `## <title>` heading, whitespace-collapsed."""
    m = re.search(rf"^##+ {re.escape(title)}\s*\n+(.+?)(?=\n##|\Z)", body, re.S | re.M)
    return " ".join(m.group(1).split()) if m else None


def _tokens(s: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", s.lower()) if t]


def matches(n: Node, term: str) -> bool:
    """Does this node answer "has this been tried?" for `term`?

    Every token must appear. A substring match meant `query "self consistency"` found
    NOTHING, because the node is `self-consistency` — the headline question failing on a
    space, and answering "untested" for work that is already dead.
    """
    hay = " ".join(_tokens(f"{n.id} {n.body} {n.frontmatter.get('tags', '')}"))
    return all(t in hay for t in _tokens(term))


def shortest_path(nodes: dict[str, Node], a: str, b: str) -> list[tuple[str, str]] | None:
    """BFS over the graph read as undirected — "how did we get from A to B?" doesn't care
    which way an edge points. Returns [(node_id, relation_taken_to_reach_it), ...], the
    first with an empty relation. `←rel` means the edge was traversed backwards."""
    for nid in (a, b):
        if nid not in nodes:
            raise GraphError(f"no node '{nid}'")

    adj = defaultdict(list)
    for nid, n in nodes.items():
        for l in n.links:
            adj[nid].append((l["to"], l["rel"]))
            adj[l["to"]].append((nid, "←" + l["rel"]))

    q, seen = deque([[(a, "")]]), {a}
    while q:
        p = q.popleft()
        if p[-1][0] == b:
            return p
        for nxt, rel in adj[p[-1][0]]:
            if nxt not in seen:
                seen.add(nxt)
                q.append(p + [(nxt, rel)])
    return None

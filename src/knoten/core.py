"""Parse markdown nodes, build the graph, generate back-links.

A node is a markdown file; an edge is a typed link in its frontmatter. Git does
versioning, branching, PRs and hosting — we do not.

The parser's first duty is to REFUSE: a node it cannot read raises, never gets skipped.
A node that silently vanishes leaves the graph reporting itself healthy.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict, deque
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

# An id becomes a filename, so anything else is a path traversal. Go through node_path()
# for EVERY id -> file conversion: `knoten detach ../../x f` used to delete a file outside
# the graph, because only the MCP surface (where the id comes from an LLM) was guarded.
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def node_path(root: Path, nid: str) -> Path:
    if not ID_RE.match(nid or ""):
        raise GraphError(f"'{nid}' is not a valid node id (kebab-case: hyp-my-idea)")
    return root / "nodes" / f"{nid}.md"


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
    body: str
    frontmatter: dict = field(default_factory=dict)
    links: list = field(default_factory=list)
    results: dict = field(default_factory=dict)
    repro: dict = field(default_factory=dict)
    sections: list = field(default_factory=list)
    backlinks: list = field(default_factory=list)
    attachments: list = field(default_factory=list)
    tokens: tuple | None = field(default=None, repr=False, compare=False)

    @property
    def status(self) -> str:
        return str(self.frontmatter.get("status", ""))

    @property
    def type(self) -> str:
        return str(self.frontmatter.get("type", ""))

    @property
    def title(self) -> str:
        """The H1 — the claim itself, in one line. This is what makes an index row
        judgeable: an id alone cannot be compared against a new idea."""
        return _title(self.body)

    @property
    def tags(self) -> list:
        """A bare `tags: decoding` is legal YAML that iterates as characters. `validate`
        reports it as malformed-tags; here it reads as untagged rather than as five
        one-letter tags."""
        raw = self.frontmatter.get("tags")
        return [str(x) for x in raw] if isinstance(raw, list) else []

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
        body=body,
        frontmatter=fm,
        links=links,
        results=fm.get("results") or {},
        repro=fm.get("repro") or {},
        attachments=[str(a) for a in (fm.get("attachments") or [])],
        sections=re.findall(r"^##+ (.+)$", body, re.M),
    )


def load(root: Path) -> dict[str, Node]:
    nodes = {}
    for p in sorted((root / "nodes").glob("*.md")):
        if p.stem.upper() == "README":
            continue
        n = parse_text(p.read_text(encoding="utf-8"), p.stem, p.name)
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


# Function words an agent's question carries and a node never means. Domain words are
# NEVER listed here — a corpus-common word like "accuracy" is handled by idf below,
# which is adaptive; a hardcoded one would be a permanent blind spot.
_STOP = {
    "a", "about", "again", "all", "an", "and", "any", "anybody", "anyone", "anything",
    "are", "as", "at", "be", "been", "before", "being", "but", "by", "did", "do", "does",
    "done", "ever", "for", "from", "had", "has", "have", "how", "i", "if", "in", "into",
    "is", "it", "its", "of", "on", "or", "over", "should", "so", "some", "someone",
    "something", "than", "that", "the", "then", "these", "this", "those", "to", "was",
    "we", "were", "what", "when", "where", "which", "who", "whom", "whose", "why",
    "will", "with", "would", "you",
}

# knoten's own schema words. Their VALUES are searchable; the key names are not, since
# they appear in every node and would make `query "status"` return the whole graph.
_STRUCTURAL = {"id", "type", "status", "tags", "links", "rel", "to", "note",
               "results", "repro", "attachments"}

# id and title carry the claim; tags are a curated label; everything else is context.
_STRONG, _MEDIUM, _WEAK = 3.0, 2.0, 1.0


def _flatten(obj, out: list) -> list:
    """Every scalar in the frontmatter, plus the keys a human chose (`tokens_per_question`,
    `acc_greedy`). The haystack was id + body + tags, so `repro.model: Qwen3-8B` was
    unsearchable and two nodes that ran on the same benchmark answered as one."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k) not in _STRUCTURAL:
                out.append(str(k))
            _flatten(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _flatten(v, out)
    elif obj is not None:
        out.append(str(obj))
    return out


def _title(body: str) -> str:
    m = re.search(r"^#\s+(.+)$", body, re.M)
    return m.group(1) if m else ""


def fields(n: Node) -> tuple[set, set, set]:
    """(id+title, tags, everything). Cached on the node — a 5k-node graph is tokenised
    once per process, not once per query."""
    if n.tokens is None:
        strong = set(_tokens(f"{n.id} {_title(n.body)}"))
        medium = set(_tokens(" ".join(str(t) for t in (n.frontmatter.get("tags") or []))))
        weak = set(_tokens(" ".join(_flatten(n.frontmatter, []) + [n.body]))) | strong | medium
        n.tokens = (strong, medium, weak)
    return n.tokens


def _passes(n: Node, tags, status, type) -> bool:
    if status and n.status not in status:
        return False
    if type and n.type not in type:
        return False
    if tags and not set(n.tags) & set(tags):
        return False
    return True


# Keep a hit only if it scores within this fraction of the best hit. Adaptive, so there
# is no absolute threshold to tune per graph: one strong match suppresses the long tail
# of nodes that merely share a common word with the query.
RELATIVE_FLOOR = 0.35


def retrieve(nodes: dict[str, Node], query: str | None = None, tags=None,
             status=None, type=None) -> list[Node]:
    """Rank nodes by relevance to `query`, narrowed by the filters. Ranked, not filtered:

    ANDing every token meant one unmatched word silenced the whole query, so
    `"has anyone tried self-consistency?"` — the README's own example — answered "no
    prior work found" about a hypothesis the graph was holding, dead and documented.
    A false "untested" is the only failure of this tool that costs real work.

    Tokens are weighted by idf, so a word in every node counts for nothing without
    anybody having to list it, and a rare one dominates.

    This is the ONE retrieval seam: `query`, `index` and the MCP tools all come through
    here, so a semantic backend replaces this body and nothing above it changes.
    """
    pool = [n for n in nodes.values() if _passes(n, tags, status, type)]
    if query is None:
        return sorted(pool, key=lambda n: n.id)

    qt = [t for t in dict.fromkeys(_tokens(query)) if t not in _STOP]
    if not qt or not pool:
        return []

    df = Counter(t for n in pool for t in qt if t in fields(n)[2])
    total = len(pool)

    scored = []
    for n in pool:
        strong, medium, weak = fields(n)
        s = sum(
            (_STRONG if t in strong else _MEDIUM if t in medium else _WEAK)
            * math.log(1 + total / (1 + df[t]))
            for t in qt if t in weak
        )
        if s > 0:
            scored.append((s, n))
    if not scored:
        return []

    floor = RELATIVE_FLOOR * max(s for s, _ in scored)
    return [n for s, n in sorted(scored, key=lambda p: (-p[0], p[1].id)) if s >= floor]


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

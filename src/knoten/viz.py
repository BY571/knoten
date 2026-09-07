"""One HTML file: the graph as columns, and the graph as a map.

Read-only, self-contained, no server and no build step. The payload is inlined, so the
file opens from `file://`, from a share, from a plane.

Two views because there are two questions. **Columns** is the inventory — what exists, in
what role, with what verdict — laid out left to right along the loop a graph declares.
**Map** is the traversal — what a claim rests on and what else that touched — laid out
around the busiest nodes, because a graph's landmarks are wherever its edges converge and
not wherever its vocabulary says they should be.

Layout is a pure function of the graph. Nothing is persisted: positions are derived state,
and a `layout.json` in git would be a merge conflict generator with ten agents appending.
"""
import dataclasses
import hashlib
import json
import math
import time
from pathlib import Path

from .core import (GATE_TYPE, GraphError, compressible, compressible_types, is_general,
                   load, section, shape, supersedes, under)
from .validate import check, load_config

HERE = Path(__file__).parent

# Columns: no vertical centring. Centring would make every column shift when any one of
# them grows, which is the reflow this whole design exists to avoid.
COLW, CARDH, GAP, TOP = 300, 74, 12, 58
# A gate card carries the tally rail — what it killed, what it passed — which makes it
# taller than every other card. Stepping every column by one fixed row height overlapped
# them by about the height of that rail.
RAIL = 28

# Map: golden-angle sunflower. Uniform density, and index k always lands in the same
# place, so appending a node never disturbs 1..k-1.
GOLDEN = math.pi * (3 - math.sqrt(5))
SPACING = 33
# Clusters sit on the same spiral, at a fixed spacing. Dividing a ring by the cluster
# COUNT would rotate every cluster the moment a new one appeared — one isolated node
# appended, and the whole map turns. A constant spacing lets a very large cluster graze
# its neighbour; that is the cheaper failure, and it is local.
CLUSTER = SPACING * 7.5

GATE_RELS = ("kn:survivedGate", "kn:killedByGate")

# Only an ordering hint. A type this does not name is not dropped — it lands after the
# ones that are, in the order the graph first used it. knoten declares no vocabulary.
FLOW = ["question", "source", "idea", "hypothesis", "experiment", "finding",
        "blocker", "retraction"]


# A node with no `created` is almost always one written by hand or by another tool —
# `knoten new` and `knoten commit` both stamp it. Sorting it EMPTY-STRING-FIRST put it in
# slot 0 and pushed every existing node along, which is the one thing this layout promises
# not to do. Undated work sorts last, with the newest, where an unknown arrival belongs.
UNDATED = "9999"


def _order(nodes: dict) -> list:
    """Oldest first, id breaking ties. `created` is what makes appending safe: new work
    sorts last, so it can only ever be added to the end of a column or the rim of a
    cluster."""
    return sorted(nodes.values(),
                  key=lambda n: (str(n.frontmatter.get("created") or UNDATED), n.id))


def _sunflower(k: int) -> tuple:
    r = SPACING * math.sqrt(k)
    return r * math.cos(k * GOLDEN), r * math.sin(k * GOLDEN)


def roles(nodes: dict) -> tuple:
    """Which types are gates, which are shelves, and what order the rest go in.

    Derived from what the edges DO. A type cited via a gate relation is a gate — a bar,
    not a stage — and belongs at the end. A type that is only ever cited and never cites
    is a shelf, and belongs at the start. Precedence is gate, then shelf, then flow: a
    gate is nearly always cited-and-never-citing, so testing for it second would file
    every gate as a shelf.

    This is a function of the WHOLE graph, so unlike positions within a column it is not
    append-stable. A node introducing a type not yet on screen, or the first edge that
    makes a type a gate, reorders the columns once. Both are rare and both are real
    changes in what the graph is; a claim appended to a type already present does not
    move anything.
    """
    gate_types, cited, citing = set(), set(), set()
    for n in nodes.values():
        for l in n.links:
            if (t := nodes.get(l["to"])) is None:
                continue
            citing.add(n.type)
            cited.add(t.type)
            if l["rel"] in GATE_RELS:
                gate_types.add(t.type)
    gate_types.add(GATE_TYPE)

    seen = list(dict.fromkeys(n.type for n in _order(nodes)))
    gates = [t for t in seen if t in gate_types]
    shelves = [t for t in seen if t not in gates and t in cited and t not in citing]
    rest = [t for t in seen if t not in gates and t not in FLOW]

    # A type FLOW names is placed by FLOW, whatever its edges look like. Sorting shelves
    # ahead of everything meant `source` — cited by an idea, citing nothing — overtook
    # `question` on any graph where nothing happened to cite the question back, so the
    # column order contradicted the loop it exists to show.
    ordered = ([t for t in FLOW if t in seen and t not in gates]
               + [t for t in rest if t in shelves]
               + [t for t in rest if t not in shelves]
               + gates)
    return ordered, set(gates), set(shelves)


def _columns(nodes: dict, basis: tuple | None = None) -> dict:
    """Stack each column top-down, accumulating heights rather than counting rows, so a
    type whose card is taller does not overlap the one beneath it.

    `basis`, when given, is `(cols, gates)` from a prior `roles()` call, used instead of
    deriving them from `nodes`. The folded view passes the full view's: `roles()` reads
    what the edges DO, and a covered type with every member hidden would otherwise vanish
    from a smaller node set's own `roles()`, shifting every column after it. Same headers,
    a shorter stack under some of them — not a page that renumbers itself when a rule
    covers the last node of a type."""
    cols, gates = basis if basis is not None else roles(nodes)[:2]
    at = {c: 0.0 for c in cols}
    pos = {}
    for n in _order(nodes):
        pos[n.id] = [cols.index(n.type) * COLW, TOP + at[n.type]]
        at[n.type] += CARDH + GAP + (RAIL if n.type in gates else 0)
    return pos


def _neighbours(nodes: dict) -> dict:
    out = {nid: [] for nid in nodes}
    for n in nodes.values():
        for l in n.links:
            if l["to"] in nodes:
                out[n.id].append(l["to"])
                out[l["to"]].append(n.id)
    return out


def _map(nodes: dict) -> tuple:
    """Cluster around the busiest nodes.

    Degree is the one signal every graph has. Clustering on `type: gate` produced 18
    clusters on one real graph and 2 on another, because how many gates a graph declares
    is a property of its rules, not of knoten.

    Honest limit, and it is bigger than "a node that gains edges": the hub COUNT is
    `round(sqrt(n))`, so it steps up at n ≈ 7, 13, 21, 31, … and the new hub's rank
    inserts mid-list, rotating every later cluster. Measured on a growing graph: 7 of 20
    nodes moved at n=21. Between those thresholds an appended leaf moves nothing.
    Ordering clusters by degree instead of arrival was tried and is strictly worse.
    """
    if not nodes:
        return {}, {}
    nbrs = _neighbours(nodes)
    degree = {nid: len(v) for nid, v in nbrs.items()}
    busiest = lambda nid: (-degree[nid], nid)

    hubs = sorted(nodes, key=busiest)[:max(3, round(math.sqrt(len(nodes))))]
    hub_set = set(hubs)

    def home(nid):
        if direct := [x for x in nbrs[nid] if x in hub_set]:
            return min(direct, key=busiest)
        if nbrs[nid]:                              # one hop further out
            best = min(nbrs[nid], key=busiest)
            if via := [x for x in nbrs[best] if x in hub_set]:
                return min(via, key=busiest)
        return "unattached"

    cells = {h: [h] for h in hubs}
    for n in _order(nodes):
        if n.id not in hub_set:
            cells.setdefault(home(n.id), []).append(n.id)

    rank = {n.id: i for i, n in enumerate(_order(nodes))}
    # `unattached` is not a node and has no arrival rank. Sorting it FIRST (a -1 default)
    # meant the day a graph gained its first orphan, every real cluster shifted one slot
    # along the spiral. It sorts last, where a bucket that only ever grows belongs.
    names = sorted(cells, key=lambda c: (rank.get(c, math.inf), c))

    pos, walls = {}, {}
    for i, c in enumerate(names):
        r = CLUSTER * math.sqrt(i)
        cx, cy = r * math.cos(i * GOLDEN), r * math.sin(i * GOLDEN)
        orbit = [x for x in cells[c] if x != c]
        if c in nodes:
            pos[c] = [round(cx, 2), round(cy, 2)]
        for j, nid in enumerate(orbit):
            dx, dy = _sunflower(j + 1 if c in nodes else j)
            pos[nid] = [round(cx + dx, 2), round(cy + dy, 2)]
        walls[c] = [round(cx, 2), round(cy, 2),
                    round(SPACING * math.sqrt(max(len(orbit), 1)) + 26, 2)]
    return pos, walls


def layout(nodes: dict) -> dict:
    """Both views, keyed by name. Pure, deterministic, no persisted state."""
    pos, _ = _map(nodes)
    return {"columns": _columns(nodes), "map": pos}


def _visible(nodes: dict, under: dict) -> dict:
    """The graph with the covered layer folded away. Its own layout, so a compression
    shrinks the picture; hung positions are derived on the page, never stored."""
    return {nid: n for nid, n in nodes.items() if nid not in under}


def _inherited(nodes: dict, visible: dict, under: dict) -> dict:
    """`visible`, with a coverer also carrying what it covers' links. A rule inherits the
    pull of everything it retired, so on the folded map it is drawn as a landmark: its
    degree jumps from its own edges to its own plus every covered node's — a gate or
    question the covered layer shared gains one edge per covered node, undeduped, on
    purpose, since each covered node really did survive that gate or serve that question
    and a repeated edge is a real vote, not noise to collapse.

    This buys degree SEPARATION between anchors and leaves; it is not immunity from
    `_map`'s documented `round(sqrt(n))` hub-count step, which still applies — and applies
    more often here, since folding is what makes a graph small enough to cross it in the
    first place. On an uncompressed graph `under` is empty and this is the identity: no
    coverer, nothing inherited, `folded` equals the full map."""
    covers = {}
    for covered, coverer in under.items():
        covers.setdefault(coverer, []).append(covered)
    out = {}
    for nid, n in visible.items():
        extra = [l for cid in covers.get(nid, []) for l in nodes[cid].links]
        out[nid] = dataclasses.replace(n, links=n.links + extra) if extra else n
    return out


SECTION_LIMIT = 4000


# The scaffold the panel lays a node's record into. A node body is free-form: an agent
# writes "kill criterion", "kill condition", or "when this is wrong" and any of them is
# the same field, so each canonical LABEL is matched against the aliases the author might
# have used, and rendered under the label in this fixed order. The label is ours, not the
# author's, which is what keeps two findings from disagreeing on shape.
#
# It shapes a claim and its verdict. A node whose headings match no label (a source, a
# gate) renders under the agent's own headings in document order, so nothing is lost -
# only reorganised where it helps.
PANEL_SECTIONS = [
    {"label": "Claim", "aliases": ["The claim", "The idea", "The direction"]},
    {"label": "Scope", "aliases": [
        "What this does not test", "What it excludes", "What is out of scope",
        "Out of scope", "What it is not"]},
    {"label": "Rationale", "aliases": [
        "Why it might be true", "Why it might work", "Why it might hold", "Why here",
        "Rationale", "Why this holds"]},
    {"label": "Risk", "aliases": [
        "Why it might be false", "Why it fails", "Why it won't work", "Risks", "Doubts",
        "Why it might not hold"]},
    {"label": "Method", "aliases": [
        "The setup", "Test", "Method", "The test", "How I tested it", "Design",
        "How it was tested"]},
    {"label": "Result", "aliases": [
        "Result", "The outcome", "Conclusion", "What I found", "The result"]},
    {"label": "Evidence", "aliases": [
        "Evidence", "The number", "Numbers", "The figures"]},
    {"label": "Kill criterion", "aliases": [
        "Kill criterion", "Kill condition", "When this is wrong", "Kill threshold"]},
]


def _clip(text: str) -> str:
    """Say when the section is cut. The panel folds long prose behind "show more", which
    would otherwise present a truncated section as the whole of it."""
    return text if len(text) <= SECTION_LIMIT else text[:SECTION_LIMIT] + "…  [truncated]"


def payload(root: Path) -> dict:
    """Everything the page draws. Structured, never raw markdown: `section()` already
    splits the body, and `results`/`repro` are already mappings — so no markdown parser
    is needed on either side of the wire."""
    nodes = load(root)
    cfg = load_config(root)
    cols, gates, shelves = roles(nodes)
    gates &= set(cols)
    pos, walls = _map(nodes)
    columns = _columns(nodes)

    # Who covers whom, and the graph with the covered layer folded away: its own layout,
    # so a compression shrinks the picture on the page that shows it.
    covered = under(nodes)
    visible = _visible(nodes, covered)
    fpos, fwalls = _map(_inherited(nodes, visible, covered))
    # Columns stay the plain visible set, not the inherited one: `_inherited`'s extra
    # links are only for `_map`'s degree count, and feeding them to `_columns` would risk
    # entering `roles()`'s citing/cited sets and flipping a shelf classification.
    fcols = _columns(visible, basis=(cols, gates))
    # The same clusters `knoten frontier` shows: recursive compression is allowed, so an
    # alive rule that shares a gate or tag with loose specifics is a candidate too.
    clusters = compressible(nodes, compressible_types(cfg))

    # A graph may declare `node_types` as a plain list, or as a mapping of type -> what
    # that word means here. Only the second can fill the legend.
    # What the graph's own rules say is wrong with it. Without this the page renders a
    # graph that breaks its own rules exactly as it renders a clean one.
    broken = {}
    for v in check(nodes, root):
        broken.setdefault(v.node, []).append({"rule": v.rule, "message": v.message})

    types = cfg.get("node_types")
    return {
        "root": root.name,
        "count": len(nodes),
        "columns": cols,
        "gate_types": sorted(gates),
        "shelf_types": sorted(shelves),
        "walls": walls,
        "violations": broken,
        "folded": {nid: {"columns": fcols[nid], "map": fpos[nid]} for nid in visible},
        "folded_walls": fwalls,
        "shape": {**shape(nodes, cfg), "clusters": len(clusters)},
        "clusters": clusters,
        "graph": {
            "name": cfg.get("name"),
            # `node_types` is a list when a graph only declares its vocabulary, and a
            # mapping when it also says what the words mean. Both are legal.
            "vocab": types if isinstance(types, dict) else {},
            "rules": [{k: r.get(k) for k in
                       ("id", "when_type", "when_status", "require_edge",
                        "require_sections", "max_alive", "message")}
                      for r in (cfg.get("rules") or [])],
        },
        "nodes": [{
            "id": n.id, "type": n.type, "status": n.status,
            "title": n.title, "tags": n.tags,
            "created": str(n.frontmatter.get("created") or ""),
            "links": [{"rel": l["rel"], "to": l["to"]} for l in n.links],
            "backlinks": [{"rel": b["rel"], "to": b["to"]} for b in n.backlinks],
            "sections": [{"title": t, "text": _clip(section(n.body, t, collapse=False) or "")}
                         for t in n.sections],
            "results": n.results, "repro": n.repro, "attachments": n.attachments,
            "columns": columns[n.id], "map": pos[n.id],
            # `rule` is a badge, not a shape. A general node that has stopped being
            # alive has stopped standing for what it retired -- `validate` names its
            # orphans -- so the page must not go on calling it the rule over them.
            "rule": is_general(n) and n.status == "alive",
            "covers": supersedes(n), "under": covered.get(n.id),
        } for n in _order(nodes)],
    }


def fingerprint(root: Path) -> str:
    """What `--watch` polls: a hash of every node plus graph.yaml.

    mtimes were the obvious choice and are wrong here. Filesystem timestamp granularity
    is coarser than an agent writing three nodes in a burst, and every file in a freshly
    written graph can report the SAME `st_mtime_ns` — so the poll would sit there
    reporting no change. Reading the content costs 0.56 ms on a 67-node graph, measured,
    which is nothing against a two-second poll.
    """
    h = hashlib.blake2b(digest_size=16)
    for f in sorted((root / "nodes").glob("*.md")) + [root / "graph.yaml"]:
        if f.exists():
            h.update(f.name.encode())
            h.update(f.read_bytes())
    return h.hexdigest()


def render(root: Path, reload_ms: int = 0) -> str:
    """The template with the payload inlined.

    `<` is escaped rather than the `</script>` sequence alone: a node body containing that
    literal would otherwise close the tag and blank the whole page — every node in the
    graph lost to one string in one post-mortem.
    """
    blob = json.dumps(payload(root), default=str).replace("<", "\\u003c")
    html = (HERE / "viz.html").read_text(encoding="utf-8")
    if reload_ms:
        # Stamped only under --watch. A static export must stay byte-identical for the
        # same graph, or `git diff` on a committed page is noise.
        html = (html.replace("__RELOAD_MS__", str(int(reload_ms)))
                    .replace("__BUILT_AT__", str(int(time.time()))))
    else:
        # Cut the block out rather than leave it behind a falsy guard. A file you emailed
        # someone should contain no code that reloads it, not merely code that declines to.
        a, b = html.index("/*__WATCH__*/"), html.rindex("/*__WATCH__*/")
        html = html[:a] + html[b + len("/*__WATCH__*/"):]
    return (html.replace("__PANEL_SECTIONS__", json.dumps(PANEL_SECTIONS))
                 .replace("__KNOTEN_DATA__", blob))


def write(root: Path, dest: Path, reload_ms: int = 0) -> Path:
    if not (root / "nodes").is_dir():
        raise GraphError(f"{root} is not a knoten graph (no nodes/ directory)")
    dest.write_text(render(root, reload_ms), encoding="utf-8")
    return dest

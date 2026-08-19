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
import json
import math
from pathlib import Path

from .core import GATE_TYPE, GraphError, load, section
from .validate import load_config

HERE = Path(__file__).parent

# Columns: no vertical centring. Centring would make every column shift when any one of
# them grows, which is the reflow this whole design exists to avoid.
COLW, CARDH, GAP, TOP = 300, 74, 12, 58

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
FLOW = ["source", "idea", "question", "hypothesis", "experiment", "finding",
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
    flow = [t for t in seen if t not in gates and t not in shelves]
    ordered = ([t for t in FLOW if t in shelves]
               + [t for t in shelves if t not in FLOW]
               + [t for t in FLOW if t in flow]
               + [t for t in flow if t not in FLOW]
               + gates)
    return ordered, set(gates), set(shelves)


def _columns(nodes: dict) -> dict:
    cols, _, _ = roles(nodes)
    at = {c: 0 for c in cols}
    pos = {}
    for n in _order(nodes):
        i = cols.index(n.type)
        pos[n.id] = [i * COLW, TOP + at[n.type] * (CARDH + GAP)]
        at[n.type] += 1
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


SECTION_LIMIT = 1500


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

    types = cfg.get("node_types")
    return {
        "root": root.name,
        "count": len(nodes),
        "columns": cols,
        "gate_types": sorted(gates),
        "shelf_types": sorted(shelves),
        "walls": walls,
        "graph": {
            "name": cfg.get("name"),
            # `node_types` is a list when a graph only declares its vocabulary, and a
            # mapping when it also says what the words mean. Both are legal.
            "vocab": types if isinstance(types, dict) else {},
            "rules": [{k: r.get(k) for k in
                       ("id", "when_type", "when_status", "require_edge",
                        "require_sections", "message")}
                      for r in (cfg.get("rules") or [])],
        },
        "nodes": [{
            "id": n.id, "type": n.type, "status": n.status,
            "title": n.title, "tags": n.tags,
            "created": str(n.frontmatter.get("created") or ""),
            "links": [{"rel": l["rel"], "to": l["to"]} for l in n.links],
            "backlinks": [{"rel": b["rel"], "to": b["to"]} for b in n.backlinks],
            "sections": [{"title": t, "text": _clip(section(n.body, t) or "")}
                         for t in n.sections],
            "results": n.results, "repro": n.repro, "attachments": n.attachments,
            "columns": columns[n.id], "map": pos[n.id],
        } for n in _order(nodes)],
    }


def render(root: Path) -> str:
    """The template with the payload inlined.

    `<` is escaped rather than the `</script>` sequence alone: a node body containing that
    literal would otherwise close the tag and blank the whole page — every node in the
    graph lost to one string in one post-mortem.
    """
    blob = json.dumps(payload(root), default=str).replace("<", "\\u003c")
    return (HERE / "viz.html").read_text(encoding="utf-8").replace("__KNOTEN_DATA__", blob)


def write(root: Path, dest: Path) -> Path:
    if not (root / "nodes").is_dir():
        raise GraphError(f"{root} is not a knoten graph (no nodes/ directory)")
    dest.write_text(render(root), encoding="utf-8")
    return dest

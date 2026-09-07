"""One HTML file: the graph as columns, as a map, and as the numbers it is moving.

Read-only, self-contained, no server and no build step; the payload is inlined, so the
file opens from `file://`. Three views because there are three questions: **columns** is
the inventory along the loop a graph declares, **map** is the traversal around the busiest
nodes, since a graph's landmarks are where its edges converge, and **metrics** is the
record, one chart per number the graph declared it is trying to move. Layout is a pure
function of the graph, and a persisted `layout.json` would be a merge conflict generator.
"""
import hashlib
import json
import re
import math
import time
from pathlib import Path

from .core import (GATE_TYPE, UNDATED, GraphError, is_general, load, metric,
                   metrics_declared, section, shape, supersedes, under)
from .validate import check, load_config

HERE = Path(__file__).parent

# Columns: no vertical centring. Centring would make every column shift when any one of
# them grows, which is the reflow this whole design exists to avoid.
COLW, CARDH, GAP, TOP = 300, 108, 12, 58
# A gate card carries the tally rail — what it killed, what it passed — which makes it
# taller than every other card. Stepping every column by one fixed row height overlapped
# them by about the height of that rail.
RAIL = 28

# Map: golden-angle sunflower. Uniform density, and index k always lands in the same
# place, so appending a node never disturbs 1..k-1.
GOLDEN = math.pi * (3 - math.sqrt(5))
SPACING = 33
# Clusters sit on the same spiral at a FIXED spacing: dividing a ring by the cluster
# count would turn the whole map the moment one isolated node was appended.
CLUSTER = SPACING * 7.5

GATE_RELS = ("kn:survivedGate", "kn:killedByGate")

# Only an ordering hint. A type this does not name is not dropped — it lands after the
# ones that are, in the order the graph first used it. knoten declares no vocabulary.
FLOW = ["question", "source", "idea", "hypothesis", "experiment", "finding",
        "blocker", "retraction"]



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
    """Which types are gates, which are shelves, and what order the rest go in. Derived
    from what the edges DO: a type cited via a gate relation is a gate and goes at the
    end, a type only ever cited and never citing is a shelf and goes at the start, and
    gate is tested first since a gate is nearly always cited-and-never-citing. A function
    of the WHOLE graph, so unlike positions within a column it is not append-stable."""
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

    # A type FLOW names is placed by FLOW, whatever its edges look like: shelves first
    # let `source` overtake `question` and contradict the loop this exists to show.
    ordered = ([t for t in FLOW if t in seen and t not in gates]
               + [t for t in rest if t in shelves]
               + [t for t in rest if t not in shelves]
               + gates)
    return ordered, set(gates), set(shelves)


def _columns(nodes: dict, basis: tuple | None = None) -> dict:
    """Stack each column top-down, accumulating heights rather than counting rows, so a
    taller card does not overlap the one beneath it.

    `basis`, when given, is `(cols, gates)` from a prior `roles()` call. The folded view
    passes the full view's, or a covered type with every member hidden would vanish from
    the smaller set's own `roles()` and shift every column after it."""
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
    """Cluster around the busiest nodes. Degree is the one signal every graph has;
    clustering on `type: gate` produced 18 clusters on one graph and 2 on another.

    Honest limit: the hub COUNT is `round(sqrt(n))`, so it steps at n ≈ 7, 13, 21, … and
    the new hub's rank inserts mid-list, rotating every later cluster (7 of 20 nodes moved
    at n=21, measured). Between those thresholds an appended leaf moves nothing."""
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
    # `unattached` has no arrival rank and sorts LAST: first, the day a graph gained its
    # first orphan every real cluster shifted one slot along the spiral.
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
    shrinks the picture; hung positions are derived on the page, never stored. Degree
    separation between rules and leaves comes for free: a rule keeps its own edges while
    the covered nodes' edges leave the map entirely."""
    return {nid: n for nid, n in nodes.items() if nid not in under}


SECTION_LIMIT = 4000


# The scaffold the panel lays a node's record into. A body is free-form: "kill criterion",
# "kill condition" and "when this is wrong" are one field, so each canonical LABEL is
# matched against the aliases an author might use and rendered in this fixed order. A node
# whose headings match no label renders under its own headings, so nothing is lost.
PANEL_SECTIONS = [
    {"label": "Origin", "aliases": ["Where it came from", "What it says"]},
    {"label": "Claim", "aliases": ["The claim", "The idea", "The direction", "What it shows"]},
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
        "The setup", "Setup", "How to reproduce", "Test", "Method", "The test",
        "How I tested it", "Design", "How it was tested"]},
    {"label": "Result", "aliases": [
        "Result", "The outcome", "Conclusion", "What I found", "The result"]},
    {"label": "Evidence", "aliases": [
        "Evidence", "The number", "Numbers", "The figures"]},
    {"label": "Kill criterion", "aliases": [
        "Kill criterion", "Kill condition", "When this is wrong", "Kill threshold",
        "What would make it false"]},
]


SUMMARY_LIMIT = 180


def _lead(body: str) -> str:
    """The first paragraph after the title and before the first `##`: what the card can
    say about a node in two lines. Falls back to the first section's text."""
    m = re.match(r"\s*#[^\n]*\n(.*?)(?=\n## |\Z)", body, re.S)
    text = " ".join((m.group(1) if m else "").split())
    if not text:
        for sec in re.split(r"^## .*$", body, flags=re.M)[1:]:
            text = " ".join(sec.split())
            if text:
                break
    return text if len(text) <= SUMMARY_LIMIT else text[:SUMMARY_LIMIT].rsplit(" ", 1)[0] + "…"


def _clip(text: str) -> str:
    """Say when the section is cut. The panel folds long prose behind "show more", which
    would otherwise present a truncated section as the whole of it."""
    return text if len(text) <= SECTION_LIMIT else text[:SECTION_LIMIT] + "…  [truncated]"


def payload(root: Path) -> dict:
    """Everything the page draws. Structured, never raw markdown, so no markdown parser
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
    fpos, fwalls = _map(visible)
    fcols = _columns(visible, basis=(cols, gates))

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
        "shape": shape(nodes, cfg),
        # One series per declared metric. The chart is the only view that reads the graph
        # along its time axis; every other one reads it along its edges.
        "metrics": [{"name": name, "goal": goal, "points": metric(nodes, name, goal)}
                    for name, goal in metrics_declared(cfg).items()],
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
            "title": n.title, "tags": n.tags, "summary": _lead(n.body),
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
    """What `--watch` polls: a hash of every node plus graph.yaml. Not mtimes -- every
    file in a freshly written graph can report the same `st_mtime_ns`, so the poll sat
    there reporting no change. Hashing costs 0.56 ms on a 67-node graph."""
    h = hashlib.blake2b(digest_size=16)
    for f in sorted((root / "nodes").glob("*.md")) + [root / "graph.yaml"]:
        if f.exists():
            h.update(f.name.encode())
            h.update(f.read_bytes())
    return h.hexdigest()


def render(root: Path, reload_ms: int = 0) -> str:
    """The template with the payload inlined. `<` is escaped, not the `</script>`
    sequence alone: a node body containing that literal would close the tag and blank the
    whole page."""
    blob = json.dumps(payload(root), default=str).replace("<", "\\u003c")
    html = (HERE / "viz.html").read_text(encoding="utf-8")
    if reload_ms:
        # Stamped only under --watch. A static export must stay byte-identical for the
        # same graph, or `git diff` on a committed page is noise.
        html = (html.replace("__RELOAD_MS__", str(int(reload_ms)))
                    .replace("__BUILT_AT__", str(int(time.time()))))
    else:
        # Cut the block out rather than leave it behind a falsy guard: a file you emailed
        # someone should contain no code that reloads it.
        a, b = html.index("/*__WATCH__*/"), html.rindex("/*__WATCH__*/")
        html = html[:a] + html[b + len("/*__WATCH__*/"):]
    return (html.replace("__PANEL_SECTIONS__", json.dumps(PANEL_SECTIONS))
                 .replace("__KNOTEN_DATA__", blob))


def write(root: Path, dest: Path, reload_ms: int = 0) -> Path:
    if not (root / "nodes").is_dir():
        raise GraphError(f"{root} is not a knoten graph (no nodes/ directory)")
    dest.write_text(render(root, reload_ms), encoding="utf-8")
    return dest

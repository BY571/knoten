"""The rules engine.

The core knows NOTHING about any domain — every rule comes from the graph's own
`graph.yaml`, so a trading graph and a biology graph share this code unchanged.

A rule this engine cannot understand is a hard error, never a no-op: a rule that
silently enforces nothing is worse than no rule, because you believe you are covered.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .core import (GATE_TYPE, GENERATED, GOALS, ID_RE, INVERSE, GraphError,
                   Node, _csv, _yaml, compressible_types, question_of, section,
                   supersedes)

# A rule key that is not in here is a typo. Refuse it.
RULE_KEYS = {
    "id",                  # required
    "message",             # what the human sees when it fires
    "when_status",         # only apply to these statuses
    "when_type",           # only apply to these node types
    "require_edge",        # node must declare this relation
    "require_sections",    # body must contain these `## ` headings
    "require_field",       # frontmatter must carry this key, any non-empty value
    "require_result",      # results must carry this key
    "require_result_min",  # {key: minimum} — numeric floor
    "require_field_one_of",  # {field: [allowed]} — closed vocabulary
    "forbid_fields",       # this type must NOT carry these frontmatter keys
    "require_edge_target",   # {rel, type, status, min} — what the edge must POINT AT
    "require_backlink",      # same shape, read from the other side: what must point AT
                             # this node. `rel` is the GENERATED inverse, e.g. kn:testedBy
    "unless_edge",           # skip this rule for a node that declares this relation
}

# Same for the top level. `node_type:` (singular) would be the next silent no-op.
GRAPH_KEYS = {"name", "description", "node_types", "statuses", "tags", "rules",
              "compressible", "metrics"}

# The rule ids the engine emits on its own, whatever the graph declares. A graph rule may
# not take one of these names: a shadowed check reports green forever.
STRUCTURAL_RULES = frozenset({
    "authored-backlink", "dangling-edge", "malformed-repro", "malformed-results",
    "malformed-tags", "mismatched-id", "missing-attachment", "missing-status",
    "missing-type", "not-a-gate", "supersession", "unknown-relation", "unknown-status",
    "unknown-tag", "unknown-type",
})

# `GATE_TYPE` was this until the rename. Named only so the migration check below can
# recognise a graph that predates it; nothing else in the package may use it.
RENAMED_GATE_TYPE = "method"
GATE_RELS = ("kn:survivedGate", "kn:killedByGate")
SURVIVED_GATE = GATE_RELS[0]  # a killed gate is not a bar survived


@dataclass
class Violation:
    node: str
    rule: str
    message: str


def load_config(root: Path) -> dict:
    """The graph's own declaration. Every key here is enforced; a key knoten does not
    understand is a hard error, because config that enforces nothing is decoration."""
    f = root / "graph.yaml"
    if not f.exists():
        return {}
    cfg = _yaml(f.read_text(encoding="utf-8"), "graph.yaml")

    if unknown := set(cfg) - GRAPH_KEYS:
        raise GraphError(
            f"graph.yaml: unknown key(s) {', '.join(sorted(unknown))}. "
            f"Known keys: {', '.join(sorted(GRAPH_KEYS))}"
        )

    # `node_types` may also be a MAPPING of type -> what that word means here. Membership
    # checks the keys either way, so nothing downstream changes; the values are for the
    # reader and for `knoten viz`, because knoten defines none of these words itself.
    for key in ("node_types", "statuses", "tags"):
        allowed = (list, dict) if key == "node_types" else (list,)
        if key in cfg and not isinstance(cfg[key], allowed):
            raise GraphError(f"graph.yaml: `{key}` must be a list, got {cfg[key]!r}")

    types = cfg.get("node_types")
    if isinstance(types, dict):
        for k, v in types.items():
            if not isinstance(v, str) or not v.strip():
                raise GraphError(
                    f"graph.yaml: `node_types` entry '{k}' must be a one-line meaning, "
                    f"got {v!r}. Use a plain list if you do not want to write meanings.")
    else:
        # `- hypothesis: a claim` is the natural half-migration to the mapping form: a
        # LIST of one-key mappings, still a list, so every node would report
        # `unknown-type` and the message would print raw dicts back at the reader.
        for t in types or []:
            if isinstance(t, (dict, list)):
                raise GraphError(
                    f"graph.yaml: `node_types` entry {t!r} is not a type name. To write "
                    f"meanings, drop the `- ` and make `node_types` a mapping.")

    # After `node_types`' own shape checks, so this cannot blame `compressible` for a
    # mistake in `node_types`.
    if (comp := cfg.get("compressible")) is not None:
        if not isinstance(comp, list) or not comp or not all(isinstance(t, str) for t in comp):
            raise GraphError("graph.yaml: `compressible` must be a non-empty list of node "
                             f"types, got {comp!r}")
        if (declared := cfg.get("node_types")) and (
                unknown := sorted({t for t in comp if t not in declared})):
            raise GraphError(f"graph.yaml: `compressible` names type(s) not in node_types: "
                             f"{', '.join(unknown)}")

    # A metric is what the graph is trying to move. Refused the same way a rule is: a
    # `goal: minimise` nobody understands would silently read as `max` and rank every
    # result upside down, and a metric name that is not an id cannot be a CLI argument.
    if (metrics := cfg.get("metrics")) is not None:
        if not isinstance(metrics, dict) or not metrics:
            raise GraphError("graph.yaml: `metrics` must be a non-empty mapping of name -> "
                             f"{{goal: max|min}}, got {metrics!r}")
        for name, spec in metrics.items():
            if not ID_RE.match(str(name)):
                raise GraphError(f"graph.yaml: metric name {name!r} must be lowercase "
                                 f"letters, digits, - and _, starting with a letter or digit")
            if not isinstance(spec, dict) or set(spec) - {"goal"}:
                raise GraphError(f"graph.yaml: metric '{name}' must be a mapping with at "
                                 f"most a `goal` key, got {spec!r}")
            if spec.get("goal", "max") not in GOALS:
                raise GraphError(f"graph.yaml: metric '{name}': `goal` must be one of "
                                 f"{', '.join(GOALS)}, got {spec['goal']!r}")

    rules = cfg.get("rules") or []
    if not isinstance(rules, list):
        raise GraphError("graph.yaml: `rules` must be a list")
    for r in rules:
        if not isinstance(r, dict):
            raise GraphError(f"graph.yaml: each rule must be a mapping, got {r!r}")
        if "id" not in r:
            raise GraphError(f"graph.yaml: rule is missing `id`: {r!r}")
        if r["id"] in STRUCTURAL_RULES:
            raise GraphError(f"graph.yaml: rule '{r['id']}' takes the name of a check knoten "
                             f"always runs, which would shadow it. Pick another id.")
        if unknown := set(r) - RULE_KEYS:
            raise GraphError(
                f"graph.yaml: rule '{r['id']}' has unknown key(s) "
                f"{', '.join(sorted(unknown))}. A rule key knoten does not understand "
                f"would enforce nothing. Known keys: {', '.join(sorted(RULE_KEYS))}"
            )
        _check_values(r)
    cfg["rules"] = rules
    return cfg


def load_rules(root: Path) -> list[dict]:
    return load_config(root).get("rules", [])


def _check_values(r: dict) -> None:
    """Key names are not enough: a wrong-SHAPED value must also be a hard error, not a
    TypeError. `require_edge: [x]` is the natural mistake, since `when_status` takes a list."""
    rid = r["id"]
    for key in ("require_edge", "require_field", "require_result"):
        if key in r and not isinstance(r[key], str):
            raise GraphError(
                f"graph.yaml: rule '{rid}': `{key}` must be a single string, "
                f"got {r[key]!r}")

    if "require_field_one_of" in r:
        fields = r["require_field_one_of"]
        if not isinstance(fields, dict):
            raise GraphError(
                f"graph.yaml: rule '{rid}': `require_field_one_of` must be a mapping of "
                f"{{field: [allowed, values]}}, got {fields!r}")
        for k, v in fields.items():
            if not isinstance(v, list) or not v:
                raise GraphError(
                    f"graph.yaml: rule '{rid}': `require_field_one_of` for '{k}' must be "
                    f"a non-empty list of allowed values, got {v!r}")

    # The two keys read the same shape from opposite directions, so `rel` is checked
    # against opposite halves of INVERSE. Backwards, a rule loads cleanly and then fails
    # every node forever with nothing saying the direction was wrong.
    for key, known in (("require_edge_target", INVERSE), ("require_backlink", GENERATED)):
        if key not in r:
            continue
        spec = r[key]
        if not isinstance(spec, dict) or not isinstance(spec.get("rel"), str):
            raise GraphError(
                f"graph.yaml: rule '{rid}': `{key}` must be a mapping with a `rel` "
                f"string, e.g. {{rel: prov:wasDerivedFrom, status: alive}}, got {spec!r}")
        if spec["rel"] not in known:
            side = "a node declares" if key == "require_edge_target" else "is generated"
            raise GraphError(
                f"graph.yaml: rule '{rid}': `{key}` `rel` must be a relation that "
                f"{side}, one of {', '.join(sorted(known))} — got {spec['rel']!r}")
        least = spec.get("min", 1)
        if isinstance(least, bool) or not isinstance(least, int) or least < 1:
            raise GraphError(
                f"graph.yaml: rule '{rid}': `{key}` `min` must be a positive whole "
                f"number, got {least!r}")

    if "require_result_min" in r:
        floors = r["require_result_min"]
        if not isinstance(floors, dict):
            raise GraphError(
                f"graph.yaml: rule '{rid}': `require_result_min` must be a mapping of "
                f"{{result_key: number}}, got {floors!r}")
        for k, v in floors.items():
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise GraphError(
                    f"graph.yaml: rule '{rid}': `require_result_min` floor for '{k}' must "
                    f"be a number, got {v!r}")

    if "unless_edge" in r:
        u = r["unless_edge"]
        if not isinstance(u, str) or u not in INVERSE:
            raise GraphError(f"graph.yaml: rule '{rid}': `unless_edge` must be a relation a "
                             f"node declares, one of {', '.join(sorted(INVERSE))} — got {u!r}")



def _tags(n: Node, cfg: dict) -> list[Violation]:
    """Tags are the filter axis. A typo'd tag is not cosmetic: the node stays in the
    graph but falls outside every filtered view of it."""
    raw = n.frontmatter.get("tags")
    if raw is None:
        return []
    if not isinstance(raw, list):
        return [Violation(n.id, "malformed-tags",
                          f"`tags` must be a list, got {type(raw).__name__} "
                          f"({raw!r}). Write `tags: [{raw}]`.")]
    if not (declared := cfg.get("tags")):
        return []
    known = {str(t) for t in declared}
    return [Violation(n.id, "unknown-tag",
                      f"tag '{t}' is not declared in graph.yaml "
                      f"(tags: {', '.join(map(str, declared))})")
            for t in map(str, raw) if t not in known]


def _blocks(n: Node) -> list[Violation]:
    """`results:` and `repro:` must be mappings. `results: 5` reached
    `n.results.get(key)` as an AttributeError out of the validator whose job is refusing
    bad nodes politely. Here, not in the parser: a scalar is malformed, not unparseable."""
    return [Violation(n.id, f"malformed-{name}",
                      f"`{name}` must be a mapping of key: value, got "
                      f"{type(raw).__name__} ({raw!r})")
            for name in ("results", "repro")
            if (raw := n.frontmatter.get(name)) is not None and not isinstance(raw, dict)]


def _vocabulary(n: Node, cfg: dict) -> list[Violation]:
    """A node's `type` and `status` must be words THIS graph declared. The core invents
    no vocabulary: declare no `node_types` and none is checked. But a claim with a typo'd
    status silently drops out of every query, which filters on the known set."""
    out = []
    if not n.type:
        out.append(Violation(n.id, "missing-type", "node declares no `type`"))
    elif (types := cfg.get("node_types")) and n.type not in types:
        out.append(Violation(n.id, "unknown-type",
                             f"type '{n.type}' is not declared in graph.yaml "
                             f"(node_types: {', '.join(map(str, types))})"))

    out += _tags(n, cfg)

    if statuses := cfg.get("statuses"):
        if not n.status:
            out.append(Violation(n.id, "missing-status",
                                 f"node declares no `status`, so it escapes every "
                                 f"when_status rule and never appears in a query "
                                 f"(statuses: {', '.join(map(str, statuses))})"))
        elif n.status not in statuses:
            out.append(Violation(n.id, "unknown-status",
                                 f"status '{n.status}' is not declared in graph.yaml "
                                 f"(statuses: {', '.join(map(str, statuses))})"))
    return out


def _alive_backers(nodes: dict[str, Node], t: Node) -> set:
    """Who, still alive, supersedes `t`. Alive only: a retracted general node has stopped
    standing for anything, so it neither keeps its targets retired nor blocks a second
    node from generalising them properly."""
    return {b["to"] for b in t.backlinks if b["rel"] == "npx:supersededBy"
            and (s := nodes.get(b["to"])) is not None and s.status == "alive"}


def _supersession(nodes: dict[str, Node], cfg: dict) -> list[Violation]:
    """The bar a node clears before it may retire others. Always on: a graph that lets a
    weaker claim replace stronger ones by declaring one edge has no bar at all.

    One target or many, the checks are the same; the difference between "replaces" and
    "generalises" is a count, not a rule."""
    types = compressible_types(cfg)
    out = []
    for n in nodes.values():
        # A superseded node with no `npx:supersededBy` at all is a claim nothing stands
        # in for: its superseder was deleted, so the node is hidden from `index` and
        # answers no question. The graph must not lose a finding that quietly.
        if n.status == "superseded" and not any(b["rel"] == "npx:supersededBy"
                                                for b in n.backlinks):
            out.append(Violation(n.id, "supersession",
                                 f"{n.id} is superseded by nothing; `knoten update "
                                 f"{n.id} --status alive` brings it back"))
        raw = supersedes(n)
        if not raw:
            continue

        def vio(m):
            out.append(Violation(n.id, "supersession", m))

        # The superseder's own type, before anything about its targets: a graph that lets
        # a `hypothesis` retire findings has no compression bar, it has a delete button.
        if n.type not in types:
            vio(f"{n.id} ({n.type}) may not supersede; only {'/'.join(types)} can")
        if n.id in raw:
            vio(f"{n.id} cannot supersede itself")
        # dangling: already reported as `dangling-edge`; self: refused just above.
        targets = [t for t in raw if t in nodes and t != n.id]
        if not targets:
            continue
        questions = {}
        for tid in targets:
            t = nodes[tid]
            # A target already retired by some OTHER alive node is refused by naming it.
            # Unless this node has itself stopped being alive: it is then a record of a
            # past compression, and must not be blamed for the node that took over.
            other = next(iter(sorted(_alive_backers(nodes, t) - {n.id})), None)
            # Two nodes retiring each other stand for nothing: each is inside the other,
            # so a page that folds the covered layer draws neither. Reported from the
            # smaller id only, or validate prints the pair twice, once from each end.
            if n.id in supersedes(t) and n.id < tid:
                vio(f"{n.id} and {tid} supersede each other")
            if t.status == "alive" or (t.status == "superseded"
                                       and (other is None or n.status != "alive")):
                pass
            elif t.status == "superseded":
                vio(f"{n.id} supersedes {tid}, which {other} already superseded")
            else:
                vio(f"{n.id} supersedes {tid}, which is {t.status}, not alive")
            if t.type not in types:
                vio(f"{n.id} supersedes {tid} ({t.type}); only {'/'.join(types)} can be superseded")
            q = question_of(nodes, tid)
            if q is None:
                vio(f"{n.id} supersedes {tid}, and cannot find the question {tid} stands under")
            else:
                questions[tid] = q
        mine = question_of(nodes, n.id)
        seen = sorted(set(questions.values()) | ({mine} if mine else set()))
        if len(seen) > 1:
            vio(f"{n.id} and its targets stand under different questions ({', '.join(seen)})")
        faced = {l["to"] for l in n.links if l["rel"] == SURVIVED_GATE}
        for tid in targets:
            for g in sorted({l["to"] for l in nodes[tid].links if l["rel"] == SURVIVED_GATE} - faced):
                vio(f"{n.id} must survive {g}, which {tid} survived; a general claim faces "
                    f"the union of the bars")
        covers = section(n.body, "Covers")
        if covers is None:
            vio(f"{n.id} has no '## Covers' section, or it is empty")
        else:
            for tid in targets:
                if not re.search(rf"(?<![a-z0-9_-]){re.escape(tid)}(?![a-z0-9_-])", covers):
                    vio(f"## Covers of {n.id} does not mention {tid}")
    return out


def _structural(nodes: dict[str, Node], root: Path, cfg: dict) -> list[Violation]:
    """Checks the core ALWAYS runs. Structural, not domain."""
    out = []
    ids = set(nodes)
    for nid, n in nodes.items():
        # The real id is the filename; `id:` in the frontmatter is decorative. A node
        # whose `id:` says something else lies to every human reading it.
        if (declared := n.frontmatter.get("id")) and str(declared) != nid:
            out.append(Violation(nid, "mismatched-id",
                                 f"frontmatter says id '{declared}' but the file is "
                                 f"{nid}.md — the filename is the id"))
        out += _blocks(n) + _vocabulary(n, cfg)
        for l in n.links:
            rel = l["rel"]
            if rel in GENERATED:
                out.append(Violation(nid, "authored-backlink",
                                     f"'{rel}' is a generated back-link — declare the "
                                     f"forward edge on the other node instead"))
            elif rel not in INVERSE:
                out.append(Violation(nid, "unknown-relation",
                                     f"'{rel}' is not a known relation. It creates no "
                                     f"back-link, so the node is invisible from the other "
                                     f"side. Known: {', '.join(sorted(INVERSE))}"))
            if l["to"] not in ids:
                out.append(Violation(nid, "dangling-edge",
                                     f"-> {l['to']} ({rel}) does not exist"))
        for a in n.attachments:
            if not (root / "attachments" / nid / a).exists():
                out.append(Violation(nid, "missing-attachment",
                                     f"'{a}' is listed but not in attachments/{nid}/"))

    # MIGRATION AID, deliberately narrow: `GATE_TYPE` used to be "method", and a graph
    # written before the rename loses a `knoten gates` row while saying nothing. It fires
    # ONLY on the dead word -- a graph calling its bar `criterion` is not wrong. Delete
    # this check once graphs have moved.
    stale = {l["to"] for n in nodes.values() for l in n.links
             if l["rel"] in GATE_RELS and nodes.get(l["to"]) is not None
             and nodes[l["to"]].type == RENAMED_GATE_TYPE}
    for nid in sorted(stale):
        out.append(Violation(nid, "not-a-gate",
                             f"cited as a gate but typed '{RENAMED_GATE_TYPE}' — the type "
                             f"was renamed to '{GATE_TYPE}', and `knoten gates` only finds "
                             f"that, so this node is invisible to it"))
    out += _supersession(nodes, cfg)
    return out


def _matching(spec: dict, edges: list, nodes: dict) -> int:
    """How many DISTINCT nodes on the other end of these edges match `spec`. Distinct,
    not one per edge: `min: 3` states an inductive standard, and listing one finding three
    times is not three observations. A dangling target is not evidence either."""
    types, statuses = _csv(spec.get("type")), _csv(spec.get("status"))
    return len({e["to"] for e in edges
                if e["rel"] == spec["rel"] and (t := nodes.get(e["to"])) is not None
                and (not types or t.type in types)
                and (not statuses or t.status in statuses)})


def applies(status: str, ntype: str, r: dict) -> bool:
    """Does this rule apply to a node with this status/type? Shared with `knoten new`, so
    the scaffold and the validator cannot disagree about which rules are in play."""
    if (st := _csv(r.get("when_status"))) and status not in st:
        return False
    if (ty := _csv(r.get("when_type"))) and ntype not in ty:
        return False
    return True


def check(nodes: dict[str, Node], root: Path) -> list[Violation]:
    cfg = load_config(root)
    out = _structural(nodes, root, cfg)

    for n in nodes.values():
        for r in cfg.get("rules", []):
            if not applies(n.status, n.type, r):
                continue
            if (u := r.get("unless_edge")) and u in n.rels():
                continue
            rid, msg = r["id"], str(r.get("message", r["id"])).strip()

            if (rel := r.get("require_edge")) and rel not in n.rels():
                out.append(Violation(n.id, rid, msg))

            for sec in _csv(r.get("require_sections")):
                if not any(sec.lower() in s.lower() for s in n.sections):
                    out.append(Violation(n.id, rid, f"{msg} (missing '## {sec}')"))

            # The only key that says what a type must NOT be. Without it a graph could
            # demand a hypothesis carry a claim and never stop it carrying the run and
            # the result too, which is how a loop loses its stages.
            if wrong := [f for f in _csv(r.get("forbid_fields")) if f in n.frontmatter]:
                out.append(Violation(n.id, rid, f"{msg} (remove: {', '.join(wrong)})"))

            if fld := r.get("require_field"):
                got = n.frontmatter.get(fld)
                if got is None or not str(got).strip():
                    out.append(Violation(n.id, rid, f"{msg} ({fld} is {got!r})"))

            if (fld := r.get("require_result")) and fld not in n.results:
                out.append(Violation(n.id, rid, msg))

            # The two checks that look past the node being checked. Outgoing asks what a
            # claim rests on, which fails the day that claim dies; incoming asks what the
            # graph did NEXT, the only way to police work that was abandoned.
            for key, edges in (("require_edge_target", n.links),
                               ("require_backlink", n.backlinks)):
                if spec := r.get(key):
                    hits, least = _matching(spec, edges, nodes), spec.get("min", 1)
                    if hits < least:
                        want = ", ".join(f"{k}={v}" for k, v in spec.items() if k != "min")
                        out.append(Violation(n.id, rid, f"{msg} ({want} -> {hits} "
                                                        f"matching, need >= {least})"))

            for fld, allowed in (r.get("require_field_one_of") or {}).items():
                got = n.frontmatter.get(fld)
                if got is None or str(got) not in {str(a) for a in allowed}:
                    out.append(Violation(n.id, rid,
                                         f"{msg} ({fld}={got!r}, one of: "
                                         f"{', '.join(map(str, allowed))})"))

            # `_blocks` already reported a non-mapping; skip rather than crash on it
            # in the same pass.
            for key, floor in ((r.get("require_result_min") or {}).items()
                               if isinstance(n.results, dict) else []):
                got = n.results.get(key)
                # `bool` is a subclass of `int`: `accuracy: true` would otherwise sail
                # through a floor of 0.8 as the number 1.
                numeric = isinstance(got, (int, float)) and not isinstance(got, bool)
                if not numeric or got < floor:
                    out.append(Violation(n.id, rid, f"{msg} ({key}={got!r}, need >= {floor})"))
    return out

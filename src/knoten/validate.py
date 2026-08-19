"""The rules engine.

The core knows NOTHING about any domain — every rule comes from the graph's own
`graph.yaml`, so a trading graph and a biology graph share this code unchanged.

A rule this engine cannot understand is a hard error, never a no-op: a rule that
silently enforces nothing is worse than no rule, because you believe you are covered.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .core import GATE_TYPE, GENERATED, INVERSE, GraphError, Node, _yaml

# A rule key that is not in here is a typo. Refuse it.
RULE_KEYS = {
    "id",                  # required
    "message",             # what the human sees when it fires
    "when_status",         # only apply to these statuses
    "when_type",           # only apply to these node types
    "require_edge",        # node must declare this relation
    "require_sections",    # body must contain these `## ` headings
    "require_result",      # results must carry this key
    "require_result_min",  # {key: minimum} — numeric floor
    "require_field_one_of",  # {field: [allowed]} — closed vocabulary
    "require_edge_target",   # {rel, type, status, min} — what the edge must POINT AT
    "require_backlink",      # same shape, read from the other side: what must point AT
                             # this node. `rel` is the GENERATED inverse, e.g. kn:testedBy
}

# Same for the top level. `node_type:` (singular) would be the next silent no-op.
GRAPH_KEYS = {"name", "description", "node_types", "statuses", "tags", "rules"}

# `GATE_TYPE` was this until the rename. Named only so the migration check below can
# recognise a graph that predates it; nothing else in the package may use it.
RENAMED_GATE_TYPE = "method"
GATE_RELS = ("kn:survivedGate", "kn:killedByGate")


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
    # `node_types` may also be a MAPPING of type -> what that word means in this graph.
    # Membership is checked against the keys either way — `in` and iteration over a dict
    # give exactly that — so nothing downstream changes. The values are for the reader and
    # for `knoten viz`, because knoten defines none of these words itself.
    if isinstance(cfg.get("node_types"), dict):
        for k, v in cfg["node_types"].items():
            if not isinstance(v, str) or not v.strip():
                raise GraphError(
                    f"graph.yaml: `node_types` entry '{k}' must be a one-line meaning, "
                    f"got {v!r}. Use a plain list if you do not want to write meanings.")

    for key in ("node_types", "statuses", "tags"):
        if key in cfg and not isinstance(cfg[key], (list, dict) if key == "node_types"
                                         else list):
            raise GraphError(f"graph.yaml: `{key}` must be a list, got {cfg[key]!r}")

    rules = cfg.get("rules") or []
    if not isinstance(rules, list):
        raise GraphError("graph.yaml: `rules` must be a list")
    for r in rules:
        if not isinstance(r, dict):
            raise GraphError(f"graph.yaml: each rule must be a mapping, got {r!r}")
        if "id" not in r:
            raise GraphError(f"graph.yaml: rule is missing `id`: {r!r}")
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
    for key in ("require_edge", "require_result"):
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
    # against opposite halves of INVERSE. Getting that backwards is the mistake worth
    # catching: a rule naming a relation nothing on that side can carry loads cleanly and
    # then fails every node forever, with nothing saying the direction was wrong.
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


def _tags(n: Node, cfg: dict) -> list[Violation]:
    """Tags are the filter axis: they narrow a graph too big to read into a slice an
    agent can take in one call. A typo'd tag is therefore not cosmetic — the node is
    still in the graph but outside every filtered view of it, which is the same silent
    disappearance as a typo'd status."""
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
    """`results:` and `repro:` must be mappings.

    `results: 5` used to reach `n.results.get(key)` in the require_result_min loop and come
    back as `AttributeError: 'int' object has no attribute 'get'` — a traceback out of the
    validator whose whole job is refusing bad nodes politely. `parse_text` keeps whatever
    the YAML held, so the check belongs here rather than in the parser: a scalar is a
    malformed node, not an unparseable one.
    """
    return [Violation(n.id, f"malformed-{name}",
                      f"`{name}` must be a mapping of key: value, got "
                      f"{type(raw).__name__} ({raw!r})")
            for name in ("results", "repro")
            if (raw := n.frontmatter.get(name)) is not None and not isinstance(raw, dict)]


def _vocabulary(n: Node, cfg: dict) -> list[Violation]:
    """A node's `type` and `status` must be words THIS graph declared.

    The core invents no vocabulary: declare no `node_types` and none is checked. But a
    graph that DOES declare one has said those are the only legal words — and a claim with
    a typo'd (or missing) status silently drops out of every query, which filters on the
    known set.
    """
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


def _structural(nodes: dict[str, Node], root: Path, cfg: dict) -> list[Violation]:
    """Checks the core ALWAYS runs. Structural, not domain."""
    out = []
    ids = set(nodes)
    for nid, n in nodes.items():
        # The real id is the filename; `id:` in the frontmatter is decorative. A node
        # whose `id:` says something else lies about itself to every human reading it
        # while every query still resolves it by its filename.
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

    # MIGRATION AID, and deliberately narrow. `GATE_TYPE` used to be "method"; a graph
    # written before the rename keeps `type: method` and `knoten gates` then returns one
    # fewer row while saying nothing.
    #
    # It fires ONLY on the dead word. A graph that calls its bar `criterion` is not
    # wrong — core.py promises such a graph "an empty bucket, not a wrong answer" — and
    # flagging it would be the core inventing vocabulary, which is the one thing this
    # project does not do. Delete this check once graphs have moved.
    stale = {l["to"] for n in nodes.values() for l in n.links
             if l["rel"] in GATE_RELS and nodes.get(l["to"]) is not None
             and nodes[l["to"]].type == RENAMED_GATE_TYPE}
    for nid in sorted(stale):
        out.append(Violation(nid, "not-a-gate",
                             f"cited as a gate but typed '{RENAMED_GATE_TYPE}' — the type "
                             f"was renamed to '{GATE_TYPE}', and `knoten gates` only finds "
                             f"that, so this node is invisible to it"))
    return out


def _csv(v) -> list[str]:
    if not v:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [x.strip() for x in str(v).split(",") if x.strip()]


def _matching(spec: dict, edges: list, nodes: dict) -> int:
    """How many DISTINCT nodes on the other end of these edges match `spec`.

    Distinct, not one per edge: `min: 3` states an inductive standard, and listing one
    finding three times is not three observations. A target that does not exist is not
    counted — it is already reported as `dangling-edge`, and a typo must not stand in for
    evidence.
    """
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
            rid, msg = r["id"], str(r.get("message", r["id"])).strip()

            if (rel := r.get("require_edge")) and rel not in n.rels():
                out.append(Violation(n.id, rid, msg))

            for sec in _csv(r.get("require_sections")):
                if not any(sec.lower() in s.lower() for s in n.sections):
                    out.append(Violation(n.id, rid, f"{msg} (missing '## {sec}')"))

            if (fld := r.get("require_result")) and fld not in n.results:
                out.append(Violation(n.id, rid, msg))

            # The only check that looks past the node it is checking. `require_edge`
            # asks whether an edge exists; this asks what is on the other end — which is
            # what makes a claim's dependants fail the day the claim it rests on dies.
            # The two checks that look past the node being checked. Outgoing asks what a
            # claim rests on — which is what fails the day that claim dies. Incoming asks
            # what the graph did NEXT, the only way to police work that was abandoned
            # rather than written badly.
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

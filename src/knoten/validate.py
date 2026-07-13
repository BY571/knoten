"""The rules engine.

The core knows NOTHING about any domain — every rule comes from the graph's own
`graph.yaml`, so a trading graph and a biology graph share this code unchanged.

A rule this engine cannot understand is a hard error, never a no-op: a rule that
silently enforces nothing is worse than no rule, because you believe you are covered.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .core import GENERATED, INVERSE, GraphError, Node, _yaml

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
}

# Same for the top level. `node_type:` (singular) would be the next silent no-op.
GRAPH_KEYS = {"name", "description", "node_types", "statuses", "rules"}


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
    for key in ("node_types", "statuses"):
        if key in cfg and not isinstance(cfg[key], list):
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
        out += _vocabulary(n, cfg)
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
    return out


def _csv(v) -> list[str]:
    if not v:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [x.strip() for x in str(v).split(",") if x.strip()]


def _applies(n: Node, r: dict) -> bool:
    if (st := _csv(r.get("when_status"))) and n.status not in st:
        return False
    if (ty := _csv(r.get("when_type"))) and n.type not in ty:
        return False
    return True


def check(nodes: dict[str, Node], root: Path) -> list[Violation]:
    cfg = load_config(root)
    out = _structural(nodes, root, cfg)

    for n in nodes.values():
        for r in cfg.get("rules", []):
            if not _applies(n, r):
                continue
            rid, msg = r["id"], str(r.get("message", r["id"])).strip()

            if (rel := r.get("require_edge")) and rel not in n.rels():
                out.append(Violation(n.id, rid, msg))

            for sec in _csv(r.get("require_sections")):
                if not any(sec.lower() in s.lower() for s in n.sections):
                    out.append(Violation(n.id, rid, f"{msg} (missing '## {sec}')"))

            if (fld := r.get("require_result")) and fld not in n.results:
                out.append(Violation(n.id, rid, msg))

            for key, floor in (r.get("require_result_min") or {}).items():
                got = n.results.get(key)
                # `bool` is a subclass of `int`: `accuracy: true` would otherwise sail
                # through a floor of 0.8 as the number 1.
                numeric = isinstance(got, (int, float)) and not isinstance(got, bool)
                if not numeric or got < floor:
                    out.append(Violation(n.id, rid, f"{msg} ({key}={got!r}, need >= {floor})"))
    return out

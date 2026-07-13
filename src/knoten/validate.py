"""The rules engine.

The core knows NOTHING about any domain. Every rule comes from the graph's own
`graph.yaml`. A trading graph and a biology graph declare entirely different rules
and share this code unchanged.

Rules are the point. A knowledge base without enforcement decays into a wiki — which
is the documented failure mode this tool exists to prevent. So a rule this engine
cannot understand is a hard error, never a no-op: a rule that silently enforces
nothing is worse than no rule at all, because you believe you are covered.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .core import GENERATED, INVERSE, GraphError, Node, _Loader

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
    "if_result_any",       # only apply require_result when one of these is present
}


@dataclass
class Violation:
    node: str
    rule: str
    message: str


def load_rules(root: Path) -> list[dict]:
    f = root / "graph.yaml"
    if not f.exists():
        return []
    try:
        cfg = yaml.load(f.read_text(encoding="utf-8"), Loader=_Loader) or {}
    except yaml.YAMLError as e:
        raise GraphError(f"graph.yaml: invalid YAML — {e}") from e

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
    return rules


def _structural(nodes: dict[str, Node], root: Path) -> list[Violation]:
    """Checks the core ALWAYS runs. Structural, not domain."""
    out = []
    ids = set(nodes)
    for nid, n in nodes.items():
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
    out = _structural(nodes, root)
    rules = load_rules(root)

    for n in nodes.values():
        for r in rules:
            if not _applies(n, r):
                continue
            rid, msg = r["id"], str(r.get("message", r["id"])).strip()

            if (rel := r.get("require_edge")) and rel not in n.rels():
                out.append(Violation(n.id, rid, msg))

            for sec in _csv(r.get("require_sections")):
                if not any(sec.lower() in s.lower() for s in n.sections):
                    out.append(Violation(n.id, rid, f"{msg} (missing '## {sec}')"))

            if fld := r.get("require_result"):
                trig = _csv(r.get("if_result_any"))
                if (not trig or any(t in n.results for t in trig)) and fld not in n.results:
                    out.append(Violation(n.id, rid, msg))

            for key, floor in (r.get("require_result_min") or {}).items():
                got = n.results.get(key)
                if not isinstance(got, (int, float)) or got < floor:
                    out.append(Violation(n.id, rid, f"{msg} ({key}={got!r}, need >= {floor})"))
    return out

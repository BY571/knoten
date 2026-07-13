"""The rules engine.

The core knows NOTHING about any domain. Every rule comes from the graph's own
`graph.yaml`. A trading graph and a biology graph declare entirely different rules
and share this code unchanged.

Rules are the point. A knowledge base without enforcement decays into a wiki — which
is the documented failure mode this tool exists to prevent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .core import Node


@dataclass
class Violation:
    node: str
    rule: str
    message: str


def _load_rules(root: Path) -> list[dict]:
    """Minimal YAML subset — enough for rules, no dependency."""
    f = root / "graph.yaml"
    if not f.exists():
        return []
    rules, cur = [], None
    in_rules = False
    for line in f.read_text().splitlines():
        if re.match(r"^rules:", line):
            in_rules = True
            continue
        if in_rules and re.match(r"^\w", line):
            break
        if not in_rules:
            continue
        if m := re.match(r"\s*-\s*id:\s*(\S+)", line):
            cur = {"id": m.group(1)}
            rules.append(cur)
        elif cur is not None and (m := re.match(r"\s+(\w+):\s*(.*)$", line)):
            cur[m.group(1)] = m.group(2).strip()
    return rules


# Built-in checks the core ALWAYS runs (structural, not domain).
def _structural(nodes: dict[str, Node], root: Path) -> list[Violation]:
    out = []
    ids = set(nodes)
    for nid, n in nodes.items():
        for l in n.links:
            if l["to"] not in ids:
                out.append(Violation(nid, "dangling-edge",
                                     f"-> {l['to']} ({l['rel']}) does not exist"))
        for a in n.attachments:
            if not (root / "attachments" / nid / a).exists():
                out.append(Violation(nid, "missing-attachment",
                                     f"'{a}' is listed but not in attachments/{nid}/"))
    return out


# Rule predicates the graph can invoke by name. Domain-agnostic primitives.
def _has_edge(n: Node, rel: str) -> bool:
    return rel in n.rels()


def _has_section(n: Node, needle: str) -> bool:
    return any(needle.lower() in s.lower() for s in n.sections)


def _has_result(n: Node, key: str) -> bool:
    return key in n.results


def check(nodes: dict[str, Node], root: Path) -> list[Violation]:
    out = _structural(nodes, root)
    rules = _load_rules(root)

    for n in nodes.values():
        for r in rules:
            rid = r.get("id", "?")
            msg = r.get("message", rid)
            when_status = _csv(r.get("when_status", ""))
            when_type = _csv(r.get("when_type", ""))
            if when_status and n.status not in when_status:
                continue
            if when_type and n.type not in when_type:
                continue

            if rel := r.get("require_edge"):
                if not _has_edge(n, rel):
                    out.append(Violation(n.id, rid, msg))
            for sec in _csv(r.get("require_sections", "")):
                if not _has_section(n, sec):
                    out.append(Violation(n.id, rid, f"{msg} (missing '## {sec}')"))
            if fld := r.get("require_result"):
                trig = _csv(r.get("if_result_any", ""))
                if (not trig or any(_has_result(n, t) for t in trig)) \
                        and not _has_result(n, fld):
                    out.append(Violation(n.id, rid, msg))
    return out


def _csv(v) -> list[str]:
    if not v:
        return []
    return [x.strip().strip("[]'\"") for x in str(v).split(",") if x.strip()]

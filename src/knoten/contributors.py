"""Who may write to a graph, as data beside the rules.

`contributors.yaml` sits next to `graph.yaml` and is rewritten by knoten on every join
and revoke. It is a separate file for one reason: `graph.yaml` carries the comments
people keep next to their rules, and a YAML round-trip strips them. Nothing here is
trusted because a server said so; every entry is checked against a signature.

An entry: `name: {key: "ssh-ed25519 ...", role: read|write|admin, invited_by?: name,
invite?: {blob: str, sig: str}, revoked?: YYYY-MM-DD}`. Revocation is a mark, not a
deletion: history signed by that key stays attributable.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from .core import GraphError, ID_RE, write_atomic
from .keys import INVITE_NS, allowed_signers, verify

FILE = "contributors.yaml"
ROLES = ("read", "write", "admin")
MAX_NAME = 64


def load(root: Path) -> dict | None:
    """None when the file is absent: that is a phase-1 graph, and it stays unsigned."""
    p = Path(root) / FILE
    if not p.exists():
        return None
    return parse(p.read_text(encoding="utf-8"), str(p))


def parse(text: str, label: str = FILE) -> dict:
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise GraphError(f"{label}: invalid YAML: {e}") from None
    if not isinstance(raw, dict):
        raise GraphError(f"{label}: expected a mapping of name to entry")
    out = {}
    for name, entry in raw.items():
        name = str(name)
        if not ID_RE.match(name) or len(name) > MAX_NAME:
            raise GraphError(f"{label}: '{name}' is not a valid contributor name")
        if not isinstance(entry, dict):
            raise GraphError(f"{label}: '{name}' must be a mapping with key and role")
        key = str(entry.get("key", "")).strip()
        if len(key.split()) != 2 or not key.startswith("ssh-ed25519 "):
            raise GraphError(f"{label}: '{name}' needs a key of the form 'ssh-ed25519 AAAA...'")
        role = entry.get("role")
        if role not in ROLES:
            raise GraphError(f"{label}: '{name}' role must be one of {', '.join(ROLES)}")
        clean = {"key": key, "role": role}
        for opt in ("invited_by", "invite", "revoked"):
            if opt in entry:
                clean[opt] = entry[opt]
        out[name] = clean
    return out


def dump(root: Path, contribs: dict) -> None:
    text = yaml.safe_dump(dict(sorted(contribs.items())), sort_keys=True,
                          default_flow_style=False)
    write_atomic(Path(root) / FILE, text)


def active(contribs: dict) -> dict:
    return {n: e for n, e in contribs.items() if not e.get("revoked")}


def keys(contribs: dict, roles: tuple[str, ...] = ("write", "admin")) -> dict[str, str]:
    """name -> public line, for those who may sign a commit here."""
    return {n: e["key"] for n, e in active(contribs).items() if e["role"] in roles}


def admins(contribs: dict) -> dict[str, str]:
    return keys(contribs, ("admin",))


def diff(prev: dict | None, cur: dict) -> tuple[dict, dict, set]:
    """(added, changed, removed) between two versions. `changed` holds the new entry."""
    prev = prev or {}
    added = {n: e for n, e in cur.items() if n not in prev}
    changed = {n: e for n, e in cur.items() if n in prev and prev[n] != e}
    removed = {n for n in prev if n not in cur}
    return added, changed, removed


# ---------------------------------------------------------------- invites

def invite_blob(graph: str, name: str, role: str, expires: str, nonce: str) -> bytes:
    """Canonical bytes: sorted keys, no whitespace. The same five fields always serialise
    the same way, so a signature made on one machine verifies on another."""
    return json.dumps({"expires": expires, "graph": graph, "name": name, "nonce": nonce,
                       "role": role}, sort_keys=True, separators=(",", ":")).encode()


def parse_blob(blob: bytes) -> dict:
    try:
        d = json.loads(blob)
    except (ValueError, UnicodeDecodeError):
        raise GraphError("that invite is malformed") from None
    if not isinstance(d, dict) or set(d) != {"expires", "graph", "name", "nonce", "role"}:
        raise GraphError("that invite is malformed")
    return d


def check_blob(d: dict, graph: str, name: str, role: str) -> None:
    """The signed fields must say exactly what the entry claims. An invite for maria as
    write is not an invite for eve, nor for maria as admin, nor for another graph."""
    if (d["graph"], d["name"], d["role"]) != (graph, name, role):
        raise GraphError("that invite was issued for a different name, role or graph")


def verify_invite(contribs: dict, blob: bytes, sig: str) -> str:
    """The admin who signed `blob`, or a refusal. Only ACTIVE admins count: a revoked
    admin's old key is still on record, for attribution, but authorises nothing."""
    for name, key in admins(contribs).items():
        if verify(allowed_signers({name: key}, (INVITE_NS,)), name, blob, sig, INVITE_NS):
            return name
    raise GraphError("that invite is not signed by an admin of this graph")

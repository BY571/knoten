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
import re
from pathlib import Path

import yaml

from .core import GraphError, ID_RE, MAX_NAME, write_atomic
from .keys import INVITE_NS, allowed_signers, verify

FILE = "contributors.yaml"
ROLES = ("read", "write", "admin")

_REVOKED_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")   # `\Z`, not `$`: `$` lets a trailing newline through


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
                if opt == "revoked":
                    # YAML 1.1 loader turns an unquoted date into a date object. Coerce to
                    # string so two loads of the same file then compare equal to a string
                    # written by today(), diff() reports no spurious changes, and json.dumps
                    # does not crash.
                    revoked = str(entry[opt])
                    if not _REVOKED_RE.match(revoked):
                        raise GraphError(f"{label}: '{name}' revoked must be a date YYYY-MM-DD")
                    clean[opt] = revoked
                else:
                    clean[opt] = entry[opt]
        out[name] = clean
    seen: dict[str, str] = {}
    for name in sorted(out):
        # The constitution authorises writes by NAME ("maria may write"), not by key --
        # `%GS` reports the signer's git-config name, and a key shared between two entries
        # lets one contributor sign a commit that is attributed, and authorised, as the
        # other. One key must map to exactly one name.
        key = out[name]["key"]
        if key in seen:
            a, b = seen[key], name
            raise GraphError(f"{label}: the same key is listed under '{a}' and '{b}'; "
                             f"one key, one name")
        seen[key] = name
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
    the same way, so a signature made on one machine verifies on another.

    Which field is enforced where, because it is not the same answer for all five.
    `graph`, `name` and `role` are checked twice, against what the entry claims: by the
    server at `/invite` (against HEAD) and again by the gate at the join commit (against
    the parent). `expires` is enforced by nothing that reads THIS blob -- the server
    checks its OWN invite record's expiry at `/join`, and the gate cannot, because a
    commit's timestamp is chosen by whoever made it. `nonce` is checked nowhere at all;
    it only keeps two invites for the same name and role from being the same bytes, and
    therefore the same signature."""
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

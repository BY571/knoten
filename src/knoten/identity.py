"""Who may write to a graph, and the keys that prove it.

`contributors.yaml` sits next to `graph.yaml` and is rewritten by knoten on every join and
revoke. A separate file because `graph.yaml` carries the comments people keep next to
their rules, and a YAML round-trip strips them. Nothing here is trusted because a server
said so; every entry is checked against a signature.

An entry: `name: {key: "ssh-ed25519 ...", role: read|write|admin, invited_by?: name,
invite?: {blob: str, sig: str}, revoked?: YYYY-MM-DD}`. Revocation is a mark, not a
deletion: history signed by that key stays attributable.

A signing key proves who wrote a commit or an invite. It grants access to nothing: it is
not an SSH login key and is never installed on any machine but its owner's. The SSH key
format is used only because git verifies it natively (`gpg.format=ssh`) and `ssh-keygen
-Y` signs arbitrary bytes with it, so one key covers commits and invites.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import yaml

from .core import GraphError, ID_RE, MAX_NAME, write_atomic

FILE = "contributors.yaml"
ROLES = ("read", "write", "admin")
INVITE_NS = "knoten-invite"        # commits use git's own namespace, "git"

_REVOKED_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")   # `\Z`, not `$`: `$` lets a trailing newline through


# ---------------------------------------------------------------- keys

def key_dir() -> Path:
    return Path(os.environ.get("KNOTEN_KEYS") or Path.home() / ".config" / "knoten" / "keys")


def _keygen(*args: str, **kw) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["ssh-keygen", *args], capture_output=True, **kw)
    except FileNotFoundError:
        raise GraphError("ssh-keygen is not installed; it is what signs and verifies") from None


def ensure_key(name: str) -> Path:
    """The private key for `name`, generated on first use. NEVER regenerated: every
    contributors.yaml listing the public half would then refuse this person's commits,
    with a message that blames their signature."""
    if not ID_RE.match(name or ""):
        raise GraphError(f"'{name}' is not a valid contributor name (use kebab-case)")
    if len(name) > MAX_NAME:
        # The name becomes a filename under the key dir, exactly like a graph name becomes
        # a directory under the data dir: bounded in the same place, for the same reason.
        raise GraphError(f"contributor name is too long (max {MAX_NAME} characters)")
    d = key_dir()
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    priv = d / name
    if not priv.exists():
        r = _keygen("-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(priv), text=True)
        if r.returncode != 0:
            raise GraphError(f"could not generate a signing key: {r.stderr.strip()}")
    return priv


def public_line(priv: Path) -> str:
    """`ssh-ed25519 AAAA...`: the two fields contributors.yaml and allowed-signers take.
    The comment field is dropped; it is not part of the key."""
    return " ".join(priv.with_suffix(".pub").read_text(encoding="utf-8").split()[:2])


def sign(priv: Path, data: bytes, namespace: str) -> str:
    """An armoured SSHSIG over `data`, bound to `namespace` so a signature made for one
    purpose cannot be replayed as another."""
    with tempfile.TemporaryDirectory() as d:
        blob = Path(d) / "blob"
        blob.write_bytes(data)
        r = _keygen("-Y", "sign", "-f", str(priv), "-n", namespace, str(blob), text=True)
        if r.returncode != 0:
            raise GraphError(f"signing failed: {r.stderr.strip()}")
        return blob.with_suffix(".sig").read_text(encoding="utf-8")


def verify(allowed: str, identity: str, data: bytes, sig: str, namespace: str) -> bool:
    """True only if `sig` was made over `data`, in `namespace`, by the key that `allowed`
    (allowed-signers text) maps to `identity`. Never raises: a verifier that throws on
    garbage is a verifier an attacker can crash."""
    if not allowed or not sig or not identity:
        return False
    with tempfile.TemporaryDirectory() as d:
        a, s = Path(d) / "allowed", Path(d) / "sig"
        a.write_text(allowed, encoding="utf-8")
        s.write_text(sig, encoding="utf-8")
        try:
            r = _keygen("-Y", "verify", "-f", str(a), "-I", identity, "-n", namespace,
                        "-s", str(s), input=data)
        except GraphError:
            return False
        return r.returncode == 0


def allowed_signers(keys: dict[str, str], namespaces: tuple[str, ...] = ("git", INVITE_NS)) -> str:
    """The file format both `git verify-commit` and `ssh-keygen -Y verify` read:
    one `name namespaces="..." <public line>` per contributor."""
    ns = ",".join(namespaces)
    return "".join(f'{name} namespaces="{ns}" {key}\n' for name, key in sorted(keys.items()))


def configure_signing(repo: Path, priv: Path) -> None:
    """Make every commit in this clone signed with `priv`, so a contributor never has to
    remember `-S`. The PRIVATE path: that is git's form for ssh signing without an agent."""
    for key, value in (("gpg.format", "ssh"), ("user.signingkey", str(priv)),
                       ("commit.gpgsign", "true")):
        r = subprocess.run(["git", "-C", str(repo), "config", key, value], capture_output=True)
        if r.returncode != 0:
            # check=True raised CalledProcessError, which escapes the CLI's GraphError
            # handler as a traceback. A read-only .git/config is ordinary user trouble
            # and owes them one line.
            raise GraphError(f"could not configure signing in {repo}: "
                             f"{r.stderr.decode(errors='replace').strip() or f'git config {key} failed'}")


# ---------------------------------------------------------------- contributors

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
    """Canonical bytes: sorted keys, no whitespace, so a signature made on one machine
    verifies on another.

    Where each field is enforced, because it differs. `graph`, `name` and `role` are
    checked twice against what the entry claims: by the server at `/invite` (against HEAD)
    and by the gate at the join commit (against the parent). `expires` is enforced by
    nothing that reads THIS blob -- the server checks its own invite record at `/join`,
    and the gate cannot, since a commit's timestamp is chosen by whoever made it. `nonce`
    is checked nowhere: it only keeps two invites for one name and role from being the
    same bytes, and therefore the same signature."""
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

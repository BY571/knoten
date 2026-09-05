"""Held signing keys.

A signing key proves who wrote a commit or an invite. It grants access to nothing: it is
not an SSH login key and is never installed on any machine but its owner's. The SSH key
format is used only because git verifies that format natively (`gpg.format=ssh`) and
`ssh-keygen -Y` signs arbitrary bytes with it, so one key covers commits and invites and
no second tool is needed.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from .core import GraphError, ID_RE, MAX_NAME

INVITE_NS = "knoten-invite"        # commits use git's own namespace, "git"


def key_dir() -> Path:
    return Path(os.environ.get("KNOTEN_KEYS") or Path.home() / ".config" / "knoten" / "keys")


def _keygen(*args: str, **kw) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["ssh-keygen", *args], capture_output=True, **kw)
    except FileNotFoundError:
        raise GraphError("ssh-keygen is not installed; it is what signs and verifies") from None


def ensure_key(name: str) -> Path:
    """The private key for `name`, generated on first use.

    Never regenerated: every contributors.yaml that lists the public half would then
    refuse this person's commits, with a message that blames their signature."""
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
    """`ssh-ed25519 AAAA...`: the two fields that go into contributors.yaml and into an
    allowed-signers line. The comment field is dropped; it is not part of the key."""
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

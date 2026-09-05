"""What a server knows that the graph does not.

A knoten server hosts graphs, and for each one it holds exactly three things the graph
itself cannot: who may connect (tokens), who has been invited but not yet arrived
(invites), and who owns the server (one secret). Everything about the graph's meaning
stays in the graph. Losing this directory loses availability, not the answer to "who
verified this".

Files, not a database, under the same lock the graph uses for its own read-modify-write
windows. Tokens and invite codes are stored hashed: a leaked tokens.json yields nothing.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .core import GraphError, ID_RE, graph_lock, write_atomic
from .hook import install_server

ROLES = ("read", "write", "admin")


def _hash(secret: str) -> str:
    return hashlib.sha256((secret or "").encode()).hexdigest()


# Every secret below that a human ever passes on a command line (owner_secret,
# invite codes) is generated with token_hex, not token_urlsafe: token_urlsafe's
# alphabet includes '-', and a secret that begins with '-' reads to argparse as
# a flag, not a value — `knoten remote create ... --owner-secret <secret>` then
# failed one run in five with a perfectly valid secret. mint()'s tokens travel
# only as a git HTTP password, never argv, so they keep token_urlsafe.


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Registry:
    def __init__(self, data: Path):
        self.data = Path(data)
        (self.data / "graphs").mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- owner

    def owner_secret(self) -> str:
        """Created on first call, 0600, printed once by `knoten serve` and never again."""
        p = self.data / "owner"
        if not p.exists():
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(secrets.token_hex(32))
        return p.read_text(encoding="utf-8").strip()

    def check_owner(self, secret: str) -> bool:
        return hmac.compare_digest(secret or "", self.owner_secret())

    # ---------------------------------------------------------------- graphs

    def graph_dir(self, name: str) -> Path:
        # A name becomes a directory. Checked here, at the ONE place every path is made,
        # so `../etc` cannot reach mkdir through any entry point.
        if not ID_RE.match(name or ""):
            raise GraphError(f"'{name}' is not a valid graph name (use kebab-case: my-topic)")
        return self.data / "graphs" / name

    def exists(self, name: str) -> bool:
        return (self.graph_dir(name) / "repo.git").is_dir()

    def repo(self, name: str) -> Path:
        r = self.graph_dir(name) / "repo.git"
        if not r.is_dir():
            raise GraphError(f"no graph '{name}' on this server")
        return r

    def create(self, name: str, admin: str) -> str:
        """A bare repo with the gate already installed. Returns the creating admin's
        token: creating a graph and being able to push to it are one act."""
        d = self.graph_dir(name)
        if self.exists(name):
            raise GraphError(f"graph '{name}' already exists on this server")
        try:
            d.mkdir(parents=True, exist_ok=True)
            repo = d / "repo.git"
            subprocess.run(["git", "init", "-q", "--bare", str(repo)], check=True)
            for key, value in (("http.receivepack", "true"),
                               ("receive.maxInputSize", "104857600")):   # 100 MB per push
                subprocess.run(["git", "-C", str(repo), "config", key, value], check=True)
            install_server(repo)
            return self.mint(name, admin, "admin")
        except GraphError:
            # A half-made repo that exists() calls valid would accept pushes with no gate, forever.
            shutil.rmtree(d, ignore_errors=True)
            raise
        except Exception as e:
            # A half-made repo that exists() calls valid would accept pushes with no gate, forever.
            shutil.rmtree(d, ignore_errors=True)
            raise GraphError(f"could not create graph '{name}': {e}") from e

    # ---------------------------------------------------------------- files

    def _read(self, name: str, file: str) -> dict:
        p = self.graph_dir(name) / file
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def _write(self, name: str, file: str, obj: dict) -> None:
        write_atomic(self.graph_dir(name) / file, json.dumps(obj, indent=1) + "\n")

    # ---------------------------------------------------------------- tokens

    def _check(self, name: str, user: str, role: str) -> None:
        if role not in ROLES:
            raise GraphError(f"role must be one of {', '.join(ROLES)}, not '{role}'")
        if not ID_RE.match(user or ""):
            raise GraphError(f"'{user}' is not a valid contributor name (use kebab-case)")
        self.repo(name)

    def mint(self, name: str, user: str, role: str) -> str:
        self._check(name, user, role)
        token = secrets.token_urlsafe(32)
        with graph_lock(self.graph_dir(name)):
            tokens = self._read(name, "tokens.json")
            tokens[user] = {"hash": _hash(token), "role": role}
            self._write(name, "tokens.json", tokens)
        return token

    def authenticate(self, name: str, user: str, token: str) -> str | None:
        """The role this token grants on this graph, or None. Never raises: an unknown
        graph and a wrong token look identical to the caller, so the server does not
        leak which graphs exist."""
        if not ID_RE.match(name or "") or not self.exists(name):
            return None
        entry = self._read(name, "tokens.json").get(user or "")
        if not entry or not hmac.compare_digest(entry["hash"], _hash(token)):
            return None
        return entry["role"]

    def revoke(self, name: str, user: str) -> None:
        self.repo(name)
        with graph_lock(self.graph_dir(name)):
            tokens = self._read(name, "tokens.json")
            if user not in tokens:
                raise GraphError(f"no contributor '{user}' on graph '{name}'")
            del tokens[user]
            self._write(name, "tokens.json", tokens)

    # ---------------------------------------------------------------- invites

    def invite(self, name: str, user: str, role: str, days: int = 7) -> str:
        self._check(name, user, role)
        code = secrets.token_hex(16)
        expires = (_now() + timedelta(days=days)).isoformat()
        with graph_lock(self.graph_dir(name)):
            invites = self._read(name, "invites.json")
            invites[_hash(code)] = {"name": user, "role": role, "expires": expires}
            self._write(name, "invites.json", invites)
        return code

    def redeem(self, name: str, code: str) -> tuple[str, str, str]:
        """One use. Returns (user, role, token). The code is removed on first try
        whether or not it was still live, so an expired code cannot be retried."""
        self.repo(name)
        with graph_lock(self.graph_dir(name)):
            invites = self._read(name, "invites.json")
            entry = invites.pop(_hash(code), None)
            if entry is None:
                raise GraphError("that invite code is not valid for this graph")
            self._write(name, "invites.json", invites)
        if datetime.fromisoformat(entry["expires"]) < _now():
            raise GraphError("that invite has expired; ask the admin for a new one")
        # Outside the lock: mint takes it again, and flock on a fresh handle would wait
        # on our own lock forever.
        return entry["name"], entry["role"], self.mint(name, entry["name"], entry["role"])

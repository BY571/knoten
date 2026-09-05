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
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .core import GraphError, ID_RE, graph_lock, write_atomic
from .hook import install_server

ROLES = ("read", "write", "admin")


def _hash(secret: str) -> str:
    return hashlib.sha256((secret or "").encode()).hexdigest()


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
                fh.write(secrets.token_urlsafe(32))
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
        d.mkdir(parents=True, exist_ok=True)
        repo = d / "repo.git"
        subprocess.run(["git", "init", "-q", "--bare", str(repo)], check=True)
        for key, value in (("http.receivepack", "true"),
                           ("receive.maxInputSize", "104857600")):   # 100 MB per push
            subprocess.run(["git", "-C", str(repo), "config", key, value], check=True)
        install_server(repo)
        return self.mint(name, admin, "admin")

    # ---------------------------------------------------------------- files

    def _read(self, name: str, file: str) -> dict:
        p = self.graph_dir(name) / file
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def _write(self, name: str, file: str, obj: dict) -> None:
        write_atomic(self.graph_dir(name) / file, json.dumps(obj, indent=1) + "\n")

    # ---------------------------------------------------------------- tokens

    def mint(self, name: str, user: str, role: str) -> str:
        """Task 2 completes this. Kept minimal here so `create` has something to return."""
        token = secrets.token_urlsafe(32)
        with graph_lock(self.graph_dir(name)):
            tokens = self._read(name, "tokens.json")
            tokens[user] = {"hash": _hash(token), "role": role}
            self._write(name, "tokens.json", tokens)
        return token

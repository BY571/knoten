"""The client side of a remote graph.

A remote is a git URL plus a token. git already knows how to push, pull and clone over
HTTP and how to ask a helper program for credentials, so this module is that helper plus
a handful of commands that wrap git and make four JSON calls. It imports nothing from the
server: a client and a server never share code paths, so a bug in one cannot hide in the
other.

Tokens live in one file, mode 0600, one line per remote: `<host>/<path> <user> <token>`.
The owner secret for a server is stored under the bare host with user `owner`.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from .core import GraphError, ID_RE


# ---------------------------------------------------------------- credentials

def cred_path() -> Path:
    return Path(os.environ.get("KNOTEN_CREDENTIALS")
                or Path.home() / ".config" / "knoten" / "credentials")


def _key(url: str) -> str:
    u = urlsplit(url)
    return f"{u.netloc}{u.path}".rstrip("/")


def cred_store(url: str, user: str, secret: str) -> None:
    p = cred_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    key = _key(url)
    lines = [l for l in (p.read_text(encoding="utf-8").splitlines() if p.exists() else [])
             if not l.startswith(key + " ")]
    lines.append(f"{key} {user} {secret}")
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    # os.open's mode applies only when the file is created; existing files keep their bits.
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def cred_lookup(url: str) -> tuple[str, str] | None:
    p = cred_path()
    if not p.exists():
        return None
    key = _key(url)
    for line in p.read_text(encoding="utf-8").splitlines():
        parts = line.split(" ", 2)
        if len(parts) == 3 and parts[0] == key:
            return parts[1], parts[2]
    return None


def credential_helper(request: str) -> str:
    """git's credential protocol: key=value lines in, username and password out.

    Empty output for an unknown remote, never an error: git then falls through to its
    next helper, so every non-knoten remote on the machine keeps working.
    """
    fields = dict(line.split("=", 1) for line in request.splitlines() if "=" in line)
    url = f"{fields.get('protocol', 'https')}://{fields.get('host', '')}/{fields.get('path', '')}"
    found = cred_lookup(url)
    return f"username={found[0]}\npassword={found[1]}\n" if found else ""

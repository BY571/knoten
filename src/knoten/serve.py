"""One job per request: decide whether this token may do this, then hand the request to
git.

`git http-backend` ships with git and speaks the smart HTTP protocol as a CGI. It is what
nginx and Apache call to host git. knoten's server is the authorization layer in front
of it and nothing more: packfiles, refs, negotiation and the pre-receive gate are git's.

git's own protocol separates reading from writing by endpoint (`git-upload-pack` serves
data out, `git-receive-pack` takes it in), so "read access" is a token that never gets
past the door for receive-pack. There is no finer reasoning about git's data model here.

Binds localhost and speaks plain HTTP. TLS is a reverse proxy's or a tunnel's job.
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .core import GraphError
from .registry import Registry

GIT_RE = re.compile(r"^/([a-z0-9][a-z0-9_-]*)\.git(/.*)$")
API_RE = re.compile(r"^/([a-z0-9][a-z0-9_-]*)/(join|invite|revoke)$")
WRITE = "git-receive-pack"


def make_server(reg: Registry, host: str = "127.0.0.1", port: int = 8899) -> ThreadingHTTPServer:
    class Handler(_Handler):
        registry = reg
    return ThreadingHTTPServer((host, port), Handler)


class _Handler(BaseHTTPRequestHandler):
    registry: Registry

    def log_message(self, *args) -> None:      # one line per request is noise on a server
        pass

    # ---------------------------------------------------------------- helpers

    def _basic(self) -> tuple[str, str]:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return "", ""
        try:
            raw = base64.b64decode(header[6:]).decode()
        except Exception:
            return "", ""
        user, _, secret = raw.partition(":")
        return user, secret

    def _body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def _json_body(self) -> dict:
        try:
            return json.loads(self._body() or b"{}")
        except json.JSONDecodeError as e:
            raise GraphError(f"request body is not JSON: {e}") from None

    def _json(self, status: int, obj: dict) -> None:
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        if status == 401:
            self.send_header("WWW-Authenticate", 'Basic realm="knoten"')
        self.end_headers()
        self.wfile.write(data)

    def _refuse(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    # ---------------------------------------------------------------- routing

    def do_GET(self) -> None:
        self._route()

    def do_POST(self) -> None:
        self._route()

    def _route(self) -> None:
        path, _, query = self.path.partition("?")
        try:
            if m := GIT_RE.match(path):
                return self._git(m.group(1), m.group(2), query)
            if path == "/graphs" and self.command == "POST":
                return self._create()
            if (m := API_RE.match(path)) and self.command == "POST":
                return getattr(self, "_" + m.group(2))(m.group(1))
            self._refuse(404, "knoten: not found")
        except GraphError as e:
            self._refuse(400, f"knoten: {e}")

    # ---------------------------------------------------------------- git

    def _git(self, name: str, sub: str, query: str) -> None:
        user, token = self._basic()
        role = self.registry.authenticate(name, user, token)
        if role is None:
            # Same answer for "wrong token" and "no such graph": the server does not
            # confirm which graphs exist to someone who cannot open them.
            return self._refuse(401, "knoten: credentials required" if not token
                                else "knoten: that token is not valid here")
        if role == "read" and (WRITE in query or sub.endswith("/" + WRITE)):
            return self._refuse(403, f"knoten: {user} has read access to {name}, not write")

        env = {
            **os.environ,
            "GIT_PROJECT_ROOT": str(self.registry.graph_dir(name)),
            "GIT_HTTP_EXPORT_ALL": "1",
            "PATH_INFO": "/repo.git" + sub,          # the URL says <name>.git; disk says repo.git
            "QUERY_STRING": query,
            "REQUEST_METHOD": self.command,
            "REMOTE_USER": user,
            "REMOTE_ADDR": self.client_address[0],
            "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": self.headers.get("Content-Length", ""),
            "HTTP_CONTENT_ENCODING": self.headers.get("Content-Encoding", ""),
        }
        r = subprocess.run(["git", "http-backend"], input=self._body(),
                           capture_output=True, env=env)
        head, _, out = r.stdout.partition(b"\r\n\r\n")
        status, headers = 200, []
        for line in head.decode(errors="replace").splitlines():
            key, _, value = line.partition(":")
            if key.strip().lower() == "status":
                status = int(value.strip().split()[0])
            elif key.strip():
                headers.append((key.strip(), value.strip()))
        self.send_response(status)
        for key, value in headers:
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)
        if r.returncode != 0:
            print(f"knoten serve: git http-backend: {r.stderr.decode(errors='replace').strip()}",
                  file=sys.stderr)

    # ---------------------------------------------------------------- api (Task 4)

    def _create(self) -> None:
        self._refuse(404, "knoten: not found")

    def _join(self, name: str) -> None:
        self._refuse(404, "knoten: not found")

    def _invite(self, name: str) -> None:
        self._refuse(404, "knoten: not found")

    def _revoke(self, name: str) -> None:
        self._refuse(404, "knoten: not found")

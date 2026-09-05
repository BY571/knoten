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
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from .core import GraphError, MAX_PUSH_BYTES, SERVER_GIT_ENV
from .registry import Registry

GIT_RE = re.compile(r"^/([a-z0-9][a-z0-9_-]*)\.git(/.*)$")
API_RE = re.compile(r"^/([a-z0-9][a-z0-9_-]*)/(join|invites|invite|revoke)$")
WRITE = "git-receive-pack"

# What `git http-backend` is allowed to inherit from the shell `knoten serve` was started
# in. NOT os.environ wholesale: a GIT_DIR, GIT_COMMITTER_NAME or GIT_CONFIG_* left in the
# operator's shell reached the pre-receive hook, which runs against a tree an attacker
# chose -- GIT_DIR in particular pointed git at a repo nobody meant to touch.
KEEP_ENV = ("PATH", "HOME", "LANG", "TMPDIR")


def _pkt_lines(data: bytes):
    """The payload of each pkt-line in `data`: four hex digits of length, then that many
    bytes including the header. `0000` is a flush. Stops at the first thing that is not
    one rather than guessing."""
    i = 0
    while i + 4 <= len(data):
        try:
            size = int(data[i:i + 4], 16)
        except ValueError:
            return
        if size == 0:                          # flush-pkt
            i += 4
            continue
        if size < 4 or i + size > len(data):
            return
        yield data[i + 4:i + size]
        i += size


def _push_refused(body: bytes) -> bool:
    """Did receive-pack decline this push?

    http-backend exits 0 and answers 200 either way, so the verdict has to be read out of
    the response. Grepping the whole body for git's words read the hook's stderr too,
    which echoes back the paths in the pushed tree: a graph in a directory named
    `denying x` made a push that landed log as refused, and every such phrase is
    attacker-chosen. Band 1 carries report-status and nothing else. There, `ng <ref>
    <reason>` is a rejection and `ok <ref>` is not, and a ref name cannot contain a
    space, so there is nothing to spoof.
    """
    packets = list(_pkt_lines(body))
    if any(p[:1] in (b"\x01", b"\x02", b"\x03") for p in packets):
        # side-band-64k: band 1 is report-status, band 2 the hook's own stderr. Band 1 is
        # itself a pkt-line stream, so it is unwrapped twice.
        packets = list(_pkt_lines(b"".join(p[1:] for p in packets if p[:1] == b"\x01")))
    # Without side-band the body IS report-status, and no unwrapping is needed.
    return any(p.startswith(b"ng ") for p in packets)


def _field(value) -> str:
    """One event, one line. A Basic username is chosen by whoever is connecting and is
    logged BEFORE authentication on the refused-push path, so a newline in it wrote a
    second, forged line into the access log: an attacker could invent pushes that never
    happened. Printable non-space characters, 64 of them at most, `-` when nothing is left.
    """
    kept = "".join(c for c in str(value) if c.isprintable() and not c.isspace())
    return kept[:64] or "-"


class _Server(ThreadingHTTPServer):
    """The registry belongs to the server, not to a handler class minted per call.

    `make_server` used to subclass the handler to carry `registry` as a class attribute,
    so two servers in one process meant two anonymous handler types and nothing could
    name either. One attribute on the server the handler already has a reference to.
    """

    def __init__(self, registry: Registry, address, handler):
        self.registry = registry
        super().__init__(address, handler)


def make_server(reg: Registry, host: str = "127.0.0.1", port: int = 8899) -> ThreadingHTTPServer:
    return _Server(reg, (host, port), _Handler)


class _Handler(BaseHTTPRequestHandler):
    # A valid Content-Length whose body never arrived parked a thread on rfile.read
    # forever: no credentials needed, one thread per connection, until there are none.
    timeout = 30

    _answered = False                          # reset per request in _route

    def send_response(self, *args, **kwargs) -> None:
        # The catch-all below must know whether a status line is already on the wire.
        self._answered = True
        super().send_response(*args, **kwargs)

    def log_request(self, *args) -> None:      # one line per request is noise on a server
        pass

    log_error = log_request                    # send_error's own line, the same noise

    def log_message(self, action: str, graph: str = "-", user: str = "-",
                    status: int | str = 200) -> None:
        """The access log, and only the events an owner would want to answer "who changed
        this graph, and when" with: creation, joins, invites, revocations and every push.
        Reads stay silent, or the log is a `git fetch` poll loop and nothing else.
        """
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        print(f"{stamp} {self.client_address[0]} {_field(graph)} {_field(user)} "
              f"{_field(action)} {status}", file=sys.stderr, flush=True)

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
        raw = self.headers.get("Content-Length")
        try:
            length = int(raw) if raw else 0
        except ValueError:
            # "Content-Length: abc" raised here unguarded, escaping _route's except
            # GraphError: the thread died with no response and a traceback on the
            # server's stderr. GraphError instead turns into an ordinary 400.
            raise GraphError("request body length is invalid") from None
        if not (0 <= length <= MAX_PUSH_BYTES):   # the same ceiling receive.maxInputSize sets
            # A negative length reaches rfile.read(-1), which reads until EOF -- on a
            # socket the client never closes, that parks the thread forever: an
            # unauthenticated way to exhaust the server's thread pool one request at a time.
            raise GraphError("request body length is invalid")
        return self.rfile.read(length)

    def _json_body(self) -> dict:
        try:
            result = json.loads(self._body() or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as e:
            # UnicodeDecodeError is not a JSONDecodeError: a body of raw bytes on the
            # unauthenticated /join route escaped this handler and killed the thread.
            raise GraphError(f"request body is not JSON: {e}") from None
        # A list like [1, 2, 3] parses fine but then crashes the thread on .get().
        if not isinstance(result, dict):
            raise GraphError("request body must be a JSON object")
        return result

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
        # HTTP/1.0 here, so this is one request per connection today. Reset anyway: the
        # flag is wrong the moment keep-alive is switched on, and it would be wrong
        # silently, on the path that exists to stop a second response being written.
        self._answered = False
        try:
            if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
                # _body only ever reads Content-Length bytes, so a chunked body arrives as
                # nothing at all. On the git routes that handed http-backend an empty
                # stdin and a bare 500; on /join it silently read {} and refused the code
                # the caller had actually sent. Refused once, for every route.
                return self._refuse(411, "knoten: chunked uploads are not supported; set "
                                    "http.postBuffer to at least the push size (knoten "
                                    "remote add does this) and push again")
            if m := GIT_RE.match(path):
                return self._git(m.group(1), m.group(2), query)
            if path == "/graphs" and self.command == "POST":
                return self._create()
            if (m := API_RE.match(path)) and self.command == "POST":
                return getattr(self, "_" + m.group(2))(m.group(1))
            self._refuse(404, "knoten: not found")
        except GraphError as e:
            self._refuse(400, f"knoten: {e}")
        except Exception as e:                 # noqa: BLE001 - the last line of defence
            # Anything unforeseen used to leave the thread dead and the client holding a
            # connection that never answers, which reads to a user as a hung network.
            # One line on the server's stderr, one refusal on the wire.
            print(f"knoten serve: {self.command} {self.path}: {e!r}", file=sys.stderr)
            if not self._answered:
                self._refuse(500, "knoten: internal error")
            # Otherwise the status line and headers are already sent, and a second
            # response would be read as the body of the first: the client gets garbage
            # where it would otherwise get a truncated but well-formed answer.

    # ---------------------------------------------------------------- git

    def _git(self, name: str, sub: str, query: str) -> None:
        user, token = self._basic()
        # Decoded, and compared whole. A substring search over the raw query let
        # `service=git-receive%2Dpack` through: git decodes it, so a `read` token got the
        # receive-pack advertisement from a check that never saw the word it looks for.
        service = parse_qs(query).get("service", [""])[0]
        push = self.command == "POST" and sub == "/" + WRITE
        role = self.server.registry.authenticate(name, user, token)
        if role is None:
            if push:
                self.log_message(WRITE, name, user or "-", 401)
            # Same answer for "wrong token" and "no such graph": the server does not
            # confirm which graphs exist to someone who cannot open them.
            return self._refuse(401, "knoten: credentials required" if not token
                                else "knoten: that token is not valid here")
        if role == "read" and (service == WRITE or push):
            if push:
                self.log_message(WRITE, name, user, 403)
            return self._refuse(403, f"knoten: {user} has read access to {name}, not write")

        env = {
            **{k: v for k, v in os.environ.items()
               if k in KEEP_ENV or k.startswith("LC_")},
            # HOME survives the whitelist, so ~/.gitconfig still reaches receive-pack. A
            # core.hooksPath there sent it looking for hooks somewhere the gate was never
            # installed, and the push landed unchecked with rc 0.
            **SERVER_GIT_ENV,
            "GIT_PROJECT_ROOT": str(self.server.registry.graph_dir(name)),
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
        status, headers, saw_status = 200, [], False
        for line in head.decode(errors="replace").splitlines():
            key, _, value = line.partition(":")
            if key.strip().lower() == "status":
                status, saw_status = int(value.strip().split()[0]), True
            elif key.strip().lower() == "content-length":
                pass  # knoten sends its own Content-Length below; relaying http-backend's too would duplicate the header
            elif key.strip():
                headers.append((key.strip(), value.strip()))
        if r.returncode != 0:
            print(f"knoten serve: git http-backend: {r.stderr.decode(errors='replace').strip()}",
                  file=sys.stderr)
            if not saw_status:
                # A pre-receive refusal travels the sideband with exit 0, so a non-zero
                # exit with no Status line can only be the backend itself dying — a
                # crashed backend used to relay to the client as a silent 200.
                return self._refuse(500, "knoten: git http-backend failed on the server; see its log")
        if push:
            # http-backend exits 0 and answers 200 when the gate refuses: the refusal
            # travels inside the response, not in the status line. A log that reads 200
            # for a rejected push cannot answer the one question it is kept for.
            self.log_message(WRITE, name, user,
                             "refused" if _push_refused(out) else status)
        self.send_response(status)
        for key, value in headers:
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    # ---------------------------------------------------------------- api

    def _create(self) -> None:
        user, secret = self._basic()
        # Distinguishing "wrong username" from "wrong secret" would tell an attacker which half they got right.
        if user != "owner" or not self.server.registry.check_owner(secret):
            return self._refuse(401, "knoten: the owner secret is required to create a graph")
        body = self._json_body()
        token = self.server.registry.create(body.get("name", ""), body.get("admin", ""))
        self.log_message("graph-created", body.get("name", ""), body.get("admin", ""), 201)
        self._json(201, {"token": token})

    def _join(self, name: str) -> None:
        body = self._json_body()
        if not self.server.registry.exists(name):
            # /join needs no credentials, so it must not become a name oracle: an
            # unknown graph gets the same 400 a wrong code gets, not "no graph 'x'".
            return self._refuse(400, "knoten: that invite code is not valid for this graph")
        user, role, token = self.server.registry.redeem(name, body.get("code", ""))
        self.log_message(f"join:{role}", name, user, 200)
        self._json(200, {"name": user, "role": role, "token": token})

    def _admin(self, name: str) -> str | None:
        """The calling admin's name, or None after having refused the request.

        If auth fails, _admin writes the 403 response itself. Callers check the return
        value and must return without writing anything, or the client gets two responses
        on one connection."""
        user, token = self._basic()
        if self.server.registry.authenticate(name, user, token) != "admin":
            self._refuse(403, f"knoten: only an admin of {name} can do that")
            return None
        return user

    def _invite(self, name: str) -> None:
        if not (admin := self._admin(name)):
            return
        body = self._json_body()
        try:
            days = int(body.get("days", 7))
        except (TypeError, ValueError):
            raise GraphError("days must be a whole number") from None
        # `by`, so revoking this admin takes the invites they issued with them. The range
        # check on days lives in the registry, next to the timedelta that overflowed.
        code = self.server.registry.invite(name, body.get("name", ""), body.get("role", "write"),
                                    days, by=admin)
        self.log_message(f"invite:{body.get('name', '')}", name, admin, 200)
        self._json(200, {"code": code})

    def _invites(self, name: str) -> None:
        """Who is still pending, and who let them in. Never the hashes."""
        if not self._admin(name):
            return
        self._json(200, {"invites": self.server.registry.invites(name)})

    def _revoke(self, name: str) -> None:
        if not (admin := self._admin(name)):
            return
        body = self._json_body()
        self.server.registry.revoke(name, body.get("name", ""))
        self.log_message(f"revoke:{body.get('name', '')}", name, admin, 200)
        self._json(200, {"revoked": body.get("name", "")})

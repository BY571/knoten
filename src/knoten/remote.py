"""The client side of a remote graph.

A remote is a git URL plus a token. git already knows how to push, pull and clone over
HTTP and how to ask a helper program for credentials, so this module is that helper plus
a handful of commands that wrap git and make four JSON calls. It imports nothing from the
server: a client and a server never share code paths, so a bug in one cannot hide in the
other.

Tokens live in one file, mode 0600, one line per remote: `<url> <user> <token>`. The key
carries the scheme, because `https://h/x.git` and `http://h/x.git` are not the same
remote and a token scoped to the first must never travel in the clear to the second. The
owner secret for a server is stored under `owner://<host>`, a scheme git never asks for,
so no git request can be answered with the key to the whole server.
"""
from __future__ import annotations

import base64
import getpass
import json
import os
import re
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
    # The scheme is part of the identity. Without it a token stored for `https://h/x.git`
    # answered git's request for `http://h/x.git` and was sent over the wire in the clear.
    return f"{u.scheme}://{u.netloc}{u.path}".rstrip("/")


def _cred_lines(p: Path) -> list[str]:
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


def _cred_write(p: Path, lines: list[str]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        # os.open's mode applies only when the file is created; existing files keep their bits.
        os.fchmod(fd, 0o600)
    except OSError:
        # The file is already truncated by now. Leaking the fd on top of that would hold
        # it open for the life of the process, on a file with the wrong bits.
        os.close(fd)
        raise
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def cred_store(url: str, user: str, secret: str) -> None:
    p = cred_path()
    key = _key(url)
    lines = [l for l in _cred_lines(p) if not l.startswith(key + " ")]
    lines.append(f"{key} {user} {secret}")
    _cred_write(p, lines)


def _match(lines: list[str], key: str) -> tuple[str, str] | None:
    for line in lines:
        parts = line.split(" ", 2)
        if len(parts) == 3 and parts[0] == key:
            return parts[1], parts[2]
    return None


def cred_lookup(url: str) -> tuple[str, str] | None:
    p = cred_path()
    lines = _cred_lines(p)
    key = _key(url)
    if found := _match(lines, key):
        return found

    # Keys used to be `<netloc><path>`, with no scheme. Every token stored before that
    # change stopped matching the moment the scheme was added, and the only symptom was
    # git prompting for a password nobody has. So the old key is tried once and rewritten
    # under the new one, which makes the migration happen on first use rather than by
    # asking everybody to re-join. The scheme comes from the URL being looked up: a
    # schemeless key is no evidence of one, and the caller's is the only evidence there is.
    u = urlsplit(url)
    if legacy := _match(lines, f"{u.netloc}{u.path}".rstrip("/")):
        cred_store(url, *legacy)
        _cred_write(p, [l for l in _cred_lines(p)
                        if not l.startswith(f"{u.netloc}{u.path}".rstrip("/") + " ")])
        return legacy
    return None


def credential_helper(request: str) -> str:
    """git's credential protocol: key=value lines in, username and password out.

    Empty output for an unknown remote, never an error: git then falls through to its
    next helper, so every non-knoten remote on the machine keeps working.
    """
    fields = dict(line.split("=", 1) for line in request.splitlines() if "=" in line)
    url = f"{fields.get('protocol', '')}://{fields.get('host', '')}/{fields.get('path', '')}"
    if not url.startswith(("http://", "https://")):
        # git asks about ssh, file and whatever else a helper is configured for, and the
        # owner secret sits under `owner://<host>`. Answering anything but http(s) here
        # would hand the key to the whole server to whoever asked for the bare host.
        return ""
    found = cred_lookup(url)
    return f"username={found[0]}\npassword={found[1]}\n" if found else ""


# ---------------------------------------------------------------- git and http

def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


def _toplevel(root: Path) -> Path:
    r = _git(root, "rev-parse", "--show-toplevel")
    if r.returncode != 0:
        raise GraphError(f"{root} is not in a git repository; run `git init` there first")
    return Path(r.stdout.strip())


def _origin(repo: Path) -> str:
    r = _git(repo, "remote", "get-url", "origin")
    if r.returncode != 0:
        raise GraphError("this graph has no remote; run `knoten remote create` or "
                         "`knoten remote add` first")
    return r.stdout.strip()


def _wire(repo: Path, git_url: str) -> None:
    """Point origin at the remote and make git ask knoten for the token."""
    has = _git(repo, "remote", "get-url", "origin").returncode == 0
    _git(repo, "remote", "set-url" if has else "add", "origin", git_url)
    for key, value in (("credential.helper", "!knoten credential"),
                       ("credential.useHttpPath", "true"),
                       # Large pushes would otherwise go chunked, which the stdlib
                       # server does not read. 500 MB keeps them Content-Length.
                       ("http.postBuffer", "524288000")):
        _git(repo, "config", key, value)


def _api(url: str, body: dict, auth: tuple[str, str] | None = None) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    if auth:
        cred = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        req.add_header("Authorization", "Basic " + cred)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            message = json.loads(e.read()).get("error", e.reason)
        except Exception:
            message = e.reason
        raise GraphError(str(message).removeprefix("knoten: ")) from None
    except urllib.error.URLError as e:
        raise GraphError(f"cannot reach {url}: {e.reason}") from None


def _explain(stderr: str) -> str:
    """git prints only a status code for an HTTP refusal. Say what it means.

    A bare substring search for "401"/"403" also matched git's own URL line
    (`fatal: unable to access 'http://127.0.0.1:36605/trading.git/': The requested
    URL returned error: 403`), so any host, port or graph name that happened to
    contain those three digits flipped the verdict — the `hub` fixture binds
    port 0, and plenty of ephemeral ports contain "401". Match git's phrasing,
    not the digits.
    """
    # A relayed `remote:` line can itself contain "401" or "403" (a node id, a rule
    # message) — match only git's own lines, or a rule violation reads as a credential
    # problem.
    own = "\n".join(l for l in stderr.splitlines() if not l.startswith("remote:"))
    if re.search(r"returned error: 401\b|HTTP 401\b|Authentication failed|terminal prompts disabled", own):
        return "credentials refused; the token may have been revoked. Ask for a new invite."
    if re.search(r"returned error: 403\b|HTTP 403\b", own):
        return "this token has read access, not write"
    if "pre-receive hook declined" in stderr:
        return "the server refused the push; fix the violations above and push again"
    lines = [l for l in stderr.strip().splitlines() if l.strip()]
    return lines[-1] if lines else "git failed"


def _relay(stderr: str) -> None:
    """The gate's own output arrives as `remote:` lines. Show those, and only those."""
    for line in stderr.splitlines():
        if line.startswith("remote:"):
            print(line, file=sys.stderr)


# ---------------------------------------------------------------- commands

def remote_create(root: Path, name: str, on: str, admin: str | None = None,
                  owner_secret: str | None = None) -> str:
    repo = _toplevel(root)
    on = on.rstrip("/")
    u = urlsplit(on)
    # Not `<scheme>://<host>`: that is the shape git asks for when useHttpPath is off, and
    # the credential helper would then hand the owner secret to a plain `git fetch`.
    owner_key = f"owner://{u.netloc}"
    # The environment before the prompt, and before argv is even considered a good idea:
    # `--owner-secret` puts the secret in `ps` output for every other user on the machine.
    secret = (owner_secret or os.environ.get("KNOTEN_OWNER_SECRET")
              or (cred_lookup(owner_key) or ("", ""))[1])
    if not secret:
        try:
            secret = getpass.getpass(f"owner secret for {u.netloc}: ")
        except EOFError:
            # No terminal to prompt on (cron, CI, a pipe): a raw traceback here would
            # break "no tracebacks for user error", so this is a refusal like any other.
            raise GraphError(f"no owner secret for {u.netloc}; pass --owner-secret, or "
                             "run this where a prompt can be answered") from None
    if admin is None:
        admin = _git(repo, "config", "user.name").stdout.strip().lower().replace(" ", "-")
    if not ID_RE.match(admin or ""):
        raise GraphError(f"'{admin}' is not a valid contributor name; pass --as NAME (kebab-case)")

    token = _api(f"{on}/graphs", {"name": name, "admin": admin}, ("owner", secret))["token"]
    cred_store(owner_key, "owner", secret)
    git_url = f"{on}/{name}.git"
    cred_store(git_url, admin, token)
    _wire(repo, git_url)
    r = _git(repo, "push", "-u", "origin", "HEAD")
    _relay(r.stderr)
    if r.returncode != 0:
        # The graph was created two calls up. Reporting only git's error read as "nothing
        # happened", so people re-ran the command and met "already exists" with no idea
        # which half had worked.
        raise GraphError(
            f"{_explain(r.stderr)}. The graph now EXISTS on {on} and your token is saved, "
            f"so do not create it again: fix the above, then `knoten push` here, or "
            f"`knoten remote add {on}/{name}` in a fresh clone.")
    return f"{on}/{name}"


def remote_add(root: Path, url: str) -> None:
    _wire(_toplevel(root), url.rstrip("/") + ".git")


def push(root: Path) -> int:
    repo = _toplevel(root)
    _origin(repo)
    r = _git(repo, "push", "origin", "HEAD")
    _relay(r.stderr)
    if r.returncode != 0:
        raise GraphError(_explain(r.stderr))
    print("  ✓ pushed")
    return 0


def pull(root: Path) -> int:
    repo = _toplevel(root)
    _origin(repo)
    r = _git(repo, "pull", "-q", "--ff-only", "origin")
    if r.returncode != 0:
        raise GraphError(_explain(r.stderr))
    print("  ✓ up to date")
    return 0


def _graph_api(root: Path) -> tuple[str, tuple[str, str]]:
    """The graph's API base URL and the caller's credentials for it."""
    repo = _toplevel(root)
    git_url = _origin(repo)
    auth = cred_lookup(git_url)
    if not auth:
        raise GraphError("no credentials stored for this remote; are you a contributor here?")
    return git_url.removesuffix(".git"), auth


def invite(root: Path, name: str, role: str = "write", days: int = 7) -> str:
    base, auth = _graph_api(root)
    return _api(f"{base}/invite", {"name": name, "role": role, "days": days}, auth)["code"]


def invites(root: Path) -> list[dict]:
    base, auth = _graph_api(root)
    return _api(f"{base}/invites", {}, auth)["invites"]


def revoke(root: Path, name: str) -> None:
    base, auth = _graph_api(root)
    _api(f"{base}/revoke", {"name": name}, auth)


def join(url: str, code: str, dest: str | None = None) -> tuple[Path, str, str]:
    """Redeem the code, remember the token, clone. Nothing is written to disk until the
    server has accepted the code, so a wrong code leaves no half-made clone behind."""
    url = url.rstrip("/")
    git_url = url + ".git"
    got = _api(f"{url}/join", {"code": code})
    # The server is not trusted with the contents of the credentials file. That file is
    # one line per remote, so a `name` carrying a newline appends a whole second line to
    # it: a credential for a remote the user never joined, on a host they never named.
    user, role = got.get("name", ""), got.get("role", "")
    token = got.get("token", "")
    if (not ID_RE.match(user or "") or role not in ("read", "write", "admin")
            or not token or any(c.isspace() for c in token)):
        raise GraphError("the server's reply was malformed")
    cred_store(git_url, user, token)
    target = Path(dest or url.rsplit("/", 1)[-1])
    r = subprocess.run(["git", "-c", "credential.helper=!knoten credential",
                        "-c", "credential.useHttpPath=true",
                        "clone", "-q", git_url, str(target)], capture_output=True, text=True)
    if r.returncode != 0:
        # The server already consumed the code in the _api call above, one line up. git's
        # own error alone reads like the code is still good and worth retrying — it is
        # not, so say what actually happened and how to finish without it.
        raise GraphError(
            f"clone failed: {_explain(r.stderr)}. The invite is spent but your credentials "
            f"are saved, so finish by hand: git clone {git_url} <dir> && cd <dir> && "
            f"knoten remote add {url}")
    _wire(target, git_url)
    return target, user, role

"""The client side of a remote graph.

A remote is a git URL plus a token. git already knows how to push, pull and clone over
HTTP and how to ask a helper program for credentials, so this module is that helper plus
a handful of commands that wrap git and make four JSON calls. It imports nothing from the
server itself (`serve.py`, `registry.py`): a client and a server never share THOSE code
paths, so a bug in one cannot hide in the other. The one exception is `gate.graph_dirs`,
a read-only tree walk with no server state behind it, reused here to find where a freshly
cloned graph lives (root or a monorepo subdirectory) -- the same question the gate itself
answers on every push.

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
import secrets
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from . import contributors as C
from . import gate
from .core import GraphError, ID_RE, _yaml, today
from .keys import INVITE_NS, configure_signing, ensure_key, key_dir, public_line, sign


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
    """The exact key or nothing. There is deliberately no fallback to the schemeless
    `<netloc><path>` key this file used before the scheme was added.

    That fallback existed for one release-less week and it leaked the owner secret. Old
    keys carried no scheme, so `owner://h:8899` and `https://h:8899` both collapse to
    `h:8899`: a plain `git fetch` against the bare host (any repo without
    credential.useHttpPath) matched the owner line, and the migration then rewrote it as
    `https://h:8899`, reachable by ordinary git from then on. The whole point of the
    `owner://` namespace is that no git request can name it, and a lookup that strips the
    scheme is exactly a lookup that can. Nothing is released, so nothing is stranded.
    """
    return _match(_cred_lines(cred_path()), _key(url))


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

def _my_key(contribs: dict, name: str) -> Path:
    """The private key already on THIS machine for `name`, checked against what
    contributors.yaml lists for them -- checked BEFORE anything (`ensure_key` included)
    can generate a fresh, mismatched keypair under that name. `ensure_key` never
    regenerates an existing key, so a wrong key made here would be wrong forever."""
    priv = key_dir() / name
    if not priv.exists() or (contribs.get(name) or {}).get("key") != public_line(priv):
        raise GraphError(f"{C.FILE} lists a different key for '{name}' than {priv}; "
                         "use the machine that holds the listed key")
    return priv


def _graph_name(root: Path) -> str:
    """The graph's own `name:`, read straight from `graph.yaml` -- not through
    `validate.load_config`, whose full schema check would refuse an invite over a
    problem (an unknown key, a bad `node_types` entry) that has nothing to do with
    inviting anyone."""
    f = Path(root) / "graph.yaml"
    if not f.exists():
        raise GraphError(f"{f} is missing; is this a graph directory?")
    cfg = _yaml(f.read_text(encoding="utf-8"), "graph.yaml")
    name = cfg.get("name")
    if not isinstance(name, str) or not name:
        raise GraphError("graph.yaml has no 'name'")
    return name


def _graph_dir(repo: Path) -> str | None:
    """Which directory inside `repo` holds ITS graph, at HEAD: `''` for the root, a
    subpath in a monorepo layout. `None` when there is no graph at all. More than one is
    refused here in one line -- exactly the ambiguity the gate itself would face, and
    `join` cannot guess which one a newcomer means to join."""
    dirs = gate.graph_dirs("HEAD", repo=repo)
    if len(dirs) > 1:
        raise GraphError(
            f"{repo} hosts more than one graph; join does not know which one you mean. "
            f"Your clone and credentials are in place; add yourself to the right "
            f"{C.FILE} by hand.")
    return dirs[0] if dirs else None


def _bootstrap(root: Path, repo: Path, admin: str) -> None:
    """Make this clone sign as `admin`, and make the graph list `admin` as its admin.

    The first contributors.yaml is the constitution's genesis: the gate accepts it only
    if the commit is signed by an admin it names, so the creator's key must exist before
    the first push, and the file and the signature must agree."""
    contribs = C.load(root)
    if contribs is not None and admin not in contribs:
        # Checked before ensure_key (never even reached below): a typo'd but kebab-valid
        # --as must not leave a stray keypair on disk after this exact refusal.
        raise GraphError(f"'{admin}' is not listed in {C.FILE}; an admin has to invite you, "
                         "or delete that file if you are starting this graph")
    if contribs is not None:
        # Not the genesis: the graph already names an admin, so the key on this machine
        # must be the one it lists, checked before anything could mint a fresh one.
        configure_signing(repo, _my_key(contribs, admin))
        return
    priv = ensure_key(admin)
    configure_signing(repo, priv)
    C.dump(root, {admin: {"key": public_line(priv), "role": "admin"}})
    # Only contributors.yaml: `add -A` in the enclosing repo would also stage every
    # unrelated untracked file the monorepo layout allows next to this graph, and
    # remote_create would then push it with no listing or confirmation.
    _git(repo, "add", "--", str(root / C.FILE))
    r = _git(repo, "commit", "-q", "-m", f"{admin} creates the graph as admin")
    if r.returncode != 0:
        first_line = next((l for l in r.stderr.splitlines() if l.strip()), r.stderr.strip())
        raise GraphError(f"could not commit {C.FILE}: {first_line}. Set user.name and "
                         "user.email in this repo and run the command again")


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

    # Local work, and idempotent: a refusal here creates nothing on the server, and a
    # later failure (wrong owner secret) leaves a harmless bootstrap commit that a re-run
    # accepts.
    _bootstrap(root, repo, admin)

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
    body = {"name": name, "role": role, "days": days}
    contribs = C.load(root)
    if contribs is not None:
        # Bounded before it ever reaches timedelta: unbounded, a huge --expires overflows
        # timedelta with a raw OverflowError, well before the server gets a chance to
        # enforce the very same bound itself.
        if not 1 <= int(days) <= 365:
            raise GraphError("days must be between 1 and 365")
        priv = _my_key(contribs, auth[0])
        graph = _graph_name(root)
        expires = (datetime.now(timezone.utc) + timedelta(days=int(days))).date().isoformat()
        blob = C.invite_blob(graph, name, role, expires, secrets.token_hex(8))
        body.update(blob=blob.decode(), sig=sign(priv, blob, INVITE_NS))
    return _api(f"{base}/invite", body, auth)["code"]


def invites(root: Path) -> list[dict]:
    base, auth = _graph_api(root)
    return _api(f"{base}/invites", {}, auth)["invites"]


def revoke(root: Path, name: str) -> None:
    base, auth = _graph_api(root)
    contribs = C.load(root)
    if contribs is not None:
        repo = _toplevel(root)
        # This clone's history may be behind -- as it is the moment right after someone
        # new has joined -- and revoking a name this clone cannot yet see would silently
        # mark nothing.
        rp = _git(repo, "pull", "-q", "--ff-only", "origin")
        if rp.returncode != 0:
            raise GraphError(f"could not update before revoking: {_explain(rp.stderr)}")
        contribs = C.load(root)
        # Who acts here is this clone's OWN configured signing key, not whatever this
        # machine's credential file happens to hold for the remote right now -- a join
        # run from the very same store (as every test and every single-laptop admin
        # does) overwrites that entry with the newcomer's token. The signing key survives
        # that untouched, and it is what the commit below is actually signed with.
        admin = Path(_git(repo, "config", "user.signingkey").stdout.strip()).name
        _my_key(contribs, admin)
        if admin not in C.admins(contribs):
            raise GraphError("only an admin can revoke")
        if name not in contribs:
            raise GraphError(f"'{name}' is not listed in {C.FILE}")
        if contribs[name].get("revoked"):
            raise GraphError(f"'{name}' is already revoked")
        # The mark first, the token second. The mark is what the gate enforces and what
        # a clone can still read a year on; the token is convenience. If the push is
        # refused, nothing has changed and the message says why.
        contribs[name]["revoked"] = today()
        C.dump(root, contribs)
        # Only contributors.yaml: `add -A` in the enclosing repo would also stage every
        # unrelated untracked file the monorepo layout allows next to this graph.
        _git(repo, "add", "--", str(root / C.FILE))
        r = _git(repo, "commit", "-q", "-m", f"{admin} revokes {name}")
        if r.returncode != 0:
            first_line = next((l for l in r.stderr.splitlines() if l.strip()), r.stderr.strip())
            raise GraphError(f"could not commit the revocation: {first_line}")
        r = _git(repo, "push", "origin", "HEAD")
        _relay(r.stderr)
        if r.returncode != 0:
            raise GraphError(f"the gate refused the revocation: {_explain(r.stderr)}")
        try:
            _api(f"{base}/revoke", {"name": name}, auth)
        except GraphError as e:
            # The graph mark just pushed is already the source of truth, and it already
            # locks this person out at the gate; the server's token table catching up is
            # convenience on top of that, not a condition of success. `auth` here is
            # whatever this machine's credential file currently holds for the remote --
            # not necessarily the acting admin's own token, since a join can have
            # overwritten it -- so a refusal here is not evidence the mark itself failed.
            print(f"  ⚠ the graph is updated, but the server did not confirm: {e}",
                 file=sys.stderr)
        return
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
    # A hosted graph may sit at the clone's root or, in a monorepo layout, a subdirectory
    # of it -- the gate itself has to answer this same question on every push, so it is
    # asked here rather than assumed to be the root.
    gname = _graph_dir(target)
    gdir = target / gname if gname is not None else None
    contribs = C.load(gdir) if gdir is not None else None
    if contribs is not None:
        # A signed graph: the server handed back the admin's signed invite. The newcomer
        # adds themself with it, in a commit signed by their own new key, and the gate
        # accepts exactly that shape and nothing else.
        blob, sig = got.get("blob", ""), got.get("sig", "")
        if not isinstance(blob, str) or not isinstance(sig, str) or not blob or not sig:
            raise GraphError("the server's reply was malformed")
        priv = ensure_key(user)
        configure_signing(target, priv)
        contribs[user] = {"key": public_line(priv), "role": role,
                          "invited_by": got.get("by", ""),
                          "invite": {"blob": blob, "sig": sig}}
        C.dump(gdir, contribs)
        # git refuses to commit without an identity, and a fresh clone under
        # GIT_ISOLATION (or on a bare new machine) has none configured yet.
        for k, v in (("user.name", user), ("user.email", f"{user}@knoten")):
            if not _git(target, "config", k).stdout.strip():
                _git(target, "config", k, v)
        # `-C target` already anchors the command there, so the pathspec must be
        # relative to it -- `target / C.FILE` looked right but resolved against the
        # PROCESS's cwd first, one directory too deep, and silently added nothing.
        pathspec = f"{gname}/{C.FILE}" if gname else C.FILE
        _git(target, "add", "--", pathspec)
        r = _git(target, "commit", "-q", "-m", f"{user} joins as {role}")
        if r.returncode != 0:
            first_line = next((l for l in r.stderr.splitlines() if l.strip()), r.stderr.strip())
            raise GraphError(
                f"could not commit your entry: {first_line}. Your clone and credentials "
                f"are in place at {target}; fix the problem and commit {C.FILE} yourself, "
                f"or run `knoten join` again with --dest pointing at a new directory.")
        r = _git(target, "push", "origin", "HEAD")
        _relay(r.stderr)
        if r.returncode != 0:
            raise GraphError(
                f"the gate refused your join commit: {_explain(r.stderr)}. Your clone and "
                f"credentials are in place; ask the admin for a fresh invite and run "
                f"`knoten join` again with --dest pointing at a new directory.")
    return target, user, role

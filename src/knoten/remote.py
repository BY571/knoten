"""The client side of a remote graph: git's credential helper plus a handful of commands
that wrap git and make four JSON calls. It imports nothing from the server (`serve.py`,
`registry.py`) so a bug in one cannot hide in the other -- except `gate.graph_dirs`, a
read-only tree walk asked the same question the gate asks on every push.

Tokens live in one file, mode 0600, one line per remote: `<url> <user> <token>`. The key
carries the scheme, because a token scoped to `https://h/x.git` must never travel in the
clear to `http://h/x.git`. The owner secret is stored under `owner://<host>`, a scheme
git never asks for, so no git request can be answered with the key to the whole server.
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

from . import identity as C
from . import gate
from .core import GraphError, ID_RE, MAX_DAYS, MAX_NAME, _yaml, today
from .identity import INVITE_NS, configure_signing, ensure_key, key_dir, public_line, sign


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
    """The exact key or nothing, with deliberately NO fallback to a schemeless
    `<netloc><path>` key: `owner://h:8899` and `https://h:8899` both collapse to `h:8899`,
    so a plain `git fetch` against the bare host matched the owner line and leaked the
    secret. The whole point of the `owner://` namespace is that no git request names it,
    and a lookup that strips the scheme is exactly a lookup that can."""
    return _match(_cred_lines(cred_path()), _key(url))


def credential_helper(request: str) -> str:
    """git's credential protocol: key=value lines in, username and password out. Empty
    output for an unknown remote, never an error, so git falls through to its next helper
    and every non-knoten remote on the machine keeps working."""
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

    Match git's PHRASING, not the digits: a bare search for "401"/"403" also matched the
    port and the graph name in git's own URL line. And only git's own lines, not the
    relayed `remote:` ones, or a rule message containing "403" reads as a credential
    problem."""
    own = "\n".join(l for l in stderr.splitlines() if not l.startswith("remote:"))
    if re.search(r"returned error: 401\b|HTTP 401\b|Authentication failed|terminal prompts disabled", own):
        return "credentials refused; the token may have been revoked. Ask for a new invite."
    if re.search(r"returned error: 403\b|HTTP 403\b", own):
        return "this token has read access, not write"
    if "pre-receive hook declined" in stderr:
        return "the server refused the push; fix the violations above and push again"
    if re.search(r"\(fetch first\)|\(non-fast-forward\)", own):
        return "the graph moved on since your last pull; run `knoten pull`, then push again"
    if "CONFLICT" in stderr or "could not apply" in own:
        return ("pull stopped on a conflict; fix the file git names, `git add` it, "
                "`git rebase --continue`, then push")
    lines = [l for l in stderr.strip().splitlines() if l.strip()]
    return lines[-1] if lines else "git failed"


IN_PLACE = "your clone and credentials are in place"


def _stranded(why: str, done: str, finish: str) -> GraphError:
    """A refusal AFTER something already landed. git's error alone reads as "nothing
    happened", so people re-ran the command and met "already exists" with no idea which
    half had worked. Say which half did, then how to finish without redoing it."""
    return GraphError(f"{why}. But {done}, so do not start over: {finish}.")


def _relay(stderr: str) -> None:
    """The gate's own output arrives as `remote:` lines. Show those, and only those."""
    for line in stderr.splitlines():
        if line.startswith("remote:"):
            print(line, file=sys.stderr)


# ---------------------------------------------------------------- commands

def _my_key(contribs: dict, name: str) -> Path:
    """The private key already on THIS machine for `name`, checked against what
    contributors.yaml lists -- BEFORE anything can generate a fresh, mismatched keypair
    under that name. `ensure_key` never regenerates, so a wrong key is wrong forever."""
    priv = key_dir() / name
    if not priv.exists() or (contribs.get(name) or {}).get("key") != public_line(priv):
        raise GraphError(f"{C.FILE} lists a different key for '{name}' than {priv}; "
                         "use the machine that holds the listed key")
    return priv


def _commit_file(repo: Path, pathspec: str, message: str) -> str | None:
    """Stage exactly `pathspec` and commit it -- never `add -A`, which would also stage
    every unrelated file a monorepo layout allows beside the graph. `None` on success,
    the first non-empty stderr line on failure."""
    _git(repo, "add", "--", pathspec)
    r = _git(repo, "commit", "-q", "-m", message)
    if r.returncode != 0:
        return next((l for l in r.stderr.splitlines() if l.strip()), r.stderr.strip())
    return None


def _graph_name(root: Path) -> str:
    """The graph's own `name:`, read straight from `graph.yaml` -- not through
    `load_config`, whose full schema check would refuse an invite over an unknown key
    that has nothing to do with inviting anyone."""
    f = Path(root) / "graph.yaml"
    if not f.exists():
        raise GraphError(f"{f} is missing; is this a graph directory?")
    cfg = _yaml(f.read_text(encoding="utf-8"), "graph.yaml")
    name = cfg.get("name")
    if not isinstance(name, str) or not name:
        raise GraphError("graph.yaml has no 'name'")
    return name


def _graph_subdir(repo: Path) -> str | None:
    """Which directory inside `repo` holds ITS graph at HEAD: `''` for the root, a
    subpath in a monorepo layout, `None` for no graph. More than one is refused: `join`
    cannot guess which one a newcomer means."""
    dirs = gate.graph_dirs("HEAD", repo=repo)
    if len(dirs) > 1:
        raise GraphError(
            f"{repo} hosts more than one graph; join does not know which one you mean. "
            f"Your clone and credentials are in place; add yourself to the right "
            f"{C.FILE} by hand.")
    return dirs[0] if dirs else None


def default_signing_name(cwd: Path | None = None) -> str:
    """Who this machine signs as when nobody said: git's own `user.name`, lower-cased and
    hyphenated. Raises rather than returning "", which reached `ensure_key` as a key file
    with no name and a refusal that blamed the name instead of saying what to do."""
    name = _git(cwd or Path.cwd(), "config", "user.name").stdout.strip().lower().replace(" ", "-")
    if not name:
        raise GraphError("pass a name or set git config user.name")
    return name


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
    fail = _commit_file(repo, str(root / C.FILE), f"{admin} creates the graph as admin")
    if fail:
        raise GraphError(f"could not commit {C.FILE}: {fail}. Set user.name and "
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
    if not ID_RE.match(admin or "") or len(admin) > MAX_NAME:
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
        raise _stranded(_explain(r.stderr), f"the graph now EXISTS on {on} and your token "
                        f"is saved", f"fix the above and `knoten push` here, or "
                        f"`knoten remote add {on}/{name}` in a fresh clone")
    return f"{on}/{name}"


def remote_add(root: Path, url: str, me: str | None = None) -> str | None:
    """Point a clone at its remote. On a signed graph, if `me` is listed there and this
    machine holds their key, make the clone sign as them: this is the second-laptop
    path, and a clone that pulls fine but pushes unsigned is a puzzle nobody deserves.
    Returns the name the clone now signs as, or None (unsigned graph, or a reader)."""
    repo = _toplevel(root)
    git_url = url.rstrip("/") + ".git"
    _wire(repo, git_url)
    contribs = C.load(root)
    if contribs is None:
        return None
    me = me or (cred_lookup(git_url) or ("",))[0] or default_signing_name(root)
    if me not in contribs:
        return None
    configure_signing(repo, _my_key(contribs, me))
    return me


def push(root: Path) -> int:
    repo = _toplevel(root)
    _origin(repo)
    # `knoten commit` files a node on disk and git commits nothing; `push` sends HEAD.
    # Without this a collaborator files a node, pushes, sees a tick, and has sent nothing.
    dirty = _git(repo, "status", "--porcelain", "--", str(root))
    if dirty.stdout.strip():
        n = len(dirty.stdout.strip().splitlines())
        raise GraphError(f"{n} change(s) in the graph are not committed to git, so there is "
                         "nothing to push yet: `git add -A && git commit -m '...'`, then push")
    r = _git(repo, "push", "origin", "HEAD")
    _relay(r.stderr)
    if r.returncode != 0:
        raise GraphError(_explain(r.stderr))
    print("  ✓ pushed")
    return 0


def pull(root: Path) -> int:
    repo = _toplevel(root)
    _origin(repo)
    # Rebase, never merge: the gate refuses a merge commit, so a merge is a pull that can
    # never be pushed. A rebase makes commits, which this clone signs. Autostash keeps a
    # half-written node out of the way.
    r = _git(repo, "pull", "-q", "--rebase", "--autostash", "origin")
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
        # Bounded before timedelta sees it: a huge --expires overflows with a raw
        # OverflowError before the server can enforce the same bound itself.
        if not 1 <= int(days) <= MAX_DAYS:
            raise GraphError(f"days must be between 1 and {MAX_DAYS}")
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
        # Anyone may pull: this clone's history may be behind -- as it is the moment
        # right after someone new has joined -- and revoking a name this clone cannot
        # yet see would silently mark nothing.
        rp = _git(repo, "pull", "-q", "--ff-only", "origin")
        if rp.returncode != 0:
            raise GraphError(f"could not update before revoking: {_explain(rp.stderr)}. "
                             f"Pull or resolve that yourself, then run "
                             f"`knoten revoke {name}` again")
        contribs = C.load(root)
        # `auth[0]` is this machine's OWN identity for this remote. Signing has to be
        # configured before `_my_key` checks anything against it: an admin clone recovered
        # with a bare `git clone` + `knoten remote add` has no `user.signingkey` at all,
        # and `_my_key` would then be asked to check a key file with an empty name.
        if not _git(repo, "config", "user.signingkey").stdout.strip():
            raise GraphError(
                f"this clone has no signing key configured; run `knoten key {auth[0]}` "
                "for the path, then `git config user.signingkey <path>` (and "
                "`git config gpg.format ssh`, `git config commit.gpgsign true`) before "
                "revoking here")
        _my_key(contribs, auth[0])
        if auth[0] not in C.admins(contribs):
            raise GraphError("only an admin can revoke")
        if name not in contribs:
            raise GraphError(f"'{name}' is not listed in {C.FILE}")
        if not contribs[name].get("revoked"):
            # The mark first, the token second: the mark is what the gate enforces and
            # what a clone can still read a year on. Already-revoked is not refused, so a
            # failed `/revoke` below can be retried without redoing the mark.
            contribs[name]["revoked"] = today()
            C.dump(root, contribs)
            fail = _commit_file(repo, str(root / C.FILE), f"{auth[0]} revokes {name}")
            if fail:
                raise GraphError(f"could not commit the revocation: {fail}")
            r = _git(repo, "push", "origin", "HEAD")
            _relay(r.stderr)
            if r.returncode != 0:
                raise GraphError(f"the gate refused the revocation: {_explain(r.stderr)}")
        try:
            _api(f"{base}/revoke", {"name": name}, auth)
        except GraphError as e:
            # The mark landed and locks them out at the gate, but the token is not dead
            # until the server hears about it: swallowing this would report success while
            # a live token still works.
            raise GraphError(
                f"the graph already marks '{name}' revoked, but the server refused the "
                f"token call: {e}; '{name}' can still connect with a live token until "
                f"you run `knoten revoke {name}` again") from e
        return
    _api(f"{base}/revoke", {"name": name}, auth)


def join(url: str, code: str, dest: str | None = None) -> tuple[Path, str, str, bool]:
    """Redeem the code, remember the token, clone. Returns (clone, name, role, signed),
    where `signed` says whether this join put a signing key and an entry in the graph.
    Nothing is written to disk until the server has accepted the code, so a wrong code
    leaves no half-made clone behind."""
    url = url.rstrip("/")
    git_url = url + ".git"
    got = _api(f"{url}/join", {"code": code})
    # The server is not trusted with the contents of the credentials file: it is one line
    # per remote, so a `name` carrying a newline appends a credential for a remote the
    # user never joined, on a host they never named.
    user, role = got.get("name", ""), got.get("role", "")
    token = got.get("token", "")
    if (not ID_RE.match(user or "") or len(user) > MAX_NAME
            or role not in ("read", "write", "admin")
            or not token or any(c.isspace() for c in token)):
        raise GraphError("the server's reply was malformed")
    cred_store(git_url, user, token)
    target = Path(dest or url.rsplit("/", 1)[-1])
    r = subprocess.run(["git", "-c", "credential.helper=!knoten credential",
                        "-c", "credential.useHttpPath=true",
                        "clone", "-q", git_url, str(target)], capture_output=True, text=True)
    if r.returncode != 0:
        raise _stranded(f"clone failed: {_explain(r.stderr)}",
                        "the invite is spent but your credentials are saved",
                        f"git clone {git_url} <dir> && cd <dir> && knoten remote add {url}")
    _wire(target, git_url)
    if role == "read":
        # A reader is NOT listed: every entry in contributors.yaml is a key the gate will
        # accept a commit from, and a reader has nothing to sign. Their token is the whole
        # of their access, and it is the admin's to revoke.
        return target, user, role, False
    # A hosted graph may sit at the clone's root or in a monorepo subdirectory: asked,
    # not assumed, because the gate asks the same on every push.
    gname = _graph_subdir(target)
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
        # git refuses to commit without an identity, and a fresh clone has none.
        for k, v in (("user.name", user), ("user.email", f"{user}@knoten")):
            if not _git(target, "config", k).stdout.strip():
                _git(target, "config", k, v)
        # `-C target` anchors the command, so the pathspec is relative to it: an absolute
        # `target / C.FILE` resolved one directory too deep and silently added nothing.
        pathspec = f"{gname}/{C.FILE}" if gname else C.FILE
        fail = _commit_file(target, pathspec, f"{user} joins as {role}")
        if fail:
            raise _stranded(f"could not commit your entry: {fail}", IN_PLACE,
                            f"commit {C.FILE} yourself, or run `knoten join` again "
                            f"with --dest pointing at a new directory")
        r = _git(target, "push", "origin", "HEAD")
        _relay(r.stderr)
        if r.returncode != 0:
            raise _stranded(f"the gate refused your join commit: {_explain(r.stderr)}",
                            IN_PLACE, "ask the admin for a fresh invite and run `knoten "
                            "join` again with --dest pointing at a new directory")
        return target, user, role, True
    return target, user, role, False

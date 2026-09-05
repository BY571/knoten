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

from . import contributors as C
from . import gate
from .core import (GraphError, ID_RE, MAX_NAME, MAX_PUSH_BYTES, graph_lock,
                   server_git_env, write_atomic)
from .hook import install_server

ROLES = ("read", "write", "admin")

# An invite is a bearer secret. A year is already generous for one.
MAX_DAYS = 365


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
        # 0700, not the 0755 mkdir defaults to: tokens.json, the owner secret and every
        # graph live under here, and on a server with more than one local account the
        # default made all of it world-readable.
        self.data.mkdir(mode=0o700, parents=True, exist_ok=True)
        (self.data / "graphs").mkdir(mode=0o700, exist_ok=True)

    # ---------------------------------------------------------------- owner

    def ensure_owner_secret(self) -> tuple[str, bool]:
        """The secret, and whether this call is the one that made it.

        `knoten serve` prints it exactly once, on the run that creates it, so it has to be
        told. Deciding from "did the file exist" is what broke: a file that existed but
        was EMPTY counted as made, so serve printed nothing, check_owner had nothing to
        compare against, and every `POST /graphs` was a 401 forever with nothing on disk
        to explain it. A crash between the create and the write is exactly how that file
        appears, which is why the write goes through a temp file and a rename.
        """
        p = self.data / "owner"
        current = p.read_text(encoding="utf-8").strip() if p.exists() else ""
        if current:
            return current, False
        secret = secrets.token_hex(32)
        tmp = p.with_name("owner.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(secret)
        os.chmod(tmp, 0o600)         # os.open's mode is ignored when the temp file existed
        os.replace(tmp, p)
        return secret, True

    def owner_secret(self) -> str:
        """Created on first call, 0600, printed once by `knoten serve` and never again."""
        return self.ensure_owner_secret()[0]

    def check_owner(self, secret: str) -> bool:
        """Fail closed. This must never CREATE the secret: it is reached by an
        unauthenticated request, and creating one here would mint a secret nobody is
        watching for. `knoten serve` creates it at startup, where the owner can read it."""
        p = self.data / "owner"
        stored = p.read_text(encoding="utf-8").strip() if p.exists() else ""
        # An empty owner file made "" a valid secret, so a truncated file handed graph
        # creation to anyone who sent no password at all.
        return bool(stored) and hmac.compare_digest(secret or "", stored)

    # ---------------------------------------------------------------- graphs

    def graph_dir(self, name: str) -> Path:
        # A name becomes a directory. Checked here, at the ONE place every path is made,
        # so `../etc` cannot reach mkdir through any entry point.
        if not ID_RE.match(name or ""):
            raise GraphError(f"'{name}' is not a valid graph name (use kebab-case: my-topic)")
        if len(name) > MAX_NAME:
            raise GraphError(f"graph name is too long (max {MAX_NAME} characters)")
        return self.data / "graphs" / name

    def exists(self, name: str) -> bool:
        """Never raises. `authenticate` and the /join route both lean on this, and both
        owe an outsider the SAME answer for every name they cannot open: a 65-character
        name that raised came back as a 400 where an unknown graph gets a 401, and gave
        /join a different body from the one pinned to be identical to a wrong code. Out
        here, too long is simply no such graph; the raise stays on create and mint, where
        the caller is naming something they own."""
        if not ID_RE.match(name or "") or len(name) > MAX_NAME:
            return False
        return (self.graph_dir(name) / "repo.git").is_dir()

    def repo(self, name: str) -> Path:
        r = self.graph_dir(name) / "repo.git"
        if not r.is_dir():
            raise GraphError(f"no graph '{name}' on this server")
        return r

    def head_graph(self, name: str) -> tuple[dict | None, str]:
        """The hosted repo's contributors.yaml at its tip and its graph name. None for a
        phase-1 graph or an empty repo. A signed remote holds one graph: the server has
        to know WHICH contributors.yaml an invite is checked against.

        The tip is `HEAD` when that resolves, but `knoten remote create` runs `git push
        -u origin HEAD` -- whatever branch the user happens to be on, `main` as often as
        `master` -- so a bare repo freshly made by `git init --bare` has an UNBORN HEAD
        (it still points at refs/heads/master, which a push to `main` never creates).
        Reading only `HEAD` there returned "no contributors, no name" for a graph that is
        fully bootstrapped and signed, and every invite for it minted unsigned. When HEAD
        does not resolve, fall back to whatever branch actually exists: exactly one, use
        it, and FIX HEAD to point there so this fallback runs exactly once -- a graph
        pushed as `main` today must not turn into "several branches and no HEAD" the day
        someone pushes a second branch to it. None, an empty repo. More than one with no
        valid HEAD, refuse: there is no single line of history left to check an invite
        against, and only a human can say which branch should have been HEAD."""
        repo = self.repo(name)
        tip = "HEAD"
        if gate._git("rev-parse", "--verify", "-q", "HEAD", repo=repo).returncode != 0:
            r = gate._git("for-each-ref", "--format=%(refname)", "refs/heads/", repo=repo)
            branches = [b for b in r.stdout.decode().splitlines() if b]
            if not branches:
                return None, ""
            if len(branches) > 1:
                raise GraphError(
                    f"graph '{name}' has several branches and no HEAD; a signed remote "
                    f"needs one line of history -- fix it with: "
                    f"git --git-dir={repo} symbolic-ref HEAD refs/heads/<branch>")
            tip = branches[0]
            gate._git("symbolic-ref", "HEAD", tip, repo=repo)
        dirs = gate.graph_dirs(tip, repo=repo)
        if len(dirs) > 1:
            raise GraphError(f"graph '{name}' holds {len(dirs)} graphs; a signed remote holds one")
        if not dirs:
            return None, ""
        return gate.contributors_at(tip, dirs[0], repo=repo), gate.graph_name_at(tip, dirs[0], repo=repo)

    def create(self, name: str, admin: str) -> str:
        """A bare repo with the gate already installed. Returns the creating admin's
        token: creating a graph and being able to push to it are one act."""
        d = self.graph_dir(name)
        if self.exists(name):
            raise GraphError(f"graph '{name}' already exists on this server")
        if d.exists():
            # Only repo.git used to be checked. A graph deleted by removing repo.git left
            # tokens.json and invites.json behind, and the graph recreated under the same
            # name inherited them: the deleted graph's tokens authenticated on the new one.
            raise GraphError(f"graph '{name}' has a leftover directory on this server; "
                             f"remove {d} first")
        try:
            d.mkdir(mode=0o700, parents=True)
            repo = d / "repo.git"
            # The same config every other git on this server runs under: a core.hooksPath
            # or an init.templateDir in the daemon account's ~/.gitconfig would otherwise
            # make the repo we create and the repo receive-pack sees two different repos.
            # server_git_env(), not {**os.environ, **SERVER_GIT_ENV}: a stray GIT_DIR in
            # the operator's shell outranks `-C` and would point `git init`/`git config`
            # at a repo nobody asked to create or configure.
            env = server_git_env()
            subprocess.run(["git", "init", "-q", "--bare", str(repo)], check=True, env=env)
            for key, value in (("http.receivepack", "true"),
                               ("receive.maxInputSize", str(MAX_PUSH_BYTES)),
                               # A `write` collaborator's stray `--force` rewrote the
                               # shared graph and left no reflog to recover it from and no
                               # line in any log saying it had happened. A shared graph is
                               # append-only: no rewrites, no deletions, every ref update
                               # recorded.
                               ("receive.denyNonFastForwards", "true"),
                               ("receive.denyDeletes", "true"),
                               ("core.logAllRefUpdates", "true"),
                               # A malformed object accepted here is one every clone then
                               # fails to check out, and the server is where it can still
                               # be refused.
                               ("receive.fsckObjects", "true")):
                subprocess.run(["git", "-C", str(repo), "config", key, value],
                               check=True, env=env)
            # server_git_env(), because this server runs receive-pack itself and under
            # exactly that -- and because hook._git uses this env AS GIVEN, with no
            # merge over os.environ, a stray GIT_DIR in the daemon's own environment
            # cannot survive into the `rev-parse --git-path hooks` call install_server
            # makes and redirect it at a different repo's hooks directory. Asked under
            # anything else, git answers with a different hooks directory and the gate
            # is installed where the git that enforces it will never look.
            install_server(repo, env=server_git_env())
            if not (repo / "hooks" / "pre-receive").exists():
                # install_server reported success, but nothing is actually there to
                # enforce the gate -- exactly what a GIT_DIR or core.hooksPath silently
                # redirecting the write would produce. An unsigned push into this repo
                # would then be accepted with no gate at all; refuse instead of serving
                # a graph nobody is actually checking.
                raise GraphError("the gate did not land in the hosted repo; a GIT_DIR or "
                                 "core.hooksPath in the server's environment redirected it")
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
        if len(user) > MAX_NAME:
            raise GraphError(f"contributor name is too long (max {MAX_NAME} characters)")
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
        if not self.exists(name):
            return None
        entry = self._read(name, "tokens.json").get(user or "")
        # .get, not entry["hash"]: a hand-edited or truncated tokens.json then fails
        # closed (no entry matches) instead of a traceback on every request for that user.
        if not entry or not hmac.compare_digest(entry.get("hash", ""), _hash(token)):
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
            # A revoked admin's outstanding invite still redeemed the next day, and it
            # redeemed as admin: revoking their token ended nothing. Their own pending
            # invite goes, and so does every invite they issued.
            invites = self._read(name, "invites.json")
            open_ = {h: e for h, e in invites.items()
                     if e.get("name") != user and e.get("by") != user}
            if len(open_) != len(invites):
                self._write(name, "invites.json", open_)

    # ---------------------------------------------------------------- invites

    def invite(self, name: str, user: str, role: str, days: int = 7, by: str = "",
              blob: str = "", sig: str = "") -> str:
        self._check(name, user, role)
        try:
            days = int(days)
        except (TypeError, ValueError):
            raise GraphError("days must be a whole number") from None
        if not 1 <= days <= MAX_DAYS:
            # `--expires 999999999999` reached timedelta, which raised OverflowError and
            # killed the serving thread with no response at all.
            raise GraphError(f"days must be between 1 and {MAX_DAYS}")
        code = secrets.token_hex(16)
        expires = (_now() + timedelta(days=days)).isoformat()
        with graph_lock(self.graph_dir(name)):
            invites = self._read(name, "invites.json")
            # `by` so revoking an admin can take their outstanding invites with them, and
            # so the list an admin reads says who let each pending person in. `blob`/`sig`
            # are the admin's signed authorisation on a signed graph, kept so the joiner
            # can be handed the same proof they will need for their join commit.
            invites[_hash(code)] = {"name": user, "role": role, "expires": expires, "by": by,
                                    "blob": blob, "sig": sig}
            self._write(name, "invites.json", invites)
        return code

    def invites(self, name: str) -> list[dict]:
        """The open invites, without their hashes. An admin needs to see who is still
        pending; the hash and the signed blob/sig are not for this listing -- it is for
        humans deciding who to expect, not a place to keep the signature alive."""
        self.repo(name)
        return [{"name": e.get("name", ""), "role": e.get("role", ""),
                 "expires": e.get("expires", ""), "by": e.get("by", "")}
                for e in self._read(name, "invites.json").values()]

    def redeem(self, name: str, code: str, contribs_for=None) -> tuple[str, str, str, dict]:
        """One use. Returns (user, role, token, extra) where extra is the admin's signed
        blob/sig and who issued it (empty strings on an unsigned invite). The code is
        removed on first try whether or not it was still live, so an expired code cannot
        be retried.

        `contribs_for`, when given, is a ZERO-ARGUMENT CALLABLE returning the graph's
        current contributors.yaml (or None), called only AFTER the code is confirmed to
        exist and already popped -- a bogus code must not pay for the git read this
        implies, nor leak a misconfigured hosted repo's own error through this route
        before the code itself was even checked. When it does return contributors, a
        signed invite's signature is re-checked against them: the gate would refuse the
        eventual join COMMIT anyway once an admin is revoked, but without this a revoked
        admin's still-unexpired invite minted a live TOKEN here and now, which reads a
        private graph long before that commit is ever attempted."""
        self.repo(name)
        with graph_lock(self.graph_dir(name)):
            invites = self._read(name, "invites.json")
            entry = invites.pop(_hash(code), None)
            if entry is None:
                raise GraphError("that invite code is not valid for this graph")
            self._write(name, "invites.json", invites)
        try:
            # .get and a guarded parse, matching authenticate's fail-closed read: a
            # hand-edited or truncated invites.json used to raise ValueError or KeyError
            # here, which killed the serving thread instead of refusing the code.
            expires = datetime.fromisoformat(entry.get("expires", ""))
        except ValueError:
            raise GraphError("that invite is malformed; ask for a new one") from None
        if expires < _now():
            raise GraphError("that invite has expired; ask the admin for a new one")
        blob, sig = entry.get("blob", ""), entry.get("sig", "")
        if contribs_for is not None:
            try:
                contribs = contribs_for()
            except GraphError:
                # A misconfigured hosted repo (several branches, no HEAD; more than one
                # graph at the tip) must not leak its own message through /join, and must
                # not read any differently from a plain bad code -- both are the one
                # refusal this route ever gives for a code that no longer works.
                raise GraphError("that invite code is not valid for this graph") from None
            if contribs is not None:
                if not (blob and sig):
                    # This invite predates contributors.yaml: minted while the graph was
                    # still phase-1, before there was any admin key to sign it with. It
                    # is not a forgery, but it authorises nothing on a graph that now
                    # requires a signature for every join.
                    raise GraphError("that invite was issued before this graph was signed; "
                                     "ask for a new one")
                try:
                    signer = C.verify_invite(contribs, blob.encode(), sig)
                except GraphError:
                    signer = ""
                if signer != entry.get("by"):
                    # The code is already popped above -- correctly: a code that no
                    # longer proves anything must not be retried into working just as dead.
                    raise GraphError("that invite is no longer valid; its signer is not an admin here any more")
        # Outside the lock: mint takes it again, and flock on a fresh handle would wait
        # on our own lock forever.
        extra = {"blob": blob, "sig": sig, "by": entry.get("by", "")}
        return entry["name"], entry["role"], self.mint(name, entry["name"], entry["role"]), extra

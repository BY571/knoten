"""The server-side gate: what the pre-receive hook execs.

Per pushed ref: find every graph in the pushed tree (a `graph.yaml` blob with a real
`nodes/` tree beside it), unpack it as regular files only, and run the graph's own rules.
Task 4 adds the signature walk in front of that. Anything failing refuses the whole push,
and every refusal is one `knoten:` line on stderr, which git relays to the pusher.

Runs inside the bare repo (git sets the hook's cwd) under SERVER_GIT_ENV, so nothing in
the operator's shell or git config changes what is checked.
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import yaml

from . import contributors as C
from . import ops
from .core import GraphError, SERVER_GIT_ENV, server_git_env
from .keys import allowed_signers

ZERO = re.compile(r"^0+$")
SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")

# Said by check_ref, for a repo that already has a branch, and by main(), for a second
# creation inside ONE push. One wording, because to the pusher it is one rule.
ONE_BRANCH = "a hosted graph has one branch; new branches and tags are refused"


def _git(*args: str, input: bytes | None = None,
         repo: Path | None = None) -> subprocess.CompletedProcess:
    # subprocess.run() rejects stdin= together with input=, so the two are exclusive: an
    # explicit input still pipes it in, but with no input the child gets /dev/null, never
    # the hook's own stdin. That stdin IS git's ref list; a child that read from it instead
    # of getting EOF (`git verify-commit`/gpg, Task 4) would consume lines the outer loop
    # in main() still needs to read.
    kw = {"input": input} if input is not None else {"stdin": subprocess.DEVNULL}
    cmd = ["git", *(["-C", str(repo)] if repo else []), *args]
    if repo:
        # server_git_env(), not {**os.environ, **SERVER_GIT_ENV}: an absolute GIT_DIR left
        # in the operator's shell outranks `-C` and points git at a repo nobody asked for
        # -- Registry.head_graph() reads a HOSTED repo from outside its own process,
        # exactly the case a stray GIT_DIR silently redirects.
        env = server_git_env()
    else:
        # No `repo`: this IS the hook, running inside the bare repo git itself invoked it
        # in. git sets GIT_DIR and, mid-push, the quarantine object-directory variables
        # (GIT_OBJECT_DIRECTORY, GIT_ALTERNATE_OBJECT_DIRECTORIES, GIT_QUARANTINE_PATH) in
        # THIS process's own environment so the hook can see objects not yet migrated
        # into the main odb. Stripping every GIT_* var here (as server_git_env() does)
        # made the hook blind to the very commits it was asked to check.
        env = {**os.environ, **SERVER_GIT_ENV}
    return subprocess.run(cmd, capture_output=True, env=env, **kw)


def say(msg: str) -> None:
    print(f"knoten: {msg}", file=sys.stderr, flush=True)


def _safe_dirs(found: set[str]) -> list[str]:
    """The directory names, sorted, with any one git could read as something other than a
    path refused.

    A directory name is a path lifted from the PUSHED tree, never trusted input. A
    component starting with `-` reaches `git archive`/`git ls-tree` as an OPTION
    (`--output=x` made git write a file; `--remote=host:path` made it shell out to ssh),
    and one starting with `:` is pathspec magic (`:(top)`, `:(exclude)`). Both walks that
    lift a name out of a tree come through here, so no name from a pushed tree ever
    reaches git as anything but a plain path -- `extract`'s `--` separator is defense in
    depth, not the only gate."""
    out = []
    for d in sorted(found):
        if d and any(part.startswith(("-", ":")) for part in d.split("/")):
            say(f"refusing {d}: directory name would be parsed as a git option, not a path")
            raise GraphError(f"unsafe directory name in pushed tree: {d}")
        out.append(d)
    return out


def graph_dirs(rev: str, repo: Path | None = None) -> list[str]:
    """Directories at `rev` that hold a graph: a `graph.yaml` blob and a `nodes` tree.

    Read from the tree listing, never from disk: a symlink is a 120000 blob in git, so it
    can never pass as the `nodes` tree, and no file has to touch the filesystem to be
    ruled out. '' names a graph at the repo root."""
    r = _git("ls-tree", "-r", "-t", "-z", rev, repo=repo)
    if r.returncode != 0:
        raise GraphError(f"cannot read the tree at {rev[:7]}")
    yamls, nodes = set(), set()
    for entry in r.stdout.split(b"\0"):
        if not entry:
            continue
        meta, _, path = entry.partition(b"\t")
        mode, kind, _ = meta.split()
        p = path.decode("utf-8", "surrogateescape")
        parent, _, leaf = p.rpartition("/")
        if leaf == "graph.yaml" and kind == b"blob" and mode in (b"100644", b"100755"):
            yamls.add(parent)
        elif leaf == "nodes" and kind == b"tree":
            nodes.add(parent)
    return _safe_dirs(yamls & nodes)


def contributors_dirs(rev: str, repo: Path | None = None) -> list[str]:
    """Directories at `rev` holding a `contributors.yaml` blob, graph or not.

    `graph_dirs` answers "where are the graphs". This answers "where did somebody sign",
    and the two disagree exactly when a graph's own files are gone while its constitution
    stays -- a tree that must never be read as a phase-1 (unsigned) graph."""
    r = _git("ls-tree", "-r", "--name-only", "-z", rev, repo=repo)
    if r.returncode != 0:
        raise GraphError(f"cannot read the tree at {rev[:7]}")
    found = set()
    for entry in r.stdout.split(b"\0"):
        if not entry:
            continue
        p = entry.decode("utf-8", "surrogateescape")
        parent, _, leaf = p.rpartition("/")
        if leaf == C.FILE:
            found.add(parent)
    return _safe_dirs(found)


def extract(rev: str, gdir: str, dest: Path) -> Path:
    """The graph's files at `rev`, regular files only. A symlink in a pushed tree would
    resolve against the SERVER's filesystem; here it never reaches disk at all."""
    # `--` separates the tree-ish from the pathspec: without it, a pushed directory named
    # like an option (`--output=...`) is parsed as one by `git archive`, not as a path.
    r = _git("archive", "--format=tar", rev, "--", *([gdir] if gdir else []))
    if r.returncode != 0:
        raise GraphError(f"cannot read the tree at {rev[:7]}")
    # 3.12+ warns unless an extraction filter is named; older Pythons have no filter.
    kw = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
    with tarfile.open(fileobj=io.BytesIO(r.stdout)) as tar:
        for m in tar.getmembers():
            if not m.isfile() or ".." in Path(m.name).parts:
                continue
            tar.extract(m, dest, set_attrs=False, **kw)
    return dest / gdir if gdir else dest


def _render(payload: dict) -> None:
    """The violations, the way `knoten validate` prints them, on stderr."""
    for v in payload["violations"]:
        say(f"  ✗ {v['node']}\n          [{v['rule']}] {v['message']}")
    say(f"  {len(payload['violations'])} violation(s)")


def validate_tree(rev: str, gdir: str, ref: str) -> bool:
    with tempfile.TemporaryDirectory() as d:
        root = extract(rev, gdir, Path(d))
        say(f"validating {gdir or '.'}/graph.yaml at {ref}")
        try:
            payload = ops.validate(root)
        except GraphError as e:
            say(str(e))
            return False
        if not payload["valid"]:
            _render(payload)
        return bool(payload["valid"])


def _show(rev: str, path: str, repo: Path | None = None) -> bytes | None:
    r = _git("show", f"{rev}:{path}", repo=repo)
    return r.stdout if r.returncode == 0 else None


def contributors_at(rev: str, gdir: str, repo: Path | None = None) -> dict | None:
    path = f"{gdir}/{C.FILE}" if gdir else C.FILE
    raw = _show(rev, path, repo=repo)
    if raw is None:
        return None
    return C.parse(raw.decode("utf-8", "replace"), f"{path} at {rev[:7]}")


def graph_name_at(rev: str, gdir: str, repo: Path | None = None) -> str:
    raw = _show(rev, f"{gdir}/graph.yaml" if gdir else "graph.yaml", repo=repo) or b""
    try:
        return str((yaml.safe_load(raw) or {}).get("name", ""))
    except yaml.YAMLError:
        return ""


def signature(sha: str, keys: dict[str, str]) -> tuple[str, str]:
    """git's own verdict on the commit's signature against exactly these keys:
    (status, signer). G good; N none; U a key not listed; B bad; E unverifiable.
    An empty key set means nobody may sign, and that is a U, not a crash."""
    if not keys:
        return "U", ""
    with tempfile.NamedTemporaryFile("w", suffix=".signers", delete=False) as f:
        signers = f.name
    try:
        # allowed_signers() runs INSIDE the try: the temp file above already exists on
        # disk the moment NamedTemporaryFile made it, so a raise from a malformed key
        # before the git call must still reach the unlink below, not leak the file.
        with open(signers, "w", encoding="utf-8") as f:
            f.write(allowed_signers(keys, ("git",)))
        # gpg.ssh.program is pinned alongside gpg.format: git otherwise takes the
        # verifier's path from config, and the server-side global config is /dev/null
        # only for the gits knoten runs itself. Naming ssh-keygen here means one program
        # verifies every signature, whatever the box is configured to prefer.
        r = _git("-c", "gpg.format=ssh", "-c", "gpg.ssh.program=ssh-keygen",
                 "-c", f"gpg.ssh.allowedSignersFile={signers}",
                 "log", "-1", "--format=%G?%n%GS", sha)
    finally:
        os.unlink(signers)
    lines = r.stdout.decode("utf-8", "replace").splitlines()
    status = lines[0].strip() if lines else "E"
    signer = lines[1].strip() if len(lines) > 1 else ""
    return status, signer


WHY = {"N": "unsigned", "U": "signed by a key not listed here",
       "B": "bad signature", "E": "signature could not be checked"}


def _touches_only(sha: str, target: str) -> bool:
    """Did this commit change `target` and nothing else? Fails closed on a git error."""
    r = _git("diff-tree", "--no-commit-id", "--name-only", "-r", "-z", f"{sha}^", sha)
    if r.returncode != 0:
        raise GraphError(f"cannot diff {sha[:7]}")
    paths = {p for p in r.stdout.decode("utf-8", "surrogateescape").split("\0") if p}
    return paths == {target}


def check_commit(sha: str, gdir: str, ref: str, has_parent: bool,
                 is_graph: bool = True, parent_dirs: frozenset[str] = frozenset()) -> bool:
    """One commit against the contributors in force BEFORE it.

    Three shapes. A commit that leaves contributors.yaml alone needs any listed writer's
    signature. A commit that adds exactly one entry carrying an admin-signed invite is a
    join, and needs the NEWCOMER's signature (the invite is the admin's part). Anything
    else that touches the file is a change to who may write, and needs an admin. Two
    things are refused outright, whoever signed: a name leaving the file, the whole file
    leaving the gdir included (revoke, never remove), and, under `knoten serve`, a
    bootstrap by anything but the graph's admin token.

    `has_parent` is the caller's own answer to "does this commit have a parent", asked
    once per commit rather than once per (commit, gdir) pair -- check_ref may call this
    for several directories on one sha. `is_graph` and `parent_dirs` say whether `gdir`
    holds a graph AT this commit and which directories held one at its parent; both are
    read once per commit for the same reason."""
    prev = contributors_at(f"{sha}^", gdir) if has_parent else None
    cur = contributors_at(sha, gdir)
    where = f"{ref}: {sha[:7]}"
    if prev is None and cur is None:
        return True                     # a phase-1 graph: unsigned by design
    if prev is None:
        # Bootstrap. Nobody can vouch for the first contributors.yaml but itself, so the
        # commit introducing it must be signed by a key it names as admin; otherwise
        # anyone could install themselves as admin of a phase-1 graph.
        status, signer = signature(sha, C.admins(cur))
        if status != "G":
            say(f"{where} introduces {C.FILE} but is not signed by an admin it lists "
                f"({WHY.get(status, status)})")
            return False
        # A second constitution beside an existing one is not a genesis. Without this, a
        # writer planted a graph in a fresh directory naming themselves its sole admin,
        # and the server's head_graph then died with "holds 2 graphs" for everyone.
        elders = {}
        # Every constitution at the parent, not just the ones with a graph under them: a
        # graph an admin has retired still says who its admins are, and they are exactly
        # the people who may found its successor.
        for d in (contributors_dirs(f"{sha}^") if has_parent else []):
            other = contributors_at(f"{sha}^", d)
            if other is not None:
                elders.update(C.admins(other))
        if elders:
            status, _ = signature(sha, elders)
            if status != "G":
                say(f"{where} starts a second {C.FILE} at {gdir or '.'} and is not signed "
                    f"by an admin of the graph already here ({WHY.get(status, status)})")
                return False
        # `KNOTEN_ROLE` is set by `knoten serve` for the token it authenticated, so its
        # presence means this push came through a knoten server. There the graph's own
        # admin token is the only one that may lay down the first constitution: a `write`
        # collaborator could otherwise bootstrap a phase-1 hosted graph and be its admin.
        # Absent (a `knoten hook --server` repo, which has no tokens at all), nothing here
        # applies and the genesis rule above stands alone.
        if os.environ.get("KNOTEN_ROLE") is not None:
            if (os.environ["KNOTEN_ROLE"] != "admin"
                    or signer != os.environ.get("KNOTEN_PUSHER", "")):
                say(f"{where}: only the graph's admin token may bootstrap {C.FILE}")
                return False
            # Same restriction a join carries, for the same reason: the signature proves
            # who wrote the constitution, not that the rest of the tree was reviewed.
            target = f"{gdir}/{C.FILE}" if gdir else C.FILE
            if has_parent and not _touches_only(sha, target):
                say(f"{where}: a commit that introduces {C.FILE} may change nothing else")
                return False
        return True

    added, changed, removed = ({}, {}, set(prev)) if cur is None else C.diff(prev, cur)
    if removed:
        # Revocation is a mark, so history stays attributable. Dropping an entry erases
        # who could write, and deleting the file outright is the same act in two commits
        # -- delete it, then bootstrap a fresh one that leaves somebody out, which the
        # bootstrap rules would accept because nothing is left to weigh it against. Both
        # are refused whoever signed, so a graph's constitution never stops existing.
        # A directory rename is a deletion here too: it takes the file away from the
        # gdir the entries were written for.
        say(f"{where}: contributors are revoked, never removed")
        return False
    if not added and not changed:
        if gdir in parent_dirs and not is_graph:
            # contributors.yaml untouched, but graph.yaml or nodes/ went away: the
            # directory stops being a graph while still holding a constitution. That is
            # what the server's head_graph can no longer find, so it is an admin's call,
            # not a writer's.
            status, _ = signature(sha, C.admins(prev))
            if status == "G":
                return True
            say(f"{where} stops {gdir or '.'} being a graph and is not signed by an admin "
                f"({WHY.get(status, status)})")
            return False
        status, _ = signature(sha, C.keys(prev))
        if status == "G":
            return True
        say(f"{where} is not signed by a contributor who may write here ({WHY.get(status, status)})")
        return False

    if len(added) == 1 and not changed:
        name, entry = next(iter(added.items()))
        inv = entry.get("invite")
        if isinstance(inv, dict) and inv.get("blob") and inv.get("sig"):
            blob = str(inv["blob"]).encode()
            try:
                by = C.verify_invite(prev, blob, str(inv["sig"]))
                # The graph's name is read at the PARENT, not at sha: reading it at sha
                # let a same-commit rename of graph.yaml's `name:` make an invite issued
                # for one graph verify against whatever name the joiner picked for it in
                # the very commit being judged. At the parent, the name is whatever it
                # was a moment before this commit -- something the invite's signer could
                # actually have seen and signed for.
                C.check_blob(C.parse_blob(blob), graph_name_at(f"{sha}^", gdir), name, entry["role"])
            except GraphError as e:
                say(f"{where}: {e}")
                return False
            if entry.get("invited_by") not in (None, by):
                say(f"{where}: {name}'s entry names a different inviter than the one who signed")
                return False
            # An invite authorises adding exactly one name to contributors.yaml -- nothing
            # about the rest of the tree. Without this, a join commit was accepted
            # wholesale on the invite plus the newcomer's signature, so a `read` invite
            # bought its holder one unrestricted write to the entire graph (any node,
            # graph.yaml, anything else bundled into the same commit).
            target = f"{gdir}/{C.FILE}" if gdir else C.FILE
            if not _touches_only(sha, target):
                say(f"{where}: a join may change nothing but {C.FILE}")
                return False
            # The invite is the admin's half: it authorises this name and role. This is
            # the other half -- the commit must be signed by the very key the entry
            # publishes, so the person who committed the entry is the person who holds
            # that key. The invite itself binds no key, so this does NOT stop someone else
            # arriving with maria's invite under a key of their own: whoever holds the
            # invite (the blob+sig pair) can join as the invited name with any key. That is
            # the invite-by-code model for phase 2 -- an invite grants the invited role
            # under the invited name to whoever holds it; the role cannot be escalated.
            status, _ = signature(sha, {name: entry["key"]})
            if status == "G":
                return True
            say(f"{where} adds {name} but is not signed by {name}'s own key ({WHY.get(status, status)})")
            return False

    status, _ = signature(sha, C.admins(prev))
    if status == "G":
        return True
    say(f"{where} changes {C.FILE} and is not signed by an admin ({WHY.get(status, status)})")
    return False


def check_ref(old: str, new: str, ref: str) -> bool:
    """One hosted graph, one line of history.

    Branches, tags, deletions and rewrites are all ways to put a tree on the server that
    the next `knoten pull` will never look at, or to take one away. `receive.denyDeletes`
    and `receive.denyNonFastForwards` say the same thing in the hosted repo's config, but
    only there: a graph in a bare repo somebody gated with `knoten hook --server` has
    whatever config its operator set. The rule belongs with the gate, which every hosted
    graph runs, and it is checked BEFORE any signature so a refusal names the real
    reason.
    """
    if ZERO.match(new):
        say(f"{ref}: hosted graphs keep their history; refs are not deleted")
        return False
    if ZERO.match(old):
        if not ref.startswith("refs/heads/"):
            # A hosted graph has a branch and nothing else. The branch count below reads
            # `refs/heads/` only, so it cannot see a tag: into a repo with no branch a tag
            # answered "none here", landed, and the branch still landed after it. Counting
            # `refs/` instead would be worse -- a tag would then block the branch forever.
            say(f"{ref}: {ONE_BRANCH}")
            return False
        r = _git("for-each-ref", "--format=%(refname)", "refs/heads/")
        if r.returncode != 0:
            raise GraphError(f"cannot list the branches already here, for {ref}")
        if r.stdout.strip():
            say(f"{ref}: {ONE_BRANCH}")
            return False
        # The first push, into a repo with no branch yet: this ref becomes the only one.
        # `--not --all` still earns its place -- a tag can reach a branchless repo before
        # the branch does -- and it keeps the walk to what is actually new here.
        rng = [new, "--not", "--all"]
    else:
        if _git("merge-base", "--is-ancestor", old, new).returncode != 0:
            # A force push drops commits the gate already accepted and everyone else
            # already pulled. Refused here rather than left to receive.denyNonFastForwards,
            # which is one config edit away from gone.
            say(f"{ref}: not a fast-forward; pull first")
            return False
        rng = [f"{old}..{new}"]
    r = _git("rev-list", "--merges", *rng)
    if r.returncode != 0:
        # A failed rev-list returns empty stdout, same as "no merges here" -- silently
        # treating that as "nothing to check" would let a push through on a git error
        # instead of refusing it.
        raise GraphError(f"cannot list commits for {ref}")
    if r.stdout.strip():
        # "The contributors before this commit" has two answers at a merge. knoten's
        # own pull is --ff-only; say so instead of picking a parent.
        say(f"{ref}: merge commits are not accepted here; rebase onto the remote and push again")
        return False
    r = _git("rev-list", "--reverse", *rng)
    if r.returncode != 0:
        raise GraphError(f"cannot list commits for {ref}")
    commits = r.stdout.decode().split()
    ok = True
    for sha in commits:
        has_parent = _git("rev-parse", "--verify", "-q", f"{sha}^").returncode == 0
        # A gdir can vanish between a commit and its parent -- moved elsewhere, or deleted
        # outright. Checking only graph_dirs(sha) let a commit do either to g and skip the
        # per-commit signature check entirely, since g was gone from the tree being
        # examined. Union in graph_dirs at the parent too, so "g disappeared here" is
        # still checked against who could write to g a moment before this commit.
        now = frozenset(graph_dirs(sha))
        before = frozenset(graph_dirs(f"{sha}^")) if has_parent else frozenset()
        # Every rule in check_commit keys on contributors.yaml, but the loop used to
        # visit only directories that hold a GRAPH at this commit or its parent. A
        # directory with a constitution and no graph was therefore judged by nobody: a
        # writer could plant `mine/contributors.yaml` naming themselves admin (invisible,
        # `mine` is not a graph dir) and add `mine/graph.yaml` and `mine/nodes/` in a
        # second commit, where the constitution now reads as unchanged and is checked
        # against their own key. The same blind spot let a writer rewrite the constitution
        # of a graph an admin had retired, dropping the admin, while its directory held no
        # graph at either end. Watch wherever a constitution is or was, not where a graph is.
        watched = now | before | frozenset(contributors_dirs(sha))
        if has_parent:
            watched |= frozenset(contributors_dirs(f"{sha}^"))
        for gdir in sorted(watched):
            if not check_commit(sha, gdir, ref, has_parent,
                                is_graph=gdir in now, parent_dirs=before):
                ok = False
    if not ok:
        return False
    for gdir in graph_dirs(new):
        if not validate_tree(new, gdir, ref):
            ok = False
    return ok


def main(stdin=None) -> int:
    ok = True
    born = False
    for line in (stdin or sys.stdin):
        parts = line.split()
        if len(parts) != 3:
            continue
        old, new, ref = parts
        if not (SHA_RE.match(old) and SHA_RE.match(new)):
            # old/new reach `check_ref` and from there `rev-list`/`archive` as revisions;
            # anything not shaped like an object id would be parsed there as an option
            # (`--output=x`) instead of refused as garbage input.
            say(f"malformed ref line for {ref}")
            ok = False
            continue
        if ZERO.match(old):
            # check_ref asks git which branches exist, and mid-push the answer is the
            # same for every line: no ref has moved yet when pre-receive runs. So a
            # `git push --all` into a repo with no branch read "none here" once per ref
            # and created them all. The count of creations in THIS push is kept here,
            # where the lines are read, because nothing git can be asked knows it.
            if born:
                say(f"{ref}: {ONE_BRANCH}, and one refused ref refuses the whole push")
                ok = False
                continue
            # Only a branch spends it. A tag is refused by check_ref whatever else is in
            # the push, and letting it spend the allowance made the BRANCH line carry the
            # refusal -- the pusher then read the wrong ref as the problem.
            born = ref.startswith("refs/heads/")
        try:
            if not check_ref(old, new, ref):
                ok = False
        except GraphError as e:
            say(str(e))
            ok = False
        except Exception as e:
            # A traceback here goes out on band 2, which git relays to the PUSHER -- a bug
            # anywhere under check_ref must still fail closed with one line, not leak a
            # server-side stack trace (module names, paths) to whoever pushed.
            say(f"cannot check {ref}: {type(e).__name__}")
            ok = False
    if not ok:
        say("push REFUSED. Fix the graph, commit, push again.")
    return 0 if ok else 1

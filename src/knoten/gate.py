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
from .core import GraphError, SERVER_GIT_ENV
from .keys import allowed_signers

ZERO = re.compile(r"^0+$")
SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")


def _git(*args: str, input: bytes | None = None) -> subprocess.CompletedProcess:
    # subprocess.run() rejects stdin= together with input=, so the two are exclusive: an
    # explicit input still pipes it in, but with no input the child gets /dev/null, never
    # the hook's own stdin. That stdin IS git's ref list; a child that read from it instead
    # of getting EOF (`git verify-commit`/gpg, Task 4) would consume lines the outer loop
    # in main() still needs to read.
    kw = {"input": input} if input is not None else {"stdin": subprocess.DEVNULL}
    return subprocess.run(["git", *args], capture_output=True,
                          env={**os.environ, **SERVER_GIT_ENV}, **kw)


def say(msg: str) -> None:
    print(f"knoten: {msg}", file=sys.stderr, flush=True)


def graph_dirs(rev: str) -> list[str]:
    """Directories at `rev` that hold a graph: a `graph.yaml` blob and a `nodes` tree.

    Read from the tree listing, never from disk: a symlink is a 120000 blob in git, so it
    can never pass as the `nodes` tree, and no file has to touch the filesystem to be
    ruled out. '' names a graph at the repo root."""
    r = _git("ls-tree", "-r", "-t", "-z", rev)
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
    safe = []
    for d in sorted(yamls & nodes):
        # A directory name is a path lifted from the PUSHED tree, never trusted input. A
        # component starting with `-` reaches `git archive`/`git ls-tree` as an OPTION
        # (`--output=x` made git write a file; `--remote=host:path` made it shell out to
        # ssh), and one starting with `:` is pathspec magic (`:(top)`, `:(exclude)`). Ruling
        # both out here means no name from a pushed tree ever reaches git as anything but
        # a plain path -- `extract`'s `--` separator is defense in depth, not the only gate.
        if d and any(part.startswith(("-", ":")) for part in d.split("/")):
            say(f"refusing {d}: directory name would be parsed as a git option, not a path")
            raise GraphError(f"unsafe directory name in pushed tree: {d}")
        safe.append(d)
    return safe


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


def _show(rev: str, path: str) -> bytes | None:
    r = _git("show", f"{rev}:{path}")
    return r.stdout if r.returncode == 0 else None


def contributors_at(rev: str, gdir: str) -> dict | None:
    path = f"{gdir}/{C.FILE}" if gdir else C.FILE
    raw = _show(rev, path)
    if raw is None:
        return None
    return C.parse(raw.decode("utf-8", "replace"), f"{path} at {rev[:7]}")


def graph_name_at(rev: str, gdir: str) -> str:
    raw = _show(rev, f"{gdir}/graph.yaml" if gdir else "graph.yaml") or b""
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
        f.write(allowed_signers(keys, ("git",)))
        signers = f.name
    try:
        r = _git("-c", "gpg.format=ssh", "-c", f"gpg.ssh.allowedSignersFile={signers}",
                 "log", "-1", "--format=%G?%n%GS", sha)
    finally:
        os.unlink(signers)
    lines = r.stdout.decode("utf-8", "replace").splitlines()
    status = lines[0].strip() if lines else "E"
    signer = lines[1].strip() if len(lines) > 1 else ""
    return status, signer


WHY = {"N": "unsigned", "U": "signed by a key not listed by the graph for that",
       "B": "bad signature", "E": "signature could not be checked"}


def check_commit(sha: str, gdir: str, ref: str) -> bool:
    """One commit against the contributors in force BEFORE it. Task 5 adds the rule for
    commits that change contributors.yaml itself."""
    has_parent = _git("rev-parse", "--verify", "-q", f"{sha}^").returncode == 0
    prev = contributors_at(f"{sha}^", gdir) if has_parent else None
    cur = contributors_at(sha, gdir)
    where = f"{ref}: {sha[:7]}"
    if prev is None and cur is None:
        return True                     # a phase-1 graph: unsigned by design
    if prev is None:
        # Bootstrap. Nobody can vouch for the first contributors.yaml but itself, so the
        # commit introducing it must be signed by a key it names as admin; otherwise
        # anyone could install themselves as admin of a phase-1 graph.
        status, _ = signature(sha, C.admins(cur))
        if status == "G":
            return True
        say(f"{where} introduces {C.FILE} but is not signed by an admin it lists "
            f"({WHY.get(status, status)})")
        return False
    status, _ = signature(sha, C.keys(prev))
    if status == "G":
        return True
    say(f"{where} is not signed by a contributor who may write here ({WHY.get(status, status)})")
    return False


def check_ref(old: str, new: str, ref: str) -> bool:
    if ZERO.match(new):                 # a deletion carries no tree to check
        return True
    rng = [new] if ZERO.match(old) else [f"{old}..{new}"]
    if _git("rev-list", "--merges", *rng).stdout.strip():
        # "The contributors before this commit" has two answers at a merge. knoten's
        # own pull is --ff-only; say so instead of picking a parent.
        say(f"{ref}: merge commits are not accepted here; rebase onto the remote and push again")
        return False
    commits = _git("rev-list", "--reverse", *rng).stdout.decode().split()
    ok = True
    for sha in commits:
        for gdir in graph_dirs(sha):
            if not check_commit(sha, gdir, ref):
                ok = False
    if not ok:
        return False
    for gdir in graph_dirs(new):
        if not validate_tree(new, gdir, ref):
            ok = False
    return ok


def main(stdin=None) -> int:
    ok = True
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

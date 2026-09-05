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

from . import ops
from .core import GraphError, SERVER_GIT_ENV

ZERO = re.compile(r"^0+$")


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


def check_ref(old: str, new: str, ref: str) -> bool:
    """Task 4 puts the signature walk in front of the validation."""
    if ZERO.match(new):                 # a deletion carries no tree to check
        return True
    ok = True
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

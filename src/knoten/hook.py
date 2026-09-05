"""The git pre-commit gate.

`knoten validate` has always printed "commit REJECTED". Nothing rejected a commit —
git wrote the invalid graph to history without complaint, and the phrase was a bluff.

A rule that only fires when you remember to ask is the same rule that let the previous
attempt (`knowledge_graph.jsonl`, still zero bytes) rot. The gate has to sit in the one
place you cannot forget to walk through.

We ask GIT where its hooks live rather than assuming `.git/hooks`. That assumption is
wrong in three common cases — `core.hooksPath` (husky, the pre-commit framework, most
monorepos), worktrees and submodules (where `.git` is a FILE, not a directory) — and
being wrong here means writing the hook somewhere git never reads, reporting success,
and silently not gating. Which is precisely the failure this module exists to prevent.
"""
from __future__ import annotations

import stat
import subprocess
from pathlib import Path

from .core import GraphError

MARKER = "# knoten pre-commit gate"

HOOK = f"""\
#!/bin/sh
{MARKER} — installed by `knoten hook`. Delete this file to remove it.
#
# A graph you can commit broken is a wiki with extra steps.
# To bypass once (you should have a reason):  git commit --no-verify

if ! command -v knoten >/dev/null 2>&1; then
    echo "knoten: not on PATH — activate the environment knoten is installed in," >&2
    echo "        or bypass with: git commit --no-verify" >&2
    exit 1
fi

cd "$(git rev-parse --show-toplevel)/{{graph}}" || exit 1
exec knoten validate
"""


def _git(root: Path, *args: str, env: dict | None = None) -> str:
    try:
        # env AS GIVEN, never merged with os.environ: a caller that passes an env is
        # asserting "this is the complete environment", server_git_env() among them --
        # merging os.environ back in here let a stray GIT_DIR survive every filter the
        # caller applied and point `rev-parse --git-path hooks` at a repo nobody asked
        # for. `env=None` (no caller-supplied env, the client `hook.install` path) still
        # inherits the parent's environment in full, same as subprocess.run's own default.
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                           env=env)
    except FileNotFoundError as e:
        raise GraphError("git is not installed") from e
    if r.returncode != 0:
        raise GraphError(f"{root} is not inside a git repository — run `git init` first")
    return r.stdout.strip()


def hooks_dir(root: Path, env: dict | None = None) -> Path:
    """Where git ACTUALLY reads hooks from — not where we guess it does.

    `env` is how the server side asks the same question the server-side git will answer:
    ask it under a different config and you write the hook where nothing runs it.
    """
    p = Path(_git(root, "rev-parse", "--git-path", "hooks", env=env))
    return p if p.is_absolute() else (root / p).resolve()


def _write_hook(root: Path, name: str, marker: str, body: str, force: bool,
                env: dict | None = None) -> Path:
    """Put a hook where git actually reads it, without clobbering one somebody wrote.

    Shared so the two gates cannot drift on the clobber rule, which is the half a reader
    has to trust rather than check.
    """
    hooks = hooks_dir(root, env)
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / name

    if hook.exists() and marker not in hook.read_text(encoding="utf-8") and not force:
        raise GraphError(
            f"{hook} already exists and knoten did not write it. Refusing to clobber a "
            f"hook you wrote. Re-run with --force, or merge knoten's check into it yourself."
        )

    hook.write_text(body, encoding="utf-8")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return hook


def install(root: Path, force: bool = False) -> Path:
    repo = Path(_git(root, "rev-parse", "--show-toplevel"))
    graph = root.resolve().relative_to(repo.resolve())
    return _write_hook(root, "pre-commit", MARKER,
                       HOOK.format(graph=graph.as_posix() or "."), force)


# --------------------------------------------------------------- the server-side gate
#
# The gate above runs in one clone and `--no-verify` walks past it. This one runs on the
# repo everyone pushes TO, so it cannot be skipped from a laptop.
#
# The script itself only proves knoten is on PATH, fail-closed, then execs `knoten gate`
# (src/knoten/gate.py), which reads the pushed refs from stdin, finds every graph in each
# pushed tree, unpacks it as regular files only, and runs its rules. That logic moved out
# of shell because later checks (signatures, the constitution rule) are not shell.

SERVER_MARKER = "# knoten pre-receive gate"

SERVER_HOOK = """\
#!/bin/sh
""" + SERVER_MARKER + """ - installed by `knoten hook --server`. Delete this file to remove it.
#
# Refuses a push whose graph breaks its own rules, or whose commits are not signed by
# someone the graph lists, BEFORE the ref moves. The checks live in `knoten gate`; this
# script only makes sure knoten is there to run them.

if ! command -v knoten >/dev/null 2>&1; then
    echo "knoten: not on PATH on the server, so this gate cannot check anything." >&2
    echo "        Refusing the push: a gate that waves work through when it cannot" >&2
    echo "        check it is not a gate. Install knoten for the user owning this repo." >&2
    exit 1
fi

exec knoten gate
"""


def install_server(repo: Path, force: bool = False, env: dict | None = None) -> Path:
    """Install the pre-receive gate into the repo everyone pushes to.

    Takes the repo rather than a graph: a bare repo has no working tree, so there is no
    `graph.yaml` here to read and no graph path worth recording. The hook finds the
    graphs in each pushed tree instead.

    `env` must be whatever the receive-pack that will ENFORCE this gate runs under, since
    that is what decides where hooks are read from. `knoten serve` owns the repo and runs
    receive-pack itself, so `Registry.create` passes `server_git_env()` -- the COMPLETE
    environment, not a few keys merged over the caller's own os.environ: a stray GIT_DIR
    or core.hooksPath left in the daemon's environment must not survive into this call
    and redirect where the gate gets written. `knoten hook --server` passes no env at
    all: that repo is hosted by nginx or sshd under the operator's own account,
    receive-pack reads their ~/.gitconfig, and forcing a server env here wrote the gate
    to repo.git/hooks while git went looking at their core.hooksPath. The gate then
    failed OPEN, which is the one way for it to be wrong and still report green.
    """
    return _write_hook(repo, "pre-receive", SERVER_MARKER, SERVER_HOOK, force, env=env)

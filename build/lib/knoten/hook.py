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


def _git(root: Path, *args: str) -> str:
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    except FileNotFoundError as e:
        raise GraphError("git is not installed") from e
    if r.returncode != 0:
        raise GraphError(f"{root} is not inside a git repository — run `git init` first")
    return r.stdout.strip()


def hooks_dir(root: Path) -> Path:
    """Where git ACTUALLY reads hooks from — not where we guess it does."""
    p = Path(_git(root, "rev-parse", "--git-path", "hooks"))
    return p if p.is_absolute() else (root / p).resolve()


def install(root: Path, force: bool = False) -> Path:
    repo = Path(_git(root, "rev-parse", "--show-toplevel"))
    hooks = hooks_dir(root)
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-commit"

    if hook.exists() and MARKER not in hook.read_text(encoding="utf-8") and not force:
        raise GraphError(
            f"{hook} already exists and knoten did not write it. Refusing to clobber a "
            f"hook you wrote. Re-run with --force, or add `knoten validate` to it yourself."
        )

    graph = root.resolve().relative_to(repo.resolve())
    hook.write_text(HOOK.format(graph=graph.as_posix() or "."), encoding="utf-8")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return hook

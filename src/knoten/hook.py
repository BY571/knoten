"""The git pre-commit gate.

`knoten validate` has always printed "commit REJECTED". Nothing rejected a commit —
git wrote the invalid graph to history without complaint, and the phrase was a bluff.

A rule that only fires when you remember to ask is the same rule that let the previous
attempt (`knowledge_graph.jsonl`, still zero bytes) rot. The gate has to sit in the one
place you cannot forget to walk through.
"""
from __future__ import annotations

import stat
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


def git_root(start: Path) -> Path | None:
    """The repo containing this graph. `.git` is a directory, or a file in a worktree."""
    for c in [start, *start.parents]:
        if (c / ".git").exists():
            return c
    return None


def install(root: Path, force: bool = False) -> Path:
    repo = git_root(root)
    if repo is None:
        raise GraphError(f"{root} is not inside a git repository — run `git init` first")

    hooks = repo / ".git" / "hooks"
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

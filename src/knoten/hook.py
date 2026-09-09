"""The git pre-commit gate.

A rule that only fires when you remember to ask is a rule that rots. The gate sits in the
one place you cannot forget to walk through.

We ask GIT where its hooks live rather than assuming `.git/hooks`, which is wrong under
`core.hooksPath` (husky, monorepos) and in worktrees and submodules, where `.git` is a
FILE. Being wrong there means writing the hook where git never reads it and reporting
success: the exact failure this module exists to prevent.
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
    """Where git ACTUALLY reads hooks from."""
    p = Path(_git(root, "rev-parse", "--git-path", "hooks"))
    return p if p.is_absolute() else (root / p).resolve()


def _write_hook(root: Path, name: str, marker: str, body: str, force: bool) -> Path:
    """Put a hook where git actually reads it, without clobbering one somebody wrote."""
    hooks = hooks_dir(root)
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
    """The gate, and the two settings that keep a shared graph one line of history:
    `git pull` rebases (never a merge commit) and a half-written node on disk does not
    block it. Set here, not only by `init`, because a clone starts with neither and
    `knoten hook` is the one command every clone runs."""
    repo = Path(_git(root, "rev-parse", "--show-toplevel"))
    graph = root.resolve().relative_to(repo.resolve())
    _git(root, "config", "pull.rebase", "true")
    _git(root, "config", "rebase.autoStash", "true")
    return _write_hook(root, "pre-commit", MARKER,
                       HOOK.format(graph=graph.as_posix() or "."), force)

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


# --------------------------------------------------------------- the server-side gate
#
# The client hook above gates one person, in one clone, and `--no-verify` walks past it.
# On a shared graph the normal contributor is one who never ran `knoten hook`. This one
# runs on the repo everyone pushes TO, so it sees everybody's work and cannot be skipped
# from a laptop.

SERVER_MARKER = "# knoten pre-receive gate"

SERVER_HOOK = """\
#!/bin/sh
""" + SERVER_MARKER + """ - installed by `knoten hook --server`. Delete this file to remove it.
#
# Refuses a push whose graph breaks its own rules, BEFORE the ref moves. Unlike CI this
# needs no runner and no minutes, and unlike the client hook it cannot be bypassed.

ZERO=0000000000000000000000000000000000000000

if ! command -v knoten >/dev/null 2>&1; then
    echo "knoten: not on PATH on the server, so this gate cannot check anything." >&2
    echo "        Refusing the push: a gate that waves work through when it cannot" >&2
    echo "        check it is not a gate. Install knoten for the user owning this repo." >&2
    exit 1
fi

work=$(mktemp -d) || exit 1
trap 'rm -rf "$work"' EXIT
failed=$work/failed

while read -r old new ref; do
    [ "$new" = "$ZERO" ] && continue    # a branch deletion carries no tree to check

    tree=$work/tree
    rm -rf "$tree" && mkdir -p "$tree" || exit 1
    # Two steps, not a pipe: in POSIX sh a pipeline reports only the LAST command's
    # status, so `git archive | tar` hides a failed archive behind a happy tar and the
    # push sails through unchecked.
    git archive "$new" > "$work/tree.tar" || { echo "knoten: cannot read $ref" >&2; exit 1; }
    tar -xf "$work/tree.tar" -C "$tree"  || { echo "knoten: cannot read $ref" >&2; exit 1; }

    # The graph is FOUND, not configured. A path recorded at install time rots the moment
    # someone moves the folder, and rots silently: the hook then finds no graph and
    # accepts everything, reporting green.
    find "$tree" -name graph.yaml -type f > "$work/graphs"
    while IFS= read -r cfg; do
        [ -n "$cfg" ] || continue
        dir=$(dirname "$cfg")
        # `graph.yaml` is not a name knoten owns. Another tool's config of the same name
        # fails validation, and treating it as a graph would make the WHOLE repo
        # unpushable forever, citing a file nobody thinks of as a graph. So: a graph is
        # one that has a nodes/ directory, or declares a key only knoten declares.
        # Two tests, because neither alone is enough - git does not track an empty
        # nodes/, so a graph whose nodes were all deleted would slip through the first,
        # and a graph.yaml too malformed to name its keys would slip through the second.
        [ -d "$dir/nodes" ] || grep -qE '^(node_types|statuses|rules|tags):' "$cfg" || continue
        echo "knoten: validating ${cfg#$tree/} at $ref" >&2
        ( cd "$dir" && knoten validate ) || : > "$failed"
    done < "$work/graphs"
done

if [ -e "$failed" ]; then
    echo "knoten: push REFUSED. Fix the graph, commit, push again." >&2
    exit 1
fi
exit 0
"""


def install_server(repo: Path, force: bool = False) -> Path:
    """Install the pre-receive gate into the repo everyone pushes to.

    Takes the repo rather than a graph: a bare repo has no working tree, so there is no
    `graph.yaml` here to read and no graph path worth recording. The hook finds the
    graphs in each pushed tree instead.
    """
    _git(repo, "rev-parse", "--git-dir")      # raises unless this really is a repo
    hooks = hooks_dir(repo)
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-receive"

    if hook.exists() and SERVER_MARKER not in hook.read_text(encoding="utf-8") and not force:
        raise GraphError(
            f"{hook} already exists and knoten did not write it. Refusing to clobber a "
            f"hook you wrote. Re-run with --force, or add `knoten validate` to it yourself."
        )

    hook.write_text(SERVER_HOOK, encoding="utf-8")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return hook

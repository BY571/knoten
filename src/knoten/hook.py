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


def _write_hook(root: Path, name: str, marker: str, body: str, force: bool) -> Path:
    """Put a hook where git actually reads it, without clobbering one somebody wrote.

    Shared so the two gates cannot drift on the clobber rule, which is the half a reader
    has to trust rather than check.
    """
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
    repo = Path(_git(root, "rev-parse", "--show-toplevel"))
    graph = root.resolve().relative_to(repo.resolve())
    return _write_hook(root, "pre-commit", MARKER,
                       HOOK.format(graph=graph.as_posix() or "."), force)


# --------------------------------------------------------------- the server-side gate
#
# The gate above runs in one clone and `--no-verify` walks past it. This one runs on the
# repo everyone pushes TO, so it cannot be skipped from a laptop.

SERVER_MARKER = "# knoten pre-receive gate"

SERVER_HOOK = """\
#!/bin/sh
""" + SERVER_MARKER + """ - installed by `knoten hook --server`. Delete this file to remove it.
#
# Refuses a push whose graph breaks its own rules, BEFORE the ref moves. Unlike CI this
# needs no runner and no minutes, and unlike the client hook it cannot be bypassed.

if ! command -v knoten >/dev/null 2>&1; then
    echo "knoten: not on PATH on the server, so this gate cannot check anything." >&2
    echo "        Refusing the push: a gate that waves work through when it cannot" >&2
    echo "        check it is not a gate. Install knoten for the user owning this repo." >&2
    exit 1
fi

work=$(mktemp -d) || exit 1
trap 'rm -rf "$work"' EXIT
failed=$work/failed
tree=$work/tree

while read -r old new ref; do
    # An all-zero oid is a deletion: no tree to check. Matched by shape rather than
    # against a 40-zero literal, which would miss the 64 zeros a SHA-256 repo sends.
    case "$new" in *[!0]*) ;; *) continue ;; esac

    rm -rf "$tree" && mkdir -p "$tree" || exit 1
    # Two statements, not a pipe: in POSIX sh a pipeline reports only the LAST command's
    # status, so `git archive | tar` would hide a failed archive behind a happy tar and
    # the push would sail through unchecked. `||` short-circuits, so tar never runs on a
    # failed archive.
    if ! git archive "$new" > "$work/tree.tar" || ! tar -xf "$work/tree.tar" -C "$tree"; then
        echo "knoten: cannot read $ref" >&2
        exit 1
    fi

    # The graph is FOUND, not configured. A path recorded at install time rots the moment
    # someone moves the folder, and rots silently: the hook then finds no graph and
    # accepts everything, reporting green.
    find "$tree" -name graph.yaml -type f > "$work/graphs"
    while IFS= read -r cfg; do
        dir=$(dirname "$cfg")
        # `graph.yaml` is not a name knoten owns, and `validate` rejects unknown keys.
        # Treating another tool's config of that name as a graph would make the WHOLE
        # repo unpushable forever, citing a file nobody thinks of as a graph. A graph
        # has nodes/ next to it.
        [ -d "$dir/nodes" ] || continue
        echo "knoten: validating ${cfg#"$tree"/} at $ref" >&2
        # </dev/null so validate cannot consume the ref list this loop is reading.
        ( cd "$dir" && knoten validate ) </dev/null || : > "$failed"
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
    return _write_hook(repo, "pre-receive", SERVER_MARKER, SERVER_HOOK, force)

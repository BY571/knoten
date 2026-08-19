"""`knoten validate` prints 'commit REJECTED'. Until now nothing rejected a commit —
git committed an invalid graph without complaint. The phrase was a bluff."""
import subprocess

import pytest

from knoten.cli import main
from knoten.core import GraphError
from knoten.hook import install

ALIVE_NO_GATE = "id: hyp-x\ntype: hypothesis\nstatus: alive"


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


@pytest.fixture
def repo(graph):
    """A graph that is also a git repo, with one committed node."""
    git("init", "-q", cwd=graph.root)
    git("config", "user.email", "t@t.t", cwd=graph.root)
    git("config", "user.name", "t", cwd=graph.root)
    graph.node("gate-cost", "id: gate-cost\ntype: gate")
    return graph



def test_the_hook_actually_blocks_a_commit(repo):
    """The whole point. An `alive` claim citing no gate must not reach history."""
    install(repo.root)
    repo.node("hyp-x", ALIVE_NO_GATE)
    git("add", "-A", cwd=repo.root)

    r = git("commit", "-m", "sneak in an unchallenged claim", cwd=repo.root)

    assert r.returncode != 0
    assert "live-claims-must-cite-their-gates" in r.stdout + r.stderr
    assert git("log", "--oneline", cwd=repo.root).returncode != 0  # no commits exist


def test_the_hook_lets_a_valid_graph_through(repo):
    install(repo.root)
    git("add", "-A", cwd=repo.root)

    r = git("commit", "-m", "a clean graph", cwd=repo.root)

    assert r.returncode == 0, r.stdout + r.stderr


def test_the_hook_works_when_the_graph_is_a_subdirectory(tmp_path, rules_yaml):
    """A graph is often one folder inside a bigger repo. The hook runs from the repo
    root, so it has to cd into the graph."""
    repo = tmp_path / "monorepo"
    sub = repo / "research"
    (sub / "nodes").mkdir(parents=True)
    (sub / "graph.yaml").write_text(rules_yaml, encoding="utf-8")
    (sub / "nodes" / "hyp-x.md").write_text(f"---\n{ALIVE_NO_GATE}\n---\n# x\n", encoding="utf-8")
    for cmd in (["init", "-q"], ["config", "user.email", "t@t.t"], ["config", "user.name", "t"]):
        git(*cmd, cwd=repo)

    install(sub)
    git("add", "-A", cwd=repo)
    r = git("commit", "-m", "bad", cwd=repo)

    assert r.returncode != 0
    assert "live-claims-must-cite-their-gates" in r.stdout + r.stderr


def test_the_hook_honours_core_hookspath(repo):
    """husky, the pre-commit framework, and most monorepos set core.hooksPath. Writing to
    .git/hooks then means writing to a directory git will NEVER look at — so `hook install`
    reported success and the gate silently never ran. That is the exact failure this
    feature exists to eliminate."""
    git("config", "core.hooksPath", ".githooks", cwd=repo.root)

    install(repo.root)

    assert (repo.root / ".githooks" / "pre-commit").exists()

    repo.node("hyp-x", ALIVE_NO_GATE)
    git("add", "-A", cwd=repo.root)
    r = git("commit", "-m", "sneak it in", cwd=repo.root)

    assert r.returncode != 0
    assert "live-claims-must-cite-their-gates" in r.stdout + r.stderr


def test_install_works_in_a_git_worktree(tmp_path, graph):
    """In a worktree (and a submodule) `.git` is a FILE, so `repo/.git/hooks` raised a raw
    NotADirectoryError."""
    git("init", "-q", cwd=graph.root)
    git("config", "user.email", "t@t.t", cwd=graph.root)
    git("config", "user.name", "t", cwd=graph.root)
    graph.node("gate-cost", "id: gate-cost\ntype: gate")
    git("add", "-A", cwd=graph.root)
    git("commit", "-qm", "init", cwd=graph.root)

    wt = tmp_path.parent / "wt"
    git("worktree", "add", "-q", str(wt), cwd=graph.root)

    hook = install(wt)          # must not raise

    assert hook.exists()
    assert "knoten validate" in hook.read_text()


def test_install_refuses_outside_a_git_repo(graph):
    with pytest.raises(GraphError, match="git"):
        install(graph.root)


def test_install_refuses_to_clobber_a_hook_it_did_not_write(repo):
    hook = repo.root / ".git" / "hooks"
    hook.mkdir(parents=True, exist_ok=True)
    (hook / "pre-commit").write_text("#!/bin/sh\necho mine\n", encoding="utf-8")

    with pytest.raises(GraphError, match="already"):
        install(repo.root)

    assert "echo mine" in (hook / "pre-commit").read_text()


def test_install_overwrites_its_own_hook(repo):
    install(repo.root)
    install(repo.root)          # idempotent, no error

    assert (repo.root / ".git" / "hooks" / "pre-commit").read_text().count("knoten") >= 1


def test_force_clobbers_a_foreign_hook(repo):
    hooks = repo.root / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "pre-commit").write_text("#!/bin/sh\necho mine\n", encoding="utf-8")

    install(repo.root, force=True)

    assert "knoten validate" in (hooks / "pre-commit").read_text()


def test_cli_exposes_hook_install(repo, monkeypatch):
    monkeypatch.chdir(repo.root)

    assert main(["hook"]) == 0
    assert (repo.root / ".git" / "hooks" / "pre-commit").exists()

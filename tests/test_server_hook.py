"""`knoten hook` gates the person who ran it."""
import os
import shutil
import subprocess

import pytest
from conftest import git

from knoten.cli import main
from knoten.core import GraphError
from knoten.hook import SERVER_MARKER, install_server

ALIVE_NO_GATE = "---\nid: hyp-x\ntype: hypothesis\nstatus: alive\n---\n\n# x\n"


@pytest.fixture
def server(tmp_path, rules_yaml):
    """A bare repo with the gate installed, and a clone to push from."""
    bare = tmp_path / "origin.git"
    git("init", "-q", "--bare", str(bare), cwd=tmp_path)
    install_server(bare)
    work = tmp_path / "work"
    git("clone", "-q", str(bare), str(work), cwd=tmp_path)
    for c in (["config", "user.email", "t@t.t"], ["config", "user.name", "t"]):
        git(*c, cwd=work)
    graph = work / "g"
    (graph / "nodes").mkdir(parents=True)
    (graph / "graph.yaml").write_text(rules_yaml, encoding="utf-8")
    (graph / "nodes" / "hyp-ok.md").write_text(
        "---\nid: hyp-ok\ntype: hypothesis\nstatus: open\n---\n\n# a claim nobody disputes\n",
        encoding="utf-8")
    return bare, work


def commit(work, msg):
    git("add", "-A", cwd=work)
    return git("commit", "-qm", msg, cwd=work)


def push(work, env=None):
    return git("push", "origin", "master", cwd=work, env=env)


def history(bare):
    return git("log", "--oneline", cwd=bare).stdout


# ---------------------------------------------------------------- the gate itself

def test_it_rejects_a_push_that_breaks_the_graphs_rules(server):
    """The whole point. An `alive` claim citing no gate must not reach the shared repo."""
    bare, work = server
    commit(work, "a clean graph")
    assert push(work).returncode == 0, "the clean graph should have been accepted"

    (work / "g" / "nodes" / "hyp-x.md").write_text(ALIVE_NO_GATE, encoding="utf-8")
    commit(work, "sneak in an unchallenged claim")

    r = push(work)

    assert r.returncode != 0
    assert "live-claims-must-cite-their-gates" in r.stdout + r.stderr
    assert "unchallenged" not in history(bare), "the bad commit reached the server"


def test_it_finds_the_graph_without_being_told_where_it_is(server):
    bare, work = server
    shutil.move(work / "g", work / "research" / "graph")
    commit(work, "move the graph two levels down")
    (work / "research" / "graph" / "nodes" / "hyp-x.md").write_text(ALIVE_NO_GATE,
                                                                   encoding="utf-8")
    commit(work, "still has to be caught")
    assert push(work).returncode != 0
    assert history(bare) == ""


def test_it_fails_closed_when_knoten_is_not_on_the_server_path(server):
    bare, work = server
    (work / "g" / "nodes" / "hyp-x.md").write_text(ALIVE_NO_GATE, encoding="utf-8")
    commit(work, "an unchallenged claim")
    # git must stay reachable — it is git that runs the hook. Only knoten goes missing.
    bare_path = os.pathsep.join(d for d in ("/usr/bin", "/bin")
                                if not shutil.which("knoten", path=d))
    assert not shutil.which("knoten", path=bare_path), "need a PATH without knoten"
    r = push(work, env={"PATH": bare_path})
    assert r.returncode != 0
    assert "knoten" in (r.stdout + r.stderr)
    assert history(bare) == ""


# ---------------------------------------------------------------- installation

def test_it_refuses_to_clobber_a_hook_it_did_not_write(tmp_path):
    """Somebody else's pre-receive hook is somebody else's policy."""
    bare = tmp_path / "origin.git"
    git("init", "-q", "--bare", str(bare), cwd=tmp_path)
    (bare / "hooks" / "pre-receive").write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    with pytest.raises(GraphError, match="already"):
        install_server(bare)
    assert "echo mine" in (bare / "hooks" / "pre-receive").read_text()


# ---------------------------------------------------------------- the CLI

@pytest.mark.parametrize("where, argv", [
    ("in the bare repo", ["hook", "--server"]),          # --server's `const` default
    ("from outside it",  ["hook", "--server", "REPO"]),  # the path threaded through
])
def test_cli_installs_the_server_hook(tmp_path, monkeypatch, where, argv):
    """Run on the server, where there is no graph for `find_root` to find."""
    bare = tmp_path / "origin.git"
    git("init", "-q", "--bare", str(bare), cwd=tmp_path)
    monkeypatch.chdir(bare if "in the" in where else tmp_path)
    assert main([str(bare) if a == "REPO" else a for a in argv]) == 0
    assert (bare / "hooks" / "pre-receive").exists()


# ---------------------------------------------------------------- what counts as a graph

# ---------------------------------------------------------------- every ref, every push

# ---------------------------------------------------------------- hostile trees

def test_a_symlinked_nodes_directory_cannot_read_the_servers_disk(server, tmp_path):
    """A symlink in a pushed tree resolves on the SERVER."""
    bare, work = server
    private = tmp_path / "server-private"
    private.mkdir()
    (private / "secret-plan.md").write_text(
        "---\nid: secret-plan\ntype: hypothesis\nstatus: alive\n---\n\n# ours\n",
        encoding="utf-8")
    shutil.rmtree(work / "g" / "nodes")
    os.symlink(private, work / "g" / "nodes")
    commit(work, "point nodes at the servers own disk")
    r = push(work)
    assert "secret-plan" not in r.stdout + r.stderr, "the gate read the servers disk"
    assert "server-private" not in r.stdout + r.stderr
    # And the gate still gates: a real graph pushed afterwards is still checked.
    (work / "g" / "nodes").unlink()
    (work / "g" / "nodes").mkdir()
    (work / "g" / "nodes" / "hyp-ok.md").write_text(
        "---\nid: hyp-ok\ntype: hypothesis\nstatus: open\n---\n\n# a claim\n",
        encoding="utf-8")
    commit(work, "a real nodes directory again")
    assert push(work).returncode == 0, "a clean graph stopped being accepted"
    (work / "g" / "nodes" / "hyp-x.md").write_text(ALIVE_NO_GATE, encoding="utf-8")
    commit(work, "and the gate still catches this")
    assert push(work).returncode != 0, "the gate stopped finding graphs"


def test_the_manual_install_puts_the_gate_where_this_machines_git_looks(tmp_path,
                                                                        rules_yaml,
                                                                        monkeypatch):
    home = tmp_path / "operator-home"
    hooks = home / "shared-hooks"
    hooks.mkdir(parents=True)
    (home / ".gitconfig").write_text(f"[core]\n\thooksPath = {hooks}\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    # This repo is NOT served by knoten, so nothing pins the config away and both sides
    # read the same ~/.gitconfig. conftest's git() always merges GIT_ISOLATION, which
    # would pin it, so this one test calls git itself. HOME above is what keeps the
    # developer's real config out.
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    monkeypatch.delenv("GIT_CONFIG_NOSYSTEM", raising=False)
    def plain_git(*args, cwd):
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                              env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    bare = tmp_path / "origin.git"
    plain_git("init", "-q", "--bare", str(bare), cwd=tmp_path)
    install_server(bare)
    work = tmp_path / "work"
    plain_git("clone", "-q", str(bare), str(work), cwd=tmp_path)
    for c in (["config", "user.email", "t@t.t"], ["config", "user.name", "t"]):
        plain_git(*c, cwd=work)
    graph = work / "g"
    (graph / "nodes").mkdir(parents=True)
    (graph / "graph.yaml").write_text(rules_yaml, encoding="utf-8")
    (graph / "nodes" / "hyp-x.md").write_text(ALIVE_NO_GATE, encoding="utf-8")
    plain_git("add", "-A", cwd=work)
    plain_git("commit", "-qm", "broken", cwd=work)
    r = plain_git("push", "origin", "master", cwd=work)
    assert r.returncode != 0, "the gate failed OPEN under the operators core.hooksPath"
    assert "live-claims-must-cite-their-gates" in r.stdout + r.stderr
    assert (hooks / "pre-receive").exists(), "installed somewhere git does not read"
    assert plain_git("log", "--oneline", cwd=bare).stdout == ""


# ---------------------------------------------------------------- hygiene


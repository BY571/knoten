"""`knoten hook` gates the person who ran it. Nobody else.

On a shared graph the normal contributor is one who never installed it, and
`git commit --no-verify` walks past it for the ones who did. `pre-receive` runs on the
repo everyone pushes TO, so it is the only gate that sees everybody's work — and it
refuses the push outright rather than reporting afterwards that master is broken.
"""
import os
import shutil
import subprocess

import pytest

from knoten.cli import main
from knoten.core import GraphError
from knoten.hook import SERVER_MARKER, install_server

ALIVE_NO_GATE = "---\nid: hyp-x\ntype: hypothesis\nstatus: alive\n---\n\n# x\n"


def git(*args, cwd, env=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          env={**os.environ, **(env or {})})


@pytest.fixture
def server(tmp_path, rules_yaml):
    """A bare repo with the gate installed, and a clone to push from.

    Returns (bare, work). The graph lives in `g/`, one folder down, because that is the
    shape that breaks an implementation which assumes the graph is the repo. It carries a
    node, because git does not track an empty directory and a graph in git always has
    one: a fixture without one tests a shape that cannot reach a server.
    """
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
    """A bare repo has no working tree and no graph.yaml to read at install time, so the
    hook searches the tree it was handed. A recorded path would rot the moment the graph
    moved, and rot silently: the hook would find nothing and accept everything."""
    bare, work = server
    shutil.move(work / "g", work / "research" / "graph")
    commit(work, "move the graph two levels down")

    (work / "research" / "graph" / "nodes" / "hyp-x.md").write_text(ALIVE_NO_GATE,
                                                                   encoding="utf-8")
    commit(work, "still has to be caught")

    assert push(work).returncode != 0
    assert history(bare) == ""


def test_it_validates_every_graph_in_the_repo(server):
    """Two graphs in one repo. Checking only the first leaves the second ungated."""
    bare, work = server
    second = work / "biology"
    (second / "nodes").mkdir(parents=True)
    shutil.copy(work / "g" / "graph.yaml", second / "graph.yaml")
    commit(work, "two graphs")
    assert push(work).returncode == 0

    (second / "nodes" / "hyp-x.md").write_text(ALIVE_NO_GATE, encoding="utf-8")
    commit(work, "break the SECOND graph")

    r = push(work)

    assert r.returncode != 0
    assert "live-claims-must-cite-their-gates" in r.stdout + r.stderr


def test_a_repo_with_no_graph_pushes_freely(server):
    """The gate guards graphs. It must not hold a repo hostage for having none."""
    bare, work = server
    shutil.rmtree(work / "g")
    (work / "README.md").write_text("not a graph\n", encoding="utf-8")
    commit(work, "no graph here")

    assert push(work).returncode == 0


def test_deleting_a_branch_is_not_treated_as_a_push(server):
    """A deletion arrives as an all-zeros new sha. `git archive` on it fails, and a gate
    that explodes on a routine branch delete is one people disable."""
    bare, work = server
    commit(work, "a clean graph")
    push(work)
    git("push", "-q", "origin", "master:scratch", cwd=work)

    r = git("push", "origin", "--delete", "scratch", cwd=work)

    assert r.returncode == 0, r.stdout + r.stderr


def test_it_fails_closed_when_knoten_is_not_on_the_server_path(server):
    """A gate that waves the push through when it cannot find its own checker is not a
    gate. It has to refuse and say why."""
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

def test_it_installs_into_the_bare_repos_hook_directory(tmp_path):
    """Wrong directory or a missing exec bit and git skips the hook in silence, which
    turns every behavioural test in this file into a false negative dressed as a pass."""
    bare = tmp_path / "origin.git"
    git("init", "-q", "--bare", str(bare), cwd=tmp_path)

    hook = install_server(bare)

    assert hook == bare / "hooks" / "pre-receive"
    assert os.access(hook, os.X_OK), "an unexecutable hook is one git silently skips"


def test_it_refuses_to_clobber_a_hook_it_did_not_write(tmp_path):
    """Somebody else's pre-receive hook is somebody else's policy. Overwriting it would
    silently disable whatever it was enforcing."""
    bare = tmp_path / "origin.git"
    git("init", "-q", "--bare", str(bare), cwd=tmp_path)
    (bare / "hooks" / "pre-receive").write_text("#!/bin/sh\necho mine\n", encoding="utf-8")

    with pytest.raises(GraphError, match="already"):
        install_server(bare)

    assert "echo mine" in (bare / "hooks" / "pre-receive").read_text()


def test_force_clobbers_a_foreign_hook(tmp_path):
    """The one escape hatch from the refusal above has to actually work."""
    bare = tmp_path / "origin.git"
    git("init", "-q", "--bare", str(bare), cwd=tmp_path)
    (bare / "hooks" / "pre-receive").write_text("#!/bin/sh\necho mine\n", encoding="utf-8")

    install_server(bare, force=True)

    assert SERVER_MARKER in (bare / "hooks" / "pre-receive").read_text(encoding="utf-8")


def test_it_overwrites_its_own_hook(tmp_path):
    """Re-running the installer must not need --force to replace knoten's own hook, or
    every upgrade becomes a two-step people get wrong."""
    bare = tmp_path / "origin.git"
    git("init", "-q", "--bare", str(bare), cwd=tmp_path)

    install_server(bare)
    install_server(bare)                      # idempotent, no error

    assert SERVER_MARKER in (bare / "hooks" / "pre-receive").read_text(encoding="utf-8")


def test_it_refuses_outside_a_git_repo(tmp_path):
    """One line, on a distinct entry point: the user gets a message, not a traceback."""
    with pytest.raises(GraphError, match="git"):
        install_server(tmp_path)


# ---------------------------------------------------------------- the CLI

@pytest.mark.parametrize("where, argv", [
    ("in the bare repo", ["hook", "--server"]),          # --server's `const` default
    ("from outside it",  ["hook", "--server", "REPO"]),  # the path threaded through
])
def test_cli_installs_the_server_hook(tmp_path, monkeypatch, where, argv):
    """Run on the server, where there is no graph for `find_root` to find. Both spellings
    matter: a `store_true` flag would accept the path and silently ignore it."""
    bare = tmp_path / "origin.git"
    git("init", "-q", "--bare", str(bare), cwd=tmp_path)
    monkeypatch.chdir(bare if "in the" in where else tmp_path)

    assert main([str(bare) if a == "REPO" else a for a in argv]) == 0
    assert (bare / "hooks" / "pre-receive").exists()


# ---------------------------------------------------------------- what counts as a graph

def test_an_unrelated_graph_yaml_does_not_hold_the_repo_hostage(server):
    """`graph.yaml` is not a name knoten owns. Another tool's dependency graph, a config
    of the same name, and `knoten validate` fails on it — which, if the hook treated it
    as a graph, would make EVERY push to the repo fail forever, citing a file nobody
    thinks of as a graph. A graph is graph.yaml AND nodes/."""
    bare, work = server
    (work / "vendor").mkdir()
    (work / "vendor" / "graph.yaml").write_text("name: deps\nedges: [a, b]\n", encoding="utf-8")
    commit(work, "an unrelated graph.yaml")

    r = push(work)

    assert r.returncode == 0, r.stdout + r.stderr
    assert "vendor" not in r.stderr


def test_a_malformed_graph_yaml_is_refused(server):
    """The rules file is the one file every other check reads. Broken, nothing downstream
    can be trusted, so it has to be a rejection and not a crash."""
    bare, work = server
    (work / "g" / "graph.yaml").write_text("name: x\nrules: [oops\n", encoding="utf-8")
    commit(work, "break the rules file itself")

    r = push(work)

    assert r.returncode != 0
    assert "invalid YAML" in r.stdout + r.stderr
    assert history(bare) == ""


def test_an_unparseable_node_is_refused(server):
    """`load()` raises rather than skipping, precisely so a node cannot silently vanish.
    That has to survive the trip through the hook as a rejection, not a traceback."""
    bare, work = server
    (work / "g" / "nodes" / "hyp-x.md").write_text("no frontmatter at all\n", encoding="utf-8")
    commit(work, "a node that does not parse")

    r = push(work)

    assert r.returncode != 0
    assert "frontmatter" in r.stdout + r.stderr
    assert "Traceback" not in r.stdout + r.stderr


# ---------------------------------------------------------------- every ref, every push

def test_it_checks_every_ref_in_one_push(server):
    """`git push --all` hands the hook several refs on stdin. Checking only the first
    leaves a broken branch on the server, and it is the branch someone will merge."""
    bare, work = server
    commit(work, "a clean graph")
    push(work)
    git("checkout", "-qb", "side", cwd=work)
    (work / "g" / "nodes" / "hyp-x.md").write_text(ALIVE_NO_GATE, encoding="utf-8")
    commit(work, "broken, on a side branch")

    r = git("push", "--all", "origin", cwd=work)

    assert r.returncode != 0
    assert "live-claims-must-cite-their-gates" in r.stdout + r.stderr
    assert "side" not in git("branch", cwd=bare).stdout


def test_it_gates_a_branch_that_is_not_master(server):
    """Nothing about master is special to git. A gate that only knows master is a gate
    you walk around by naming the branch something else."""
    bare, work = server
    (work / "g" / "nodes" / "hyp-x.md").write_text(ALIVE_NO_GATE, encoding="utf-8")
    commit(work, "broken")

    r = git("push", "origin", "master:experiments", cwd=work)

    assert r.returncode != 0


def test_it_gates_a_force_push(server):
    """Rewriting history is the other way to get a bad tree onto the server."""
    bare, work = server
    commit(work, "a clean graph")
    push(work)
    (work / "g" / "nodes" / "hyp-x.md").write_text(ALIVE_NO_GATE, encoding="utf-8")
    git("add", "-A", cwd=work)
    git("commit", "-q", "--amend", "-m", "rewritten and broken", cwd=work)

    r = git("push", "-f", "origin", "master", cwd=work)

    assert r.returncode != 0
    assert "rewritten" not in history(bare)


def test_it_gates_a_tag(server):
    """A tag carries a tree like any other ref. Skipping tags leaves a published, broken
    snapshot on the server."""
    bare, work = server
    (work / "g" / "nodes" / "hyp-x.md").write_text(ALIVE_NO_GATE, encoding="utf-8")
    commit(work, "broken")
    git("tag", "v1", cwd=work)

    assert git("push", "origin", "v1", cwd=work).returncode != 0


# ---------------------------------------------------------------- hostile trees

def test_a_symlinked_nodes_directory_cannot_read_the_servers_disk(server, tmp_path):
    """A symlink in a pushed tree resolves on the SERVER. A `write` user pushed
    `g/nodes -> /some/server/dir`; the gate followed it, validated that directory and
    echoed its file names back on the `remote:` lines, which made the gate a directory
    listing for anyone who could push."""
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


def test_one_broken_ref_refuses_the_whole_push(server):
    """pre-receive runs once for all refs and its exit status is the verdict on all of
    them. A push carrying a clean branch and a broken one must land neither, or the
    pusher gets a partial push and the shared repo a branch nobody validated."""
    bare, work = server
    commit(work, "a clean graph")
    git("branch", "other", cwd=work)
    (work / "g" / "nodes" / "hyp-x.md").write_text(ALIVE_NO_GATE, encoding="utf-8")
    commit(work, "broken, on master")

    r = git("push", "origin", "master", "other", cwd=work)

    assert r.returncode != 0
    assert "live-claims-must-cite-their-gates" in r.stdout + r.stderr
    assert git("branch", cwd=bare).stdout.strip() == "", "a ref landed anyway"


# ---------------------------------------------------------------- hygiene

def test_it_leaves_no_temporary_directories_behind(server, tmp_path):
    """It unpacks a whole tree on every push. A gate that leaks one copy per push fills
    the server's disk, and the first symptom is pushes failing for an unrelated reason."""
    bare, work = server
    scratch = tmp_path / "tmpdir"
    scratch.mkdir()
    commit(work, "a clean graph")
    assert push(work, env={"TMPDIR": str(scratch)}).returncode == 0

    (work / "g" / "nodes" / "hyp-x.md").write_text(ALIVE_NO_GATE, encoding="utf-8")
    commit(work, "and a rejected one")
    push(work, env={"TMPDIR": str(scratch)})

    assert list(scratch.iterdir()) == [], "the hook leaked a tree it unpacked"


def test_it_works_when_the_repo_path_contains_a_space(tmp_path, rules_yaml):
    """Unquoted shell is the classic way this breaks, and it breaks OPEN: the validate
    step fails to find the graph and the push sails through."""
    bare = tmp_path / "my research.git"
    git("init", "-q", "--bare", str(bare), cwd=tmp_path)
    install_server(bare)
    work = tmp_path / "a work dir"
    git("clone", "-q", str(bare), str(work), cwd=tmp_path)
    for c in (["config", "user.email", "t@t.t"], ["config", "user.name", "t"]):
        git(*c, cwd=work)
    graph = work / "the graph"
    (graph / "nodes").mkdir(parents=True)
    (graph / "graph.yaml").write_text(rules_yaml, encoding="utf-8")
    (graph / "nodes" / "hyp-x.md").write_text(ALIVE_NO_GATE, encoding="utf-8")
    commit(work, "broken, under a path with spaces")

    r = push(work)

    assert r.returncode != 0, "the gate failed OPEN on a path with a space"
    assert "live-claims-must-cite-their-gates" in r.stdout + r.stderr

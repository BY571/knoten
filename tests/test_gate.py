"""`knoten gate` is what the pre-receive hook execs: everything the shell script used to
do, in Python, because signature checks and the constitution rule are not shell. These
tests drive it through real pushes; the shell-era tests in test_server_hook.py stay as
they are and must keep passing."""
import io
import os
import subprocess

import pytest

from conftest import commit_node, git
from knoten import gate
from knoten.hook import install_server


@pytest.fixture
def bare(tmp_path, rules_yaml):
    """A gated bare repo, a clone pushing to it, the graph one folder down at g/."""
    origin = tmp_path / "origin.git"
    git("init", "-q", "--bare", str(origin), cwd=tmp_path)
    install_server(origin)
    work = tmp_path / "work"
    git("clone", "-q", str(origin), str(work), cwd=tmp_path)
    for c in (["config", "user.email", "t@t.t"], ["config", "user.name", "t"]):
        git(*c, cwd=work)
    g = work / "g"
    (g / "nodes").mkdir(parents=True)
    (g / "graph.yaml").write_text(rules_yaml, encoding="utf-8")
    (g / "nodes" / "hyp-ok.md").write_text(
        "---\nid: hyp-ok\ntype: hypothesis\nstatus: open\n---\n\n# ok\n", encoding="utf-8")
    return origin, work


def test_the_hook_is_now_one_exec(bare):
    """The shell script carries the fail-closed PATH check and nothing else; the logic
    lives where it can be tested as Python."""
    origin, _ = bare
    text = (origin / "hooks" / "pre-receive").read_text(encoding="utf-8")
    assert "exec knoten gate" in text
    assert "git archive" not in text


def test_graph_dirs_finds_a_graph_by_its_yaml_and_a_real_nodes_tree(bare, monkeypatch):
    origin, work = bare
    (work / "vendor").mkdir()
    (work / "vendor" / "graph.yaml").write_text("name: deps\n", encoding="utf-8")   # no nodes/
    git("add", "-A", cwd=work)
    git("commit", "-qm", "seed", cwd=work)
    sha = git("rev-parse", "HEAD", cwd=work).stdout.strip()
    monkeypatch.chdir(work)

    assert gate.graph_dirs(sha) == ["g"]


def test_graph_dirs_ignores_a_symlinked_nodes(bare, monkeypatch):
    """A symlink is mode 120000 in the tree, never a tree object, so it cannot make a
    directory a graph. This is the property the old hook enforced by deleting links."""
    origin, work = bare
    (work / "elsewhere").mkdir()
    (work / "elsewhere" / "secret-plan.md").write_text("x", encoding="utf-8")
    import shutil
    shutil.rmtree(work / "g" / "nodes")
    os.symlink("../elsewhere", work / "g" / "nodes")
    git("add", "-A", cwd=work)
    git("commit", "-qm", "linked", cwd=work)
    sha = git("rev-parse", "HEAD", cwd=work).stdout.strip()
    monkeypatch.chdir(work)

    assert gate.graph_dirs(sha) == []


def test_extract_writes_regular_files_only(bare, monkeypatch, tmp_path):
    origin, work = bare
    os.symlink("/etc/hostname", work / "g" / "nodes" / "link.md")
    git("add", "-A", cwd=work)
    git("commit", "-qm", "seed", cwd=work)
    sha = git("rev-parse", "HEAD", cwd=work).stdout.strip()
    monkeypatch.chdir(work)

    root = gate.extract(sha, "g", tmp_path / "out")

    assert (root / "nodes" / "hyp-ok.md").exists()
    assert not (root / "nodes" / "link.md").exists()


def test_main_reads_refs_from_stdin_and_refuses_a_broken_graph(bare, monkeypatch, capsys):
    origin, work = bare
    commit_node(work / "g", "hyp-x.md", "---\nid: hyp-x\ntype: hypothesis\nstatus: alive\n---\n\n# x\n")
    sha = git("rev-parse", "HEAD", cwd=work).stdout.strip()
    monkeypatch.chdir(work)

    rc = gate.main(io.StringIO(f"{'0' * 40} {sha} refs/heads/master\n"))

    err = capsys.readouterr().err
    assert rc == 1
    assert "live-claims-must-cite-their-gates" in err and "REFUSED" in err
    assert "Traceback" not in err


def test_a_deletion_line_is_skipped(bare, monkeypatch, capsys):
    origin, work = bare
    monkeypatch.chdir(work)
    assert gate.main(io.StringIO(f"{'a' * 40} {'0' * 40} refs/heads/x\n")) == 0
    assert capsys.readouterr().err == ""

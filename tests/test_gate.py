"""`knoten gate` is what the pre-receive hook execs: everything the shell script used to
do, in Python, because signature checks and the constitution rule are not shell. These
tests drive it through real pushes; the shell-era tests in test_server_hook.py stay as
they are and must keep passing."""
import io
import os

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


def test_extract_and_graph_dirs_refuse_a_directory_name_git_would_parse_as_an_option(
        bare, rules_yaml, monkeypatch, capsys):
    """`--output=PWNED.tar` reaching `git archive` with no `--` separator is not a path,
    it's an option -- verified live, it made git write a tarball into the bare repo, and
    `--remote=host:path` made git shell out to ssh. `graph_dirs` refuses any directory
    name that could be parsed as an option or pathspec magic before it ever reaches git."""
    origin, work = bare
    d = work / "--output=PWNED.tar"
    (d / "nodes").mkdir(parents=True)
    (d / "graph.yaml").write_text(rules_yaml, encoding="utf-8")
    (d / "nodes" / "hyp-ok2.md").write_text(
        "---\nid: hyp-ok2\ntype: hypothesis\nstatus: open\n---\n\n# ok\n", encoding="utf-8")
    git("add", "-A", cwd=work)
    git("commit", "-qm", "evil dirname", cwd=work)
    sha = git("rev-parse", "HEAD", cwd=work).stdout.strip()
    monkeypatch.chdir(work)

    rc = gate.main(io.StringIO(f"{'0' * 40} {sha} refs/heads/master\n"))
    err = capsys.readouterr().err

    assert rc == 1
    assert "--output=PWNED.tar" in err
    assert "Traceback" not in err
    assert not list(origin.rglob("PWNED.tar"))
    assert not list(work.rglob("PWNED.tar"))


def test_main_fails_closed_on_a_non_graph_error_bug_instead_of_leaking_a_traceback(
        bare, monkeypatch, capsys):
    """A bug anywhere under `check_ref` must still refuse the push in one line, not leak a
    server-side traceback (module names, paths) to whoever is pushing on band 2."""
    origin, work = bare
    git("add", "-A", cwd=work)
    git("commit", "-qm", "seed", cwd=work)
    sha = git("rev-parse", "HEAD", cwd=work).stdout.strip()
    monkeypatch.chdir(work)

    def _boom(root):
        raise RuntimeError("boom")

    monkeypatch.setattr(gate.ops, "validate", _boom)

    rc = gate.main(io.StringIO(f"{'0' * 40} {sha} refs/heads/master\n"))
    err = capsys.readouterr().err

    assert rc == 1
    assert "cannot check" in err
    assert "Traceback" not in err


def test_git_gives_children_no_access_to_the_hooks_stdin(bare, monkeypatch):
    """`_git` used to inherit the hook's stdin -- which IS git's ref list -- into every
    child it spawned. `git verify-commit`/gpg (Task 4) reads from stdin by default; a
    child reading the ref list instead of getting EOF would starve `main`'s own read
    loop. `git hash-object --stdin` with nothing piped in returns the empty blob's hash
    only if it saw EOF, not the parent's stdin."""
    origin, work = bare
    monkeypatch.chdir(work)

    r = gate._git("hash-object", "--stdin")

    assert r.stdout.strip() == b"e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"

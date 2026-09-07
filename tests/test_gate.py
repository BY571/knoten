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


def seeded(work):
    """The clean graph committed, and its sha: the `old` half of every ref line below.
    A hosted graph's branch is created once and moves forward after that, so a check
    driven from an all-zero `old` is testing the one push that can never happen twice."""
    git("add", "-A", cwd=work)
    git("commit", "-qm", "seed", cwd=work)
    return git("rev-parse", "HEAD", cwd=work).stdout.strip()


def test_main_reads_refs_from_stdin_and_refuses_a_broken_graph(bare, monkeypatch, capsys):
    origin, work = bare
    old = seeded(work)
    commit_node(work / "g", "hyp-x.md", "---\nid: hyp-x\ntype: hypothesis\nstatus: alive\n---\n\n# x\n")
    sha = git("rev-parse", "HEAD", cwd=work).stdout.strip()
    monkeypatch.chdir(work)

    rc = gate.main(io.StringIO(f"{old} {sha} refs/heads/master\n"))

    err = capsys.readouterr().err
    assert rc == 1
    assert "live-claims-must-cite-their-gates" in err and "REFUSED" in err
    assert "Traceback" not in err


def test_a_deletion_is_refused_and_says_why(bare, monkeypatch, capsys):
    """A deletion arrives as an all-zero new sha. It used to be waved through as "no tree
    to check", which left `receive.denyDeletes` as the only thing standing between a
    shared graph and `git push --delete master` -- one config edit away, and absent
    entirely from a repo somebody gated with `knoten hook --server`."""
    origin, work = bare
    monkeypatch.chdir(work)

    rc = gate.main(io.StringIO(f"{'a' * 40} {'0' * 40} refs/heads/x\n"))

    err = capsys.readouterr().err
    assert rc == 1
    assert "refs are not deleted" in err


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
    old = seeded(work)
    git("add", "-A", cwd=work)
    git("commit", "-qm", "evil dirname", cwd=work)
    sha = git("rev-parse", "HEAD", cwd=work).stdout.strip()
    monkeypatch.chdir(work)

    rc = gate.main(io.StringIO(f"{old} {sha} refs/heads/master\n"))
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
    old = seeded(work)
    commit_node(work / "g", "hyp-y.md", "---\nid: hyp-y\ntype: hypothesis\nstatus: open\n---\n\n# y\n")
    sha = git("rev-parse", "HEAD", cwd=work).stdout.strip()
    monkeypatch.chdir(work)

    def _boom(root):
        raise RuntimeError("boom")

    monkeypatch.setattr(gate.ops, "validate", _boom)

    rc = gate.main(io.StringIO(f"{old} {sha} refs/heads/master\n"))
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


# ---------------------------------------------------------------- signatures

from conftest import commit_signed, make_key, pub_line
from knoten import identity as C


@pytest.fixture
def signed(bare, keys_dir):
    """`bare` bootstrapped: contributors.yaml lists seb as admin, first commit signed by
    seb, pushed. Returns (origin, work, {"seb": priv}). Later tests add people."""
    origin, work = bare
    seb = make_key(keys_dir, "seb")
    C.dump(work / "g", {"seb": {"key": pub_line(seb), "role": "admin"}})
    commit_signed(work, "seb creates the graph", seb)
    r = git("push", "-q", "origin", "master", cwd=work)
    assert r.returncode == 0, r.stderr
    return origin, work, {"seb": seb}


def push(work):
    return git("push", "origin", "master", cwd=work)


def add_person(work, name, priv, role, **extra):
    c = C.load(work / "g") or {}
    c[name] = {"key": pub_line(priv), "role": role, **extra}
    C.dump(work / "g", c)


def test_a_graph_without_contributors_stays_unsigned(bare):
    """Phase-1 graphs keep working: no contributors.yaml, no signature check."""
    origin, work = bare
    git("add", "-A", cwd=work)
    git("commit", "-qm", "unsigned, and fine", cwd=work)
    assert push(work).returncode == 0


def test_bootstrap_must_be_signed_by_an_admin_the_file_lists(bare, keys_dir):
    """The first contributors.yaml has nobody to vouch for it but itself: the commit that
    introduces it must be signed by a key it names as admin. Anyone else could otherwise
    install themselves as admin of a phase-1 graph."""
    origin, work = bare
    seb, eve = make_key(keys_dir, "seb"), make_key(keys_dir, "eve")
    C.dump(work / "g", {"seb": {"key": pub_line(seb), "role": "admin"}})
    commit_signed(work, "eve pretends", eve)

    r = push(work)

    assert r.returncode != 0
    assert "not signed by an admin it lists" in r.stderr
    git("reset", "-q", "--hard", "HEAD~1", cwd=work)


def test_an_unsigned_commit_is_refused_once_the_graph_is_signed(signed):
    origin, work, k = signed
    commit_node(work / "g", "hyp-a.md", "---\nid: hyp-a\ntype: hypothesis\nstatus: open\n---\n\n# a\n")

    r = push(work)

    assert r.returncode != 0
    assert "unsigned" in r.stderr
    assert "hyp-a" not in git("log", "--oneline", cwd=origin).stdout


def test_a_commit_signed_by_a_listed_writer_lands(signed, keys_dir):
    origin, work, k = signed
    maria = make_key(keys_dir, "maria")
    add_person(work, "maria", maria, "write")
    commit_signed(work, "seb adds maria", k["seb"])
    (work / "g" / "nodes" / "hyp-m.md").write_text(
        "---\nid: hyp-m\ntype: hypothesis\nstatus: open\n---\n\n# m\n", encoding="utf-8")
    commit_signed(work, "maria's claim", maria)

    r = push(work)

    assert r.returncode == 0, r.stderr


def test_a_key_the_graph_does_not_list_is_refused(signed, keys_dir):
    origin, work, k = signed
    eve = make_key(keys_dir, "eve")
    (work / "g" / "nodes" / "hyp-e.md").write_text(
        "---\nid: hyp-e\ntype: hypothesis\nstatus: open\n---\n\n# e\n", encoding="utf-8")
    commit_signed(work, "eve's claim", eve)

    r = push(work)

    assert r.returncode != 0
    assert "not listed" in r.stderr


def test_a_reader_may_not_sign_a_commit(signed, keys_dir):
    origin, work, k = signed
    reader = make_key(keys_dir, "reader")
    add_person(work, "reader", reader, "read")
    commit_signed(work, "seb adds a reader", k["seb"])
    assert push(work).returncode == 0
    (work / "g" / "nodes" / "hyp-r.md").write_text(
        "---\nid: hyp-r\ntype: hypothesis\nstatus: open\n---\n\n# r\n", encoding="utf-8")
    commit_signed(work, "a reader writes", reader)

    r = push(work)

    assert r.returncode != 0
    assert "not listed" in r.stderr


def test_a_revoked_key_is_refused_but_its_history_stays(signed, keys_dir):
    """Revocation is a mark. Everything maria signed before stays in history and stays
    attributable; the next thing she signs does not get in."""
    origin, work, k = signed
    maria = make_key(keys_dir, "maria")
    add_person(work, "maria", maria, "write")
    commit_signed(work, "seb adds maria", k["seb"])
    (work / "g" / "nodes" / "hyp-m.md").write_text(
        "---\nid: hyp-m\ntype: hypothesis\nstatus: open\n---\n\n# m\n", encoding="utf-8")
    commit_signed(work, "maria's claim", maria)
    assert push(work).returncode == 0
    c = C.load(work / "g")
    c["maria"]["revoked"] = "2026-09-05"
    C.dump(work / "g", c)
    commit_signed(work, "seb revokes maria", k["seb"])
    assert push(work).returncode == 0
    (work / "g" / "nodes" / "hyp-m2.md").write_text(
        "---\nid: hyp-m2\ntype: hypothesis\nstatus: open\n---\n\n# m2\n", encoding="utf-8")
    commit_signed(work, "maria after revocation", maria)

    r = push(work)

    assert r.returncode != 0
    assert "hyp-m.md" in git("ls-tree", "-r", "--name-only", "master", cwd=origin).stdout
    assert "hyp-m2" not in git("ls-tree", "-r", "--name-only", "master", cwd=origin).stdout


def test_a_merge_commit_is_refused(signed, keys_dir):
    """Which contributors were in force "before" a merge is not a single answer. knoten's
    own pull is --ff-only; the gate says so rather than guessing."""
    origin, work, k = signed
    git("checkout", "-qb", "side", cwd=work)
    (work / "g" / "nodes" / "hyp-s.md").write_text(
        "---\nid: hyp-s\ntype: hypothesis\nstatus: open\n---\n\n# s\n", encoding="utf-8")
    commit_signed(work, "side", k["seb"])
    git("checkout", "-q", "master", cwd=work)
    (work / "g" / "nodes" / "hyp-t.md").write_text(
        "---\nid: hyp-t\ntype: hypothesis\nstatus: open\n---\n\n# t\n", encoding="utf-8")
    commit_signed(work, "trunk", k["seb"])
    r = git("-c", "gpg.format=ssh", "-c", f"user.signingkey={k['seb']}", "-c", "commit.gpgsign=true",
            "merge", "--no-ff", "-q", "-m", "merge", "side", cwd=work)
    assert r.returncode == 0, r.stderr

    r = push(work)

    assert r.returncode != 0
    assert "merge commits are not accepted" in r.stderr


def test_every_commit_in_a_push_is_checked_not_just_the_tip(signed, keys_dir):
    """An unsigned commit under a signed tip must not ride in on the tip's signature."""
    origin, work, k = signed
    commit_node(work / "g", "hyp-u.md", "---\nid: hyp-u\ntype: hypothesis\nstatus: open\n---\n\n# u\n")
    (work / "g" / "nodes" / "hyp-v.md").write_text(
        "---\nid: hyp-v\ntype: hypothesis\nstatus: open\n---\n\n# v\n", encoding="utf-8")
    commit_signed(work, "signed tip", k["seb"])

    r = push(work)

    assert r.returncode != 0
    assert "unsigned" in r.stderr


def test_a_malformed_ref_line_is_refused_without_a_traceback(bare, monkeypatch, capsys):
    """`new` reaches `check_ref` and from there `git rev-list`/`git archive` as a revision.
    A value like `--output=x` would be parsed by git as an option, not a sha; requiring
    old/new to look like hex object ids before ever calling `check_ref` closes that off."""
    origin, work = bare
    monkeypatch.chdir(work)

    rc = gate.main(io.StringIO(f"{'0' * 40} --output=x refs/heads/master\n"))

    err = capsys.readouterr().err
    assert rc == 1
    assert "malformed ref line for refs/heads/master" in err
    assert "Traceback" not in err


# ------------------------------------------------------- moved and deleted graph dirs

def test_a_renamed_graph_is_still_checked_against_its_old_contributors(signed, keys_dir):
    """Eve, unlisted at g, renamed g to h and wrote a fresh contributors.yaml naming
    herself sole admin. `contributors_at(parent, "h")` is None -- h did not exist a
    moment ago -- so checking graph_dirs(sha) alone let the bootstrap branch (nobody to
    vouch for the first contributors.yaml but itself) accept her, since she signed with
    her own key and her own new file names her admin. Checking graph_dirs at the parent
    too means g's disappearance is judged against g's own contributors: seb, not eve."""
    origin, work, k = signed
    eve = make_key(keys_dir, "eve")
    git("mv", "g", "h", cwd=work)
    C.dump(work / "h", {"eve": {"key": pub_line(eve), "role": "admin"}})
    commit_signed(work, "eve takes over", eve)

    r = push(work)

    assert r.returncode != 0
    tree = git("ls-tree", "-r", "--name-only", "master", cwd=origin).stdout
    assert "g/contributors.yaml" in tree


def test_an_unsigned_removal_of_a_graph_is_refused(signed):
    """An unsigned commit that deletes g outright must not land just because the per-
    commit loop, walking only graph_dirs(sha), finds no graph directory left to check."""
    origin, work, k = signed
    git("rm", "-rq", "g", cwd=work)
    git("commit", "-qm", "remove g", cwd=work)

    r = push(work)

    assert r.returncode != 0
    assert "revoked, never removed" in r.stderr
    assert "g/contributors.yaml" in git("ls-tree", "-r", "--name-only", "master", cwd=origin).stdout


def test_not_even_an_admin_may_move_the_graph_out_from_under_its_constitution(signed):
    """A rename takes contributors.yaml away from the directory its entries were written
    for, and the gate cannot tell that from a deletion: both are "g has no constitution
    now". It used to be allowed for an admin, which is also how an admin could drop a
    name in two commits -- move the graph, then found the new one without them. A hosted
    graph stays where it is; the cost is that `git mv` on a graph directory is refused."""
    origin, work, k = signed
    git("mv", "g", "h", cwd=work)
    commit_signed(work, "seb renames g to h", k["seb"])

    r = push(work)

    assert r.returncode != 0
    assert "revoked, never removed" in r.stderr
    assert "g/contributors.yaml" in git("ls-tree", "-r", "--name-only", "master",
                                        cwd=origin).stdout


# --------------------------------------------------------------- rev-list return codes

def test_check_ref_raises_instead_of_treating_a_rev_list_failure_as_nothing_to_check(
        bare, monkeypatch):
    """rev-list's own exit code was ignored at both call sites: a failure returns empty
    stdout, indistinguishable from "no merges" and "no commits", which let a push through
    on a git error instead of refusing it. Driven from the hosted repo before its first
    branch, which is the one state where a brand-new ref still gets as far as rev-list;
    a `new` that is not a real object makes it fail immediately."""
    origin, work = bare
    monkeypatch.chdir(origin)

    with pytest.raises(gate.GraphError, match="cannot list commits"):
        gate.check_ref("0" * 40, "a" * 40, "refs/heads/master")


# ------------------------------------------------------------------- misc review fixes

def test_signature_unlinks_its_temp_file_even_when_allowed_signers_raises(monkeypatch):
    """The temp allowed-signers file used to be written outside the try/finally that
    unlinks it: a raise from allowed_signers (malformed key data) before the git call
    would leak the file instead of cleaning it up."""
    import tempfile
    from pathlib import Path

    def _boom(keys, namespaces):
        raise RuntimeError("boom")

    monkeypatch.setattr(gate, "allowed_signers", _boom)
    before = set(Path(tempfile.gettempdir()).glob("*.signers"))

    with pytest.raises(RuntimeError):
        gate.signature("deadbeef", {"seb": "ssh-ed25519 AAAA"})

    after = set(Path(tempfile.gettempdir()).glob("*.signers"))
    assert after == before


# ------------------------------------------------------------ one line of history

def test_the_first_push_creates_the_only_branch(bare):
    """The repo has no branch yet, so this ref is the graph's line of history. Nothing
    about that push changes."""
    origin, work = bare
    seeded(work)

    assert git("push", "-q", "origin", "master", cwd=work).returncode == 0
    assert "master" in git("branch", cwd=origin).stdout


def test_a_second_branch_is_refused_even_when_its_tree_is_clean(bare):
    """A hosted graph has ONE line of history. A clean side branch is still a tree the
    next `knoten pull` never looks at, and a place to hide a second contributors.yaml, so
    the ref itself is refused rather than its contents judged."""
    origin, work = bare
    seeded(work)
    assert git("push", "-q", "origin", "master", cwd=work).returncode == 0
    git("checkout", "-qb", "feature", cwd=work)
    commit_node(work / "g", "hyp-f.md",
                "---\nid: hyp-f\ntype: hypothesis\nstatus: open\n---\n\n# f\n")

    r = git("push", "origin", "feature", cwd=work)

    assert r.returncode != 0
    assert "one branch" in r.stderr
    assert "feature" not in git("branch", cwd=origin).stdout


def test_a_rewrite_of_accepted_history_is_refused_as_a_non_fast_forward(bare):
    """A force push drops commits the gate already accepted and everyone else already
    pulled. `receive.denyNonFastForwards` says so in the hosted repo's config; the gate
    says it everywhere the gate runs."""
    origin, work = bare
    seeded(work)
    assert git("push", "-q", "origin", "master", cwd=work).returncode == 0
    before = git("rev-parse", "master", cwd=origin).stdout.strip()
    git("commit", "-q", "--amend", "-m", "rewritten", cwd=work)

    r = git("push", "-f", "origin", "master", cwd=work)

    assert r.returncode != 0
    assert "not a fast-forward" in r.stderr
    assert git("rev-parse", "master", cwd=origin).stdout.strip() == before


# ---------------------------------------------------------------- the constitution

from knoten.identity import INVITE_NS, sign


def invite_for(admin_priv, graph, name, role):
    blob = C.invite_blob(graph, name, role, "2099-01-01", "n0nce")
    return {"blob": blob.decode(), "sig": sign(admin_priv, blob, INVITE_NS)}


def test_a_writer_cannot_change_who_may_write(signed, keys_dir):
    """Maria may push nodes. She may not add her friend."""
    origin, work, k = signed
    maria, friend = make_key(keys_dir, "maria"), make_key(keys_dir, "friend")
    add_person(work, "maria", maria, "write")
    commit_signed(work, "seb adds maria", k["seb"])
    assert push(work).returncode == 0
    add_person(work, "friend", friend, "write")
    commit_signed(work, "maria adds a friend", maria)

    r = push(work)

    assert r.returncode != 0
    assert "not signed by an admin" in r.stderr


def test_a_join_with_a_valid_invite_lands_signed_by_the_newcomer(signed, keys_dir):
    """The one way in without an admin's commit: one new entry, an admin-signed invite
    for exactly that name and role, and a commit signed by the newcomer's own key."""
    origin, work, k = signed
    maria = make_key(keys_dir, "maria")
    add_person(work, "maria", maria, "write", invited_by="seb",
               invite=invite_for(k["seb"], "test", "maria", "write"))
    commit_signed(work, "maria joins as write", maria)

    r = push(work)

    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("graph, name, role", [
    ("other", "maria", "write"), ("test", "eve", "write"), ("test", "maria", "admin")])
def test_an_invite_for_something_else_is_refused(signed, keys_dir, graph, name, role):
    """graph.yaml itself is untouched here, so this graph's name is `test` whether it is
    read at sha or at the parent -- the `other` case below is what actually exercises
    reading it at the parent."""
    origin, work, k = signed
    maria = make_key(keys_dir, "maria")
    add_person(work, "maria", maria, "write", invited_by="seb",
               invite=invite_for(k["seb"], graph, name, role))
    commit_signed(work, "maria joins", maria)

    r = push(work)

    assert r.returncode != 0
    assert "different name, role or graph" in r.stderr


def test_a_join_may_not_touch_anything_but_contributors_yaml(signed, keys_dir):
    """A `read` invite must not buy its holder one unrestricted write to the graph: the
    join branch used to approve the whole commit on the invite plus the newcomer's
    signature alone, checking nothing about what else the commit touched."""
    origin, work, k = signed
    maria = make_key(keys_dir, "maria")
    add_person(work, "maria", maria, "read", invited_by="seb",
               invite=invite_for(k["seb"], "test", "maria", "read"))
    (work / "g" / "nodes" / "hyp-ok.md").write_text(
        "---\nid: hyp-ok\ntype: hypothesis\nstatus: alive\n---\n\n# rewritten by the joiner\n",
        encoding="utf-8")
    commit_signed(work, "maria joins and rewrites a node", maria)

    r = push(work)

    assert r.returncode != 0
    assert "a join may change nothing but" in r.stderr
    tree = git("show", "master:g/nodes/hyp-ok.md", cwd=origin).stdout
    assert "rewritten by the joiner" not in tree


def test_an_invite_is_checked_against_the_graph_name_before_this_commit(signed, keys_dir):
    """The invite's `graph` field would bind nothing if the joiner could pick the name in
    the same commit: an invite signed for a graph named `other`, alongside a same-commit
    rename of graph.yaml's `name:` to `other`, must still be refused -- the name is read
    at the PARENT, before this commit's own rewrite. (With the previous fix in place this
    is refused for touching graph.yaml at all, not reaching the name check; either way it
    must not land.)"""
    origin, work, k = signed
    maria = make_key(keys_dir, "maria")
    add_person(work, "maria", maria, "write", invited_by="seb",
               invite=invite_for(k["seb"], "other", "maria", "write"))
    (work / "g" / "graph.yaml").write_text(
        (work / "g" / "graph.yaml").read_text(encoding="utf-8").replace("name: test", "name: other"),
        encoding="utf-8")
    commit_signed(work, "maria joins and renames the graph to match her invite", maria)

    r = push(work)

    assert r.returncode != 0
    assert "issued for a different name, role or graph" in r.stderr
    assert "name: test" in git("show", "master:g/graph.yaml", cwd=origin).stdout


def test_an_invite_for_the_graphs_old_name_is_refused_after_it_is_renamed(signed, keys_dir):
    """The sibling of the test above, with the rename in a PREVIOUS pushed commit so the
    join itself touches nothing but contributors.yaml. Without it, "refused" could mean
    only "you touched graph.yaml" and the name check would never be the thing that fired."""
    origin, work, k = signed
    (work / "g" / "graph.yaml").write_text(
        (work / "g" / "graph.yaml").read_text(encoding="utf-8").replace("name: test", "name: other"),
        encoding="utf-8")
    commit_signed(work, "seb renames the graph", k["seb"])
    assert push(work).returncode == 0

    maria = make_key(keys_dir, "maria")
    add_person(work, "maria", maria, "write", invited_by="seb",
               invite=invite_for(k["seb"], "test", "maria", "write"))
    commit_signed(work, "maria joins with an invite for the old name", maria)

    r = push(work)

    assert r.returncode != 0
    assert "issued for a different name, role or graph" in r.stderr
    assert "maria" not in git("show", "master:g/contributors.yaml", cwd=origin).stdout


def test_a_join_naming_a_different_inviter_is_refused(signed, keys_dir):
    origin, work, k = signed
    maria = make_key(keys_dir, "maria")
    add_person(work, "maria", maria, "write", invited_by="eve",
               invite=invite_for(k["seb"], "test", "maria", "write"))
    commit_signed(work, "maria joins claiming eve invited her", maria)

    r = push(work)

    assert r.returncode != 0
    assert "different inviter" in r.stderr


def test_an_invite_signed_by_a_writer_is_refused(signed, keys_dir):
    origin, work, k = signed
    maria, friend = make_key(keys_dir, "maria"), make_key(keys_dir, "friend")
    add_person(work, "maria", maria, "write")
    commit_signed(work, "seb adds maria", k["seb"])
    assert push(work).returncode == 0
    add_person(work, "friend", friend, "write", invited_by="maria",
               invite=invite_for(maria, "test", "friend", "write"))
    commit_signed(work, "friend joins", friend)

    r = push(work)

    assert r.returncode != 0
    assert "not signed by an admin" in r.stderr


def test_a_join_must_be_signed_by_the_newcomers_own_key(signed, keys_dir):
    """A valid invite for maria, committed by eve with eve's key: the entry says maria's
    key, the commit does not. The person arriving must be the person invited."""
    origin, work, k = signed
    maria, eve = make_key(keys_dir, "maria"), make_key(keys_dir, "eve")
    add_person(work, "maria", maria, "write", invited_by="seb",
               invite=invite_for(k["seb"], "test", "maria", "write"))
    commit_signed(work, "eve commits maria's join", eve)

    r = push(work)

    assert r.returncode != 0
    assert "not signed by maria" in r.stderr


def test_a_join_may_add_only_itself(signed, keys_dir):
    origin, work, k = signed
    maria, friend = make_key(keys_dir, "maria"), make_key(keys_dir, "friend")
    add_person(work, "maria", maria, "write", invited_by="seb",
               invite=invite_for(k["seb"], "test", "maria", "write"))
    add_person(work, "friend", friend, "write")
    commit_signed(work, "maria joins with a plus one", maria)

    r = push(work)

    assert r.returncode != 0
    assert "not signed by an admin" in r.stderr


def test_deleting_contributors_yaml_is_refused_whoever_signs(signed, keys_dir):
    """It used to need an admin, which left the two-commit version of dropping a name
    open: delete the file, then bootstrap a fresh one leaving somebody out, which the
    bootstrap rules accept because there is nothing left to weigh it against. A graph's
    constitution does not stop existing."""
    origin, work, k = signed
    maria = make_key(keys_dir, "maria")
    add_person(work, "maria", maria, "write")
    commit_signed(work, "seb adds maria", k["seb"])
    assert push(work).returncode == 0

    (work / "g" / C.FILE).unlink()
    commit_signed(work, "maria removes the constitution", maria)
    r = push(work)
    assert r.returncode != 0
    assert "revoked, never removed" in r.stderr

    git("reset", "-q", "--hard", "HEAD~1", cwd=work)
    (work / "g" / C.FILE).unlink()
    commit_signed(work, "seb removes the constitution", k["seb"])

    r = push(work)

    assert r.returncode != 0, "an admin deleted it"
    assert "revoked, never removed" in r.stderr
    assert "maria" in git("show", "master:g/contributors.yaml", cwd=origin).stdout


def test_an_admin_may_promote_demote_and_revoke(signed, keys_dir):
    origin, work, k = signed
    maria = make_key(keys_dir, "maria")
    add_person(work, "maria", maria, "read")
    commit_signed(work, "seb adds maria as read", k["seb"])
    c = C.load(work / "g"); c["maria"]["role"] = "admin"; C.dump(work / "g", c)
    commit_signed(work, "seb promotes maria", k["seb"])
    c = C.load(work / "g"); c["maria"]["revoked"] = "2026-09-05"; C.dump(work / "g", c)
    commit_signed(work, "seb revokes maria", k["seb"])

    assert push(work).returncode == 0


def test_a_writer_may_not_become_admin_by_moving_the_graph(signed, keys_dir):
    """Maria may push nodes at g. Moving g to h and writing a fresh contributors.yaml that
    names only herself admin is the same escalation as editing contributors.yaml in
    place: the removal at g (prev present, cur None) is a contributors change, and that
    needs an admin of prev -- not the newcomer rules for a join, and not the writer rule
    for an unrelated commit."""
    origin, work, k = signed
    maria = make_key(keys_dir, "maria")
    add_person(work, "maria", maria, "write")
    commit_signed(work, "seb adds maria", k["seb"])
    assert push(work).returncode == 0
    git("mv", "g", "h", cwd=work)
    C.dump(work / "h", {"maria": {"key": pub_line(maria), "role": "admin"}})
    commit_signed(work, "maria moves the graph and crowns herself", maria)

    r = push(work)

    assert r.returncode != 0
    # Discriminates the removal-at-g branch from the bootstrap branch a naive per-gdir
    # check could have taken for the new h directory alone.
    assert "revoked, never removed" in r.stderr
    tree = git("ls-tree", "-r", "--name-only", "master", cwd=origin).stdout
    assert "g/contributors.yaml" in tree


# --------------------------------------------------- a graph, and the only graph

def test_removing_the_graph_but_keeping_its_constitution_needs_an_admin(signed, keys_dir):
    """`git rm -r g/nodes` leaves contributors.yaml with nothing under it. The file is
    unchanged, so this needed only a writer's signature -- and the server then read the
    tip as a phase-1 graph, which is where unsigned invites start being accepted for any
    role and /join stops re-checking signatures at all."""
    origin, work, k = signed
    maria = make_key(keys_dir, "maria")
    add_person(work, "maria", maria, "write")
    commit_signed(work, "seb adds maria", k["seb"])
    assert push(work).returncode == 0
    git("rm", "-rq", "g/nodes", cwd=work)
    commit_signed(work, "maria takes the nodes away", maria)

    r = push(work)

    assert r.returncode != 0
    assert "stops g being a graph" in r.stderr
    assert "g/nodes/hyp-ok.md" in git("ls-tree", "-r", "--name-only", "master", cwd=origin).stdout

    # An admin may: the rule is about who signed, not about the change being forbidden.
    git("reset", "-q", "--hard", "HEAD~1", cwd=work)
    git("rm", "-rq", "g/nodes", cwd=work)
    commit_signed(work, "seb retires the graph", k["seb"])
    assert push(work).returncode == 0, "an admin may take their own graph down"


def test_a_writer_cannot_plant_a_second_graph_naming_themselves_its_admin(signed, keys_dir,
                                                                          rules_yaml):
    """A fresh directory with its own graph.yaml, nodes and contributors.yaml reads as a
    bootstrap, and nobody vouches for a first constitution but itself -- so maria signing
    her own made her admin of it. Two graphs in one hosted repo is also the state where
    `head_graph` refuses to answer anything, for anyone, until a human intervenes."""
    origin, work, k = signed
    maria = make_key(keys_dir, "maria")
    add_person(work, "maria", maria, "write")
    commit_signed(work, "seb adds maria", k["seb"])
    assert push(work).returncode == 0

    mine = work / "mine"
    (mine / "nodes").mkdir(parents=True)
    (mine / "graph.yaml").write_text(rules_yaml, encoding="utf-8")
    (mine / "nodes" / "hyp-mine.md").write_text(
        "---\nid: hyp-mine\ntype: hypothesis\nstatus: open\n---\n\n# mine\n", encoding="utf-8")
    C.dump(mine, {"maria": {"key": pub_line(maria), "role": "admin"}})
    commit_signed(work, "maria starts a graph of her own", maria)

    r = push(work)

    assert r.returncode != 0
    assert "starts a second contributors.yaml" in r.stderr
    assert "mine/contributors.yaml" not in git("ls-tree", "-r", "--name-only", "master",
                                               cwd=origin).stdout


def test_a_name_is_revoked_never_removed(signed, keys_dir):
    """Revocation is a mark so history stays attributable. Deleting the entry erases the
    record the mark exists to keep, and an admin is no more entitled to that than anyone."""
    origin, work, k = signed
    maria = make_key(keys_dir, "maria")
    add_person(work, "maria", maria, "write")
    commit_signed(work, "seb adds maria", k["seb"])
    assert push(work).returncode == 0
    c = C.load(work / "g")
    del c["maria"]
    C.dump(work / "g", c)
    commit_signed(work, "seb erases maria", k["seb"])

    r = push(work)

    assert r.returncode != 0
    assert "revoked, never removed" in r.stderr
    assert "maria" in git("show", "master:g/contributors.yaml", cwd=origin).stdout


def test_a_revoked_writer_cannot_branch_off_the_history_they_could_write(signed, keys_dir):
    """Every commit on the branch checks out -- maria signed hers while she was still
    listed. The REF does not: a second branch is a line of history nobody pulls, and the
    person pushing it is no longer one of this graph's writers."""
    origin, work, k = signed
    maria = make_key(keys_dir, "maria")
    add_person(work, "maria", maria, "write")
    commit_signed(work, "seb adds maria", k["seb"])
    (work / "g" / "nodes" / "hyp-m.md").write_text(
        "---\nid: hyp-m\ntype: hypothesis\nstatus: open\n---\n\n# m\n", encoding="utf-8")
    commit_signed(work, "maria's claim", maria)
    hers = git("rev-parse", "HEAD", cwd=work).stdout.strip()
    assert push(work).returncode == 0
    c = C.load(work / "g")
    c["maria"]["revoked"] = "2026-09-05"
    C.dump(work / "g", c)
    commit_signed(work, "seb revokes maria", k["seb"])
    assert push(work).returncode == 0

    r = git("push", "origin", f"{hers}:refs/heads/marias-work", cwd=work)

    assert r.returncode != 0
    assert "one branch" in r.stderr
    assert "marias-work" not in git("branch", cwd=origin).stdout


def test_a_malformed_contributors_file_refuses_the_push_with_the_parse_error(signed):
    """The gate parses contributors.yaml out of the PUSHED tree. A file that does not
    parse cannot say who may write, so the push is refused and the parse message is what
    the pusher reads -- not a traceback, and not silence."""
    origin, work, k = signed
    (work / "g" / C.FILE).write_text("seb: [1\n", encoding="utf-8")
    commit_signed(work, "seb breaks the constitution", k["seb"])

    r = push(work)

    assert r.returncode != 0
    assert "invalid YAML" in r.stderr
    assert "Traceback" not in r.stderr


def test_a_graph_whose_every_writer_is_revoked_accepts_nothing(signed, keys_dir):
    """The last admin revoking themselves is allowed -- they were active when they signed
    it. What comes after is not: there is no key left that may write here, and an empty
    key set is a refusal, not a crash and not an open door."""
    origin, work, k = signed
    c = C.load(work / "g")
    c["seb"]["revoked"] = "2026-09-05"
    C.dump(work / "g", c)
    commit_signed(work, "seb steps down", k["seb"])
    assert push(work).returncode == 0
    (work / "g" / "nodes" / "hyp-after.md").write_text(
        "---\nid: hyp-after\ntype: hypothesis\nstatus: open\n---\n\n# after\n", encoding="utf-8")
    commit_signed(work, "seb writes anyway", k["seb"])

    r = push(work)

    assert r.returncode != 0
    assert "signed by a key not listed here" in r.stderr


# ------------------------------------------- a constitution with no graph under it

def test_a_writer_cannot_plant_a_constitution_where_no_graph_is_yet(signed, keys_dir):
    """The per-commit loop walked graph dirs, and every rule in it keys on
    contributors.yaml. So `mine/contributors.yaml` alone, with no graph.yaml and no
    nodes/, was judged by nobody: it landed, and the NEXT commit added the graph around
    it, where the constitution reads as unchanged and is checked against the key it
    names. Two commits, and the writer is admin of a second graph the server then chokes
    on."""
    origin, work, k = signed
    maria = make_key(keys_dir, "maria")
    add_person(work, "maria", maria, "write")
    commit_signed(work, "seb adds maria", k["seb"])
    assert push(work).returncode == 0

    (work / "mine").mkdir()
    C.dump(work / "mine", {"maria": {"key": pub_line(maria), "role": "admin"}})
    commit_signed(work, "maria plants a constitution of her own", maria)

    r = push(work)

    assert r.returncode != 0
    assert "starts a second contributors.yaml" in r.stderr
    assert "mine/contributors.yaml" not in git("ls-tree", "-r", "--name-only", "master",
                                               cwd=origin).stdout


def test_a_retired_graphs_constitution_is_still_guarded(signed, keys_dir):
    """An admin may retire a graph, which leaves contributors.yaml with no graph under
    it. While that was true the directory was invisible to the loop, so a writer could
    rewrite the constitution wholesale -- dropping the admin -- and restore nodes/ in a
    second commit. The file is watched wherever it is, graph or no graph."""
    origin, work, k = signed
    maria = make_key(keys_dir, "maria")
    add_person(work, "maria", maria, "write")
    commit_signed(work, "seb adds maria", k["seb"])
    assert push(work).returncode == 0
    git("rm", "-rq", "g/nodes", cwd=work)
    commit_signed(work, "seb retires the graph", k["seb"])
    assert push(work).returncode == 0, "an admin may retire their own graph"

    C.dump(work / "g", {"maria": {"key": pub_line(maria), "role": "admin"}})
    commit_signed(work, "maria rewrites the constitution of a graph nobody is watching", maria)

    r = push(work)

    assert r.returncode != 0
    assert "revoked, never removed" in r.stderr
    assert "seb" in git("show", "master:g/contributors.yaml", cwd=origin).stdout


def test_a_retired_graphs_admins_are_still_the_only_founders(signed, keys_dir):
    """Who may lay down a second constitution is read from the constitutions at the
    parent, and a retired graph still has one. Reading only the parent's GRAPH dirs left
    nobody to vouch for anything the moment a graph was retired, so any writer could
    found its successor and be its admin. Seb still can; maria still cannot."""
    origin, work, k = signed
    maria = make_key(keys_dir, "maria")
    add_person(work, "maria", maria, "write")
    commit_signed(work, "seb adds maria", k["seb"])
    git("rm", "-rq", "g/nodes", cwd=work)
    commit_signed(work, "seb retires the graph", k["seb"])
    assert push(work).returncode == 0

    (work / "next").mkdir()
    C.dump(work / "next", {"maria": {"key": pub_line(maria), "role": "admin"}})
    commit_signed(work, "maria founds the successor and crowns herself", maria)
    r = push(work)
    assert r.returncode != 0
    assert "starts a second contributors.yaml" in r.stderr

    git("reset", "-q", "--hard", "HEAD~1", cwd=work)
    (work / "next").mkdir()
    C.dump(work / "next", {"seb": {"key": pub_line(k["seb"]), "role": "admin"}})
    commit_signed(work, "seb founds the successor", k["seb"])

    assert push(work).returncode == 0, "an admin could not found the successor"


# ------------------------------------------------- a tag is not a branch, ever

def test_a_refused_tag_does_not_spend_the_one_creation_a_push_may_make(bare, monkeypatch,
                                                                      capsys):
    """A push carrying a tag and a branch. The tag is refused for being a tag; if it also
    counted as the push's one creation, the BRANCH line would carry the refusal and the
    pusher would read the wrong ref as the problem. Driven through `main` so the tag line
    is certainly read first, against a hosted repo that holds the objects but no ref."""
    origin, work = bare
    sha = seeded(work)
    assert git("push", "-q", "origin", "master", cwd=work).returncode == 0
    git("update-ref", "-d", "refs/heads/master", cwd=origin)   # the objects stay, the ref goes
    monkeypatch.chdir(origin)

    rc = gate.main(io.StringIO(f"{'0' * 40} {sha} refs/tags/v1\n"
                               f"{'0' * 40} {sha} refs/heads/master\n"))

    err = capsys.readouterr().err
    assert rc == 1
    assert f"refs/tags/v1: {gate.ONE_BRANCH}" in err
    assert f"refs/heads/master: {gate.ONE_BRANCH}" not in err


def test_a_tag_that_predates_the_gate_cannot_be_moved(bare, monkeypatch, capsys):
    """One branch and nothing else holds for updates too. A tag can only exist where it
    was made before the hook was (a repo gated by hand); the gate must still refuse to
    move it, or such a ref stays a second writable line of history."""
    origin, work = bare
    sha = seeded(work)
    assert git("push", "-q", "origin", "master", cwd=work).returncode == 0
    git("update-ref", "refs/tags/v1", sha, cwd=origin)      # the operator's tag, pre-gate
    monkeypatch.chdir(origin)

    rc = gate.main(io.StringIO(f"{sha} {sha} refs/tags/v1\n"))

    assert rc == 1
    assert f"refs/tags/v1: {gate.ONE_BRANCH}" in capsys.readouterr().err


def test_a_constitution_in_an_option_shaped_directory_is_refused(signed):
    """`contributors_dirs` lifts directory names out of the pushed tree exactly as
    `graph_dirs` does, and now feeds them to the same reads. A component starting with
    `-` is an option to git, not a path; one check serves both walks."""
    origin, work, k = signed
    d = work / "-x"
    d.mkdir()
    C.dump(d, {"seb": {"key": pub_line(k["seb"]), "role": "admin"}})
    commit_signed(work, "seb adds a constitution in an option-shaped directory", k["seb"])

    r = push(work)

    assert r.returncode != 0
    assert "would be parsed as a git option" in r.stderr
    assert "-x/contributors.yaml" not in git("ls-tree", "-r", "--name-only", "master",
                                             cwd=origin).stdout


def _finding(nid, title):
    return (f"---\nid: {nid}\ntype: finding\nstatus: alive\n"
            "links:\n"
            "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
            "  - {rel: kn:survivedGate, to: gate-h}\n"
            f"---\n\n# {title}\n")


def test_a_compression_by_a_writer_passes_the_gate(signed, keys_dir):
    """A general node flips other people's nodes to superseded. That is a status change
    to files the writer did not author, and the gate must read it as ordinary writing,
    not as a constitution change.

    The flip is produced by `knoten commit` itself, not hand-written here, so what the
    gate sees is exactly what a contributor's clone would send."""
    from knoten.commit import commit

    origin, work, k = signed
    maria = make_key(keys_dir, "maria")
    add_person(work, "maria", maria, "write")
    g = work / "g"
    (g / "nodes" / "question-q.md").write_text(
        "---\nid: question-q\ntype: question\nstatus: open\n---\n\n# q\n", encoding="utf-8")
    (g / "nodes" / "gate-h.md").write_text(
        "---\nid: gate-h\ntype: gate\nstatus: active\n---\n\n# h\n", encoding="utf-8")
    (g / "nodes" / "finding-1.md").write_text(_finding("finding-1", "small"), encoding="utf-8")
    (g / "nodes" / "finding-2.md").write_text(_finding("finding-2", "large"), encoding="utf-8")
    commit_signed(work, "seb adds maria and two findings", k["seb"])
    assert push(work).returncode == 0

    res = commit(g, "finding-g", "id: finding-g\ntype: finding\nstatus: alive\n"
                 "links:\n"
                 "  - {rel: npx:supersedes, to: finding-1}\n"
                 "  - {rel: npx:supersedes, to: finding-2}\n"
                 "  - {rel: kn:survivedGate, to: gate-h}\n",
                 "# the general claim\n\n## Covers\n- finding-1: the small half\n"
                 "- finding-2: the large half\n")
    assert res["status"] != "REJECTED", res
    commit_signed(work, "maria compresses two findings into one", maria)

    r = push(work)

    assert r.returncode == 0, r.stderr
    for nid in ("finding-1", "finding-2"):
        blob = git("show", f"master:g/nodes/{nid}.md", cwd=origin).stdout
        assert "status: superseded" in blob, blob

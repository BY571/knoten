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


def test_graph_dirs_ignores_a_symlinked_nodes(bare, monkeypatch):
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
    """The clean graph committed, and its sha: the `old` half of every ref line below."""
    git("add", "-A", cwd=work)
    git("commit", "-qm", "seed", cwd=work)
    return git("rev-parse", "HEAD", cwd=work).stdout.strip()


def test_extract_and_graph_dirs_refuse_a_directory_name_git_would_parse_as_an_option(
        bare, rules_yaml, monkeypatch, capsys):
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


# ---------------------------------------------------------------- signatures

from conftest import commit_signed, make_key, pub_line
from knoten import identity as C


@pytest.fixture
def signed(bare, keys_dir):
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


def test_a_key_the_graph_does_not_list_is_refused(signed, keys_dir):
    origin, work, k = signed
    eve = make_key(keys_dir, "eve")
    (work / "g" / "nodes" / "hyp-e.md").write_text(
        "---\nid: hyp-e\ntype: hypothesis\nstatus: open\n---\n\n# e\n", encoding="utf-8")
    commit_signed(work, "eve's claim", eve)
    r = push(work)
    assert r.returncode != 0
    assert "not listed" in r.stderr


def test_a_revoked_key_is_refused_but_its_history_stays(signed, keys_dir):
    """Revocation is a mark."""
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
    """Which contributors were in force "before" a merge is not a single answer."""
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
    origin, work = bare
    monkeypatch.chdir(work)
    rc = gate.main(io.StringIO(f"{'0' * 40} --output=x refs/heads/master\n"))
    err = capsys.readouterr().err
    assert rc == 1
    assert "malformed ref line for refs/heads/master" in err
    assert "Traceback" not in err


# ------------------------------------------------------- moved and deleted graph dirs

def test_a_renamed_graph_is_still_checked_against_its_old_contributors(signed, keys_dir):
    origin, work, k = signed
    eve = make_key(keys_dir, "eve")
    git("mv", "g", "h", cwd=work)
    C.dump(work / "h", {"eve": {"key": pub_line(eve), "role": "admin"}})
    commit_signed(work, "eve takes over", eve)
    r = push(work)
    assert r.returncode != 0
    tree = git("ls-tree", "-r", "--name-only", "master", cwd=origin).stdout
    assert "g/contributors.yaml" in tree


def test_not_even_an_admin_may_move_the_graph_out_from_under_its_constitution(signed):
    origin, work, k = signed
    git("mv", "g", "h", cwd=work)
    commit_signed(work, "seb renames g to h", k["seb"])
    r = push(work)
    assert r.returncode != 0
    assert "revoked, never removed" in r.stderr
    assert "g/contributors.yaml" in git("ls-tree", "-r", "--name-only", "master",
                                        cwd=origin).stdout


# --------------------------------------------------------------- rev-list return codes

# ------------------------------------------------------------------- misc review fixes

# ------------------------------------------------------------ one line of history

def test_a_second_branch_is_refused_even_when_its_tree_is_clean(bare):
    """A hosted graph has ONE line of history."""
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
    origin, work, k = signed
    maria = make_key(keys_dir, "maria")
    add_person(work, "maria", maria, "write", invited_by="seb",
               invite=invite_for(k["seb"], graph, name, role))
    commit_signed(work, "maria joins", maria)
    r = push(work)
    assert r.returncode != 0
    assert "different name, role or graph" in r.stderr


def test_a_join_may_not_touch_anything_but_contributors_yaml(signed, keys_dir):
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
    origin, work, k = signed
    maria, eve = make_key(keys_dir, "maria"), make_key(keys_dir, "eve")
    add_person(work, "maria", maria, "write", invited_by="seb",
               invite=invite_for(k["seb"], "test", "maria", "write"))
    commit_signed(work, "eve commits maria's join", eve)
    r = push(work)
    assert r.returncode != 0
    assert "not signed by maria" in r.stderr


def test_deleting_contributors_yaml_is_refused_whoever_signs(signed, keys_dir):
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


# --------------------------------------------------- a graph, and the only graph

def test_removing_the_graph_but_keeping_its_constitution_needs_an_admin(signed, keys_dir):
    """`git rm -r g/nodes` leaves contributors.yaml with nothing under it."""
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
    """Revocation is a mark so history stays attributable."""
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


# ------------------------------------------- a constitution with no graph under it

def test_a_writer_cannot_plant_a_constitution_where_no_graph_is_yet(signed, keys_dir):
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


# ------------------------------------------------- a tag is not a branch, ever

def test_a_constitution_in_an_option_shaped_directory_is_refused(signed):
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
    """A general node flips other people's nodes to superseded."""
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


# ------------------------------------------- guards that lost their only test in the cut

def test_a_tag_into_a_branchless_gated_repo_is_refused(bare):
    """No branch exists yet, so the one-branch count cannot catch it; the ref rule must."""
    origin, work = bare
    git("tag", "v1", cwd=work)
    assert git("push", "origin", "v1", cwd=work).returncode != 0


def test_moving_a_tag_that_predates_the_gate_is_refused(tmp_path, rules_yaml):
    origin = tmp_path / "o.git"
    git("init", "-q", "--bare", str(origin), cwd=tmp_path)
    work = tmp_path / "w"
    git("clone", "-q", str(origin), str(work), cwd=tmp_path)
    for c in (["config", "user.email", "t@t.t"], ["config", "user.name", "t"]):
        git(*c, cwd=work)
    (work / "a.txt").write_text("1", encoding="utf-8")
    git("add", "-A", cwd=work); git("commit", "-qm", "one", cwd=work)
    git("tag", "v1", cwd=work)
    assert git("push", "-q", "origin", "master", "v1", cwd=work).returncode == 0
    install_server(origin)                                   # gated only now
    (work / "a.txt").write_text("2", encoding="utf-8")
    git("add", "-A", cwd=work); git("commit", "-qm", "two", cwd=work)
    git("tag", "-f", "v1", cwd=work)

    assert git("push", "-f", "origin", "v1", cwd=work).returncode != 0


def test_deleting_a_branch_git_itself_would_not_protect_is_refused(bare):
    """`receive.denyDeleteCurrent` covers the branch HEAD names and nothing else."""
    origin, work = bare
    git("add", "-A", cwd=work); git("commit", "-qm", "seed", cwd=work)
    assert push(work).returncode == 0
    git("symbolic-ref", "HEAD", "refs/heads/elsewhere", cwd=origin)

    assert git("push", "origin", ":master", cwd=work).returncode != 0
    assert git("rev-parse", "--verify", "master", cwd=origin).returncode == 0


def test_one_push_may_create_only_one_branch(bare):
    """git's ref list is the same for every line mid-push, so the gate keeps the count."""
    origin, work = bare
    git("add", "-A", cwd=work); git("commit", "-qm", "seed", cwd=work)
    git("branch", "second", cwd=work)

    assert git("push", "origin", "master", "second", cwd=work).returncode != 0
    assert len(git("branch", "--list", cwd=origin).stdout.split()) <= 1


def test_a_reader_listed_in_contributors_may_not_sign_a_commit(signed, keys_dir):
    origin, work, keys = signed
    rea = make_key(keys_dir, "rea")
    add_person(work, "rea", rea, "read")
    commit_signed(work, "seb lists a reader", keys["seb"])
    assert push(work).returncode == 0
    (work / "g" / "nodes" / "hyp-r.md").write_text(
        "---\nid: hyp-r\ntype: hypothesis\nstatus: open\n---\n\n# r\n", encoding="utf-8")
    commit_signed(work, "the reader writes", rea)

    assert push(work).returncode != 0


def test_a_join_naming_a_different_inviter_is_refused(signed, keys_dir):
    origin, work, keys = signed
    ann = make_key(keys_dir, "ann")
    add_person(work, "ann", ann, "admin")
    commit_signed(work, "seb adds ann", keys["seb"])
    assert push(work).returncode == 0
    maria = make_key(keys_dir, "maria")
    add_person(work, "maria", maria, "write", invited_by="ann",     # the entry credits ann...
               invite=invite_for(keys["seb"], "test", "maria", "write"))   # ...but seb signed
    commit_signed(work, "maria joins, crediting ann", maria)

    assert push(work).returncode != 0


def test_a_graph_whose_every_writer_is_revoked_accepts_nothing(signed):
    origin, work, keys = signed
    c = C.load(work / "g"); c["seb"]["revoked"] = "2026-01-01"; C.dump(work / "g", c)
    commit_signed(work, "seb steps down", keys["seb"])
    assert push(work).returncode == 0
    commit_node(work / "g", "hyp-x.md", "---\nid: hyp-x\ntype: hypothesis\nstatus: open\n---\n\n# x\n")

    assert push(work).returncode != 0          # an unsigned commit with no live writer


def test_check_ref_fails_closed_on_a_rev_list_error(bare, monkeypatch):
    """A git error is a refusal, not "nothing to check"."""
    origin, work = bare
    monkeypatch.chdir(origin)

    with pytest.raises(gate.GraphError, match="cannot list commits"):
        gate.check_ref("0" * 40, "a" * 40, "refs/heads/master")


def test_git_gives_children_no_access_to_the_hooks_stdin(bare, monkeypatch):
    """The hook's stdin IS git's ref list; a child that read it would starve main()."""
    origin, work = bare
    monkeypatch.chdir(work)

    assert gate._git("hash-object", "--stdin").stdout.strip() == b"e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"

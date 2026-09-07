import json
import os
import stat

import pytest

from knoten.core import GraphError
from knoten.registry import ROLES, Registry, _hash


@pytest.fixture
def reg(tmp_path):
    return Registry(tmp_path / "data")


def test_the_owner_secret_is_created_once_and_kept_private(reg):
    """Printed once by `knoten serve`."""
    first = reg.owner_secret()
    assert first == reg.owner_secret()
    assert len(first) >= 32
    mode = stat.S_IMODE((reg.data / "owner").stat().st_mode)
    assert mode == 0o600, f"owner secret is world-readable: {oct(mode)}"


def test_create_refuses_a_second_graph_of_the_same_name(reg):
    reg.create("trading", admin="seb")
    with pytest.raises(GraphError, match="already exists"):
        reg.create("trading", admin="seb")


@pytest.mark.parametrize("bad", ["../etc", "Trading", "a b", "", "-x", "x/y"])
def test_graph_names_outside_id_re_are_refused_before_touching_disk(reg, bad):
    """A graph name becomes a directory."""
    with pytest.raises(GraphError, match="not a valid graph name"):
        reg.create(bad, admin="seb")
    assert sorted(p.name for p in (reg.data / "graphs").iterdir()) == []


def test_create_rolls_back_on_partial_failure(reg, monkeypatch):
    import knoten.registry
    def failing_install(repo, **kwargs):     # **kwargs, or a signature mismatch would
        raise OSError("disk full")           # pass this test for the wrong reason
    monkeypatch.setattr(knoten.registry, "install_server", failing_install)
    with pytest.raises(GraphError, match="could not create"):
        reg.create("trading", admin="seb")
    assert not reg.exists("trading")
    assert sorted(p.name for p in (reg.data / "graphs").iterdir()) == []
    # Restore monkeypatch and retry succeeds
    monkeypatch.undo()
    reg.create("trading", admin="seb")
    assert reg.exists("trading")


# ---------------------------------------------------------------- tokens

@pytest.mark.parametrize("graph, user, token", [
    pytest.param("trading", "maria", "not-it", id="wrong-token"),
    pytest.param("trading", "seb", "MARIAS", id="another-users-token"),
    pytest.param("biology", "maria", "MARIAS", id="no-such-graph"),
    pytest.param("trading", "maria", "", id="empty-token"),
    pytest.param("trading", "", "MARIAS", id="empty-user"),
])
def test_authenticate_fails_closed(reg, graph, user, token):
    """Every near miss is None, never a role. One wrong half is a whole refusal."""
    reg.create("trading", admin="seb")
    marias = reg.mint("trading", "maria", "write")

    assert reg.authenticate(graph, user, marias if token == "MARIAS" else token) is None


def test_tokens_are_stored_hashed(reg):
    """A leaked tokens.json must be worthless."""
    reg.create("trading", admin="seb")
    tok = reg.mint("trading", "maria", "write")

    on_disk = (reg.graph_dir("trading") / "tokens.json").read_text(encoding="utf-8")
    assert tok not in on_disk
    assert "maria" in on_disk


# ---------------------------------------------------------------- invites

def test_an_expired_invite_is_refused_and_spent(reg):
    reg.create("trading", admin="seb")
    code = reg.invite("trading", "maria", "write")
    # days is clamped to 1..365 now, so a stale invite is made by aging the stored entry
    # rather than by asking for a negative lifetime.
    p = reg.graph_dir("trading") / "invites.json"
    stored = json.loads(p.read_text(encoding="utf-8"))
    stored[_hash(code)]["expires"] = "2000-01-01T00:00:00+00:00"
    p.write_text(json.dumps(stored), encoding="utf-8")
    with pytest.raises(GraphError, match="expired"):
        reg.redeem("trading", code)
    with pytest.raises(GraphError, match="not valid"):
        reg.redeem("trading", code)


def test_secrets_that_travel_on_a_command_line_never_start_with_a_dash(reg):
    """`--owner-secret VALUE` and `--invite CODE` are argv."""
    reg.create("trading", admin="seb")
    for _ in range(64):
        assert not reg.invite("trading", "maria", "write").startswith("-")
    assert not reg.owner_secret().startswith("-")
    assert all(c in "0123456789abcdef" for c in reg.owner_secret())


# ---------------------------------------------------------------- names, dirs, files

def test_recreating_a_graph_over_a_leftover_directory_is_refused(reg):
    import shutil
    reg.create("trading", admin="seb")
    tok = reg.mint("trading", "maria", "write")
    shutil.rmtree(reg.repo("trading"))
    with pytest.raises(GraphError, match="leftover directory"):
        reg.create("trading", admin="seb")
    assert reg.authenticate("trading", "maria", tok) is None, "a stale token still works"


def test_check_owner_is_false_when_the_owner_file_is_empty(reg):
    reg.data.mkdir(parents=True, exist_ok=True)
    (reg.data / "owner").write_text("", encoding="utf-8")
    assert not reg.check_owner("")
    assert not reg.check_owner("anything")


def test_the_shared_repo_refuses_rewrites_and_deletions(reg):
    import subprocess
    reg.create("trading", admin="seb")
    repo = reg.repo("trading")
    def config(key):
        return subprocess.run(["git", "-C", str(repo), "config", key],
                              capture_output=True, text=True).stdout.strip()
    assert config("receive.denyNonFastForwards") == "true"
    assert config("receive.denyDeletes") == "true"
    assert config("core.logAllRefUpdates") == "true"
    assert config("receive.fsckObjects") == "true"


# ---------------------------------------------------------------- invite bookkeeping

def test_revoking_an_admin_kills_the_invites_they_issued(reg):
    reg.create("trading", admin="seb")
    rogue = reg.mint("trading", "rogue", "admin")
    assert reg.authenticate("trading", "rogue", rogue) == "admin"
    code = reg.invite("trading", "friend", "admin", by="rogue")
    reg.revoke("trading", "rogue")
    with pytest.raises(GraphError, match="not valid"):
        reg.redeem("trading", code)
    assert reg.invites("trading") == []


# ---------------------------------------------------------------- signed invites

from conftest import commit_signed, git, make_key, pub_line
from knoten import identity as C


@pytest.fixture
def pushed_signed_graph(reg, tmp_path, keys_dir, rules_yaml):
    """A hosted graph with a real graph in it, pushed through the real gate."""
    def build(name="trading", branch="master", sign=True, admin="seb"):
        reg.create(name, admin=admin)
        work = tmp_path / f"work-{name}"
        git("clone", "-q", str(reg.repo(name)), str(work), cwd=tmp_path)
        for c in (["config", "user.email", "t@t.t"], ["config", "user.name", "t"]):
            git(*c, cwd=work)
        if branch != "master":
            git("checkout", "-q", "-b", branch, cwd=work)
        (work / "nodes").mkdir()
        (work / "graph.yaml").write_text(rules_yaml, encoding="utf-8")
        (work / "nodes" / "hyp-ok.md").write_text(
            "---\nid: hyp-ok\ntype: hypothesis\nstatus: open\n---\n\n# ok\n", encoding="utf-8")
        priv = make_key(keys_dir, admin)
        if sign:
            C.dump(work, {admin: {"key": pub_line(priv), "role": "admin"}})
            commit_signed(work, f"{admin} creates the graph", priv)
        else:
            git("add", "-A", cwd=work)
            git("commit", "-qm", "seed", cwd=work)
        r = git("push", "-q", "origin", branch, cwd=work)
        assert r.returncode == 0, r.stderr
        return work, priv
    return build


# ---------------------------------------------------------------- head_graph: review fixes

def test_head_graph_resolves_a_repo_pushed_only_as_main(reg, pushed_signed_graph):
    _, seb = pushed_signed_graph(branch="main")
    contribs, name = reg.head_graph("trading")
    assert contribs == {"seb": {"key": pub_line(seb), "role": "admin"}}
    assert name == "test"


def test_head_graph_refuses_a_repo_holding_two_graphs_at_the_tip(reg, tmp_path, rules_yaml):
    reg.create("trading", admin="seb")
    work = tmp_path / "w"
    git("clone", "-q", str(reg.repo("trading")), str(work), cwd=tmp_path)
    for c in (["config", "user.email", "t@t.t"], ["config", "user.name", "t"]):
        git(*c, cwd=work)
    for name in ("a", "b"):
        (work / name / "nodes").mkdir(parents=True)
        (work / name / "graph.yaml").write_text(rules_yaml, encoding="utf-8")
        (work / name / "nodes" / "hyp-ok.md").write_text(
            "---\nid: hyp-ok\ntype: hypothesis\nstatus: open\n---\n\n# ok\n", encoding="utf-8")
    git("add", "-A", cwd=work)
    git("commit", "-qm", "two graphs", cwd=work)
    assert git("push", "-q", "origin", "master", cwd=work).returncode == 0
    with pytest.raises(GraphError, match="holds 2 graphs"):
        reg.head_graph("trading")


def test_head_graph_ignores_a_stray_git_dir_in_the_environment(reg, tmp_path,
                                                               pushed_signed_graph,
                                                               monkeypatch):
    _, seb = pushed_signed_graph()
    other = tmp_path / "somewhere-else"
    git("init", "-q", "--bare", str(other), cwd=tmp_path)
    monkeypatch.setenv("GIT_DIR", str(other))
    contribs, name = reg.head_graph("trading")
    assert contribs == {"seb": {"key": pub_line(seb), "role": "admin"}}
    assert name == "test"


def test_head_graph_refuses_a_repo_with_several_branches_and_no_head(reg, pushed_signed_graph):
    pushed_signed_graph()
    repo = reg.repo("trading")
    sha = git("rev-parse", "master", cwd=repo).stdout.strip()
    git("update-ref", "refs/heads/rescued", sha, cwd=repo)
    git("symbolic-ref", "HEAD", "refs/heads/never-created", cwd=repo)
    with pytest.raises(GraphError, match="several branches and no HEAD") as e:
        reg.head_graph("trading")
    # The refusal travels to an ordinary contributor through /invite's 400 body.
    assert "--git-dir" not in str(e.value)
    assert str(reg.data) not in str(e.value)


def test_head_graph_refuses_a_tip_that_holds_a_constitution_but_no_graph(reg,
                                                                         pushed_signed_graph):
    """`git rm -r nodes` leaves contributors.yaml behind."""
    work, seb = pushed_signed_graph()
    git("rm", "-rq", "nodes", cwd=work)
    commit_signed(work, "seb retires the graph", seb)
    assert git("push", "-q", "origin", "master", cwd=work).returncode == 0
    with pytest.raises(GraphError, match="holds a contributors.yaml but no graph"):
        reg.head_graph("trading")


# ---------------------------------------------------------------- create(): review fixes

def test_create_rolls_back_if_the_gate_did_not_land(tmp_path, monkeypatch):
    import knoten.registry as registry_mod
    monkeypatch.setattr(registry_mod, "install_server",
                        lambda repo, force=False, env=None: repo / "hooks" / "pre-receive")
    reg = Registry(tmp_path / "data")
    with pytest.raises(GraphError, match="did not land"):
        reg.create("trading", admin="seb")
    assert not reg.exists("trading")


# ---------------------------------------------------------------- redeem(): review fixes

def test_redeem_refuses_an_invite_issued_before_the_graph_was_signed(reg, tmp_path, keys_dir,
                                                                     rules_yaml):
    reg.create("trading", admin="seb")
    code = reg.invite("trading", "maria", "write", by="seb")
    seb = make_key(keys_dir, "seb")
    work = tmp_path / "w"
    git("clone", "-q", str(reg.repo("trading")), str(work), cwd=tmp_path)
    for c in (["config", "user.email", "t@t.t"], ["config", "user.name", "t"]):
        git(*c, cwd=work)
    (work / "nodes").mkdir()
    (work / "graph.yaml").write_text(rules_yaml, encoding="utf-8")
    (work / "nodes" / "hyp-ok.md").write_text(
        "---\nid: hyp-ok\ntype: hypothesis\nstatus: open\n---\n\n# ok\n", encoding="utf-8")
    C.dump(work, {"seb": {"key": pub_line(seb), "role": "admin"}})
    commit_signed(work, "seb signs the graph after the fact", seb)
    assert git("push", "-q", "origin", "master", cwd=work).returncode == 0
    contribs, _ = reg.head_graph("trading")
    with pytest.raises(GraphError, match="issued before this graph was signed"):
        reg.redeem("trading", code, lambda: contribs)
    with pytest.raises(GraphError, match="not valid"):
        reg.redeem("trading", code, lambda: contribs)          # spent either way



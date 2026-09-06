"""What a server knows that the graph does not: who may connect, who is invited, who
owns the box. Files, hashed, locked. Losing this directory loses availability, never the
meaning of a graph."""
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
    """Printed once by `knoten serve`. If it changed between calls, the owner would be
    locked out of their own server on restart."""
    first = reg.owner_secret()

    assert first == reg.owner_secret()
    assert len(first) >= 32
    mode = stat.S_IMODE((reg.data / "owner").stat().st_mode)
    assert mode == 0o600, f"owner secret is world-readable: {oct(mode)}"


def test_check_owner_accepts_only_the_secret(reg):
    assert reg.check_owner(reg.owner_secret())
    assert not reg.check_owner("nope")
    assert not reg.check_owner("")


def test_create_makes_a_gated_bare_repo(reg):
    """The gate is installed at creation, not later. A remote graph that exists for even
    one push without it has accepted whatever that push contained."""
    reg.create("trading", admin="seb")

    repo = reg.repo("trading")
    assert (repo / "HEAD").exists(), "not a git repo"
    assert not (repo / ".git").exists(), "not bare"
    hook = repo / "hooks" / "pre-receive"
    assert hook.exists() and os.access(hook, os.X_OK)
    assert "knoten" in hook.read_text(encoding="utf-8")


def test_create_refuses_a_second_graph_of_the_same_name(reg):
    reg.create("trading", admin="seb")

    with pytest.raises(GraphError, match="already exists"):
        reg.create("trading", admin="seb")


@pytest.mark.parametrize("bad", ["../etc", "Trading", "a b", "", "-x", "x/y"])
def test_graph_names_outside_id_re_are_refused_before_touching_disk(reg, bad):
    """A graph name becomes a directory. `../etc` would be created outside the data
    dir; the check has to run before mkdir, not after."""
    with pytest.raises(GraphError, match="not a valid graph name"):
        reg.create(bad, admin="seb")

    assert sorted(p.name for p in (reg.data / "graphs").iterdir()) == []


def test_repo_of_an_unknown_graph_is_an_error_not_a_path(reg):
    with pytest.raises(GraphError, match="no graph 'nope'"):
        reg.repo("nope")
    assert not reg.exists("nope")


def test_create_rolls_back_on_partial_failure(reg, monkeypatch):
    """A half-made repo that exists() calls valid would accept pushes with no gate, forever.
    Partial creation must be rolled back atomically so a retry can succeed."""
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

def test_a_minted_token_authenticates_with_its_role(reg):
    reg.create("trading", admin="seb")
    tok = reg.mint("trading", "maria", "write")

    assert reg.authenticate("trading", "maria", tok) == "write"


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


def test_create_returns_a_working_admin_token(reg):
    tok = reg.create("trading", admin="seb")

    assert reg.authenticate("trading", "seb", tok) == "admin"


@pytest.mark.parametrize("role", ["owner", "Admin", "", "rw"])
def test_a_role_outside_the_three_is_refused(reg, role):
    reg.create("trading", admin="seb")
    with pytest.raises(GraphError, match="role must be one of"):
        reg.mint("trading", "maria", role)


def test_contributor_names_follow_id_re(reg):
    """The name ends up as a JSON key and, in phase 2, as a filename in graph.yaml."""
    reg.create("trading", admin="seb")
    with pytest.raises(GraphError, match="not a valid contributor name"):
        reg.mint("trading", "Maria Lopez", "write")


def test_revoke_ends_access_and_nothing_else(reg):
    reg.create("trading", admin="seb")
    tok = reg.mint("trading", "maria", "write")

    reg.revoke("trading", "maria")

    assert reg.authenticate("trading", "maria", tok) is None
    assert reg.exists("trading")


def test_revoking_a_stranger_is_an_error(reg):
    reg.create("trading", admin="seb")
    with pytest.raises(GraphError, match="no contributor 'ghost'"):
        reg.revoke("trading", "ghost")


def test_revoking_on_an_unknown_graph_is_a_graph_error(reg):
    """graph_lock opens a file inside the graph directory. Without an existence check
    first, a well-formed name for a graph that does not exist raised a raw
    FileNotFoundError instead of the one-line refusal every other method gives."""
    with pytest.raises(GraphError, match="no graph 'biology'"):
        reg.revoke("biology", "maria")


# ---------------------------------------------------------------- invites

def test_an_invite_redeems_once_into_a_working_token(reg):
    reg.create("trading", admin="seb")
    code = reg.invite("trading", "maria", "write")

    user, role, tok, _ = reg.redeem("trading", code)

    assert (user, role) == ("maria", "write")
    assert reg.authenticate("trading", "maria", tok) == "write"
    with pytest.raises(GraphError, match="not valid"):
        reg.redeem("trading", code)                       # spent


def test_an_expired_invite_is_refused_and_spent(reg):
    """Expired codes are removed when tried, so a stale invites.json does not grow
    forever and a late guess cannot be retried after the clock is fixed."""
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


def test_invite_codes_are_stored_hashed(reg):
    reg.create("trading", admin="seb")
    code = reg.invite("trading", "maria", "write")

    assert code not in (reg.graph_dir("trading") / "invites.json").read_text(encoding="utf-8")


def test_a_wrong_code_and_a_wrong_graph_both_fail(reg):
    reg.create("trading", admin="seb")
    reg.invite("trading", "maria", "write")

    with pytest.raises(GraphError, match="not valid"):
        reg.redeem("trading", "guess")
    with pytest.raises(GraphError, match="no graph 'biology'"):
        reg.redeem("biology", "anything")


def test_secrets_that_travel_on_a_command_line_never_start_with_a_dash(reg):
    """`--owner-secret VALUE` and `--invite CODE` are argv. A value beginning with `-`
    is a flag to argparse, and token_urlsafe produced one about one run in five."""
    reg.create("trading", admin="seb")
    for _ in range(64):
        assert not reg.invite("trading", "maria", "write").startswith("-")
    assert not reg.owner_secret().startswith("-")
    assert all(c in "0123456789abcdef" for c in reg.owner_secret())


# ---------------------------------------------------------------- names, dirs, files

def test_a_name_longer_than_the_cap_is_refused(reg):
    """A graph name becomes a directory and a URL segment. ID_RE bounds the alphabet and
    nothing bounded the length, so NAME_MAX surfaced as an opaque OSError from mkdir
    after the data directory had been touched."""
    with pytest.raises(GraphError, match="too long"):
        reg.create("a" * 65, admin="seb")

    assert sorted(p.name for p in (reg.data / "graphs").iterdir()) == []
    reg.create("a" * 64, admin="seb")            # the cap itself is allowed


def test_recreating_a_graph_over_a_leftover_directory_is_refused(reg):
    """Deleting a graph by removing repo.git left tokens.json behind, and the graph
    recreated under that name inherited it: the deleted graph's tokens authenticated on
    the new one, which nobody had invited them to."""
    import shutil
    reg.create("trading", admin="seb")
    tok = reg.mint("trading", "maria", "write")
    shutil.rmtree(reg.repo("trading"))

    with pytest.raises(GraphError, match="leftover directory"):
        reg.create("trading", admin="seb")

    assert reg.authenticate("trading", "maria", tok) is None, "a stale token still works"


def test_the_data_directory_is_private_to_its_user(reg):
    """Tokens, invite hashes and the owner secret live under it. The 0755 mkdir defaults
    to made all of it readable by every other local account on the server."""
    reg.create("trading", admin="seb")

    for d in (reg.data, reg.data / "graphs", reg.graph_dir("trading")):
        assert stat.S_IMODE(d.stat().st_mode) == 0o700, f"{d} is {oct(d.stat().st_mode)}"


def test_check_owner_is_false_when_the_owner_file_is_empty(reg):
    """An empty owner file made "" a valid secret, so a truncated file handed graph
    creation to anyone who sent no password at all. check_owner must also not CREATE
    one: it is reached unauthenticated, and `knoten serve` is what shows the owner theirs."""
    reg.data.mkdir(parents=True, exist_ok=True)
    (reg.data / "owner").write_text("", encoding="utf-8")

    assert not reg.check_owner("")
    assert not reg.check_owner("anything")


def test_check_owner_is_false_when_there_is_no_owner_file(reg):
    assert not reg.check_owner("anything")
    assert not (reg.data / "owner").exists(), "check_owner minted a secret nobody saw"


def test_the_shared_repo_refuses_rewrites_and_deletions(reg):
    """A `write` collaborator's stray `--force` wiped the shared graph with no reflog to
    recover from. The refusal has to be in the repo's own config, not in one clone."""
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

@pytest.mark.parametrize("days", [0, -1, 366, 999999999999])
def test_an_invite_lifetime_outside_the_range_is_refused(reg, days):
    """`--expires 999999999999` reached timedelta, which raised OverflowError and killed
    the serving thread with no response at all."""
    reg.create("trading", admin="seb")

    with pytest.raises(GraphError, match="between 1 and 365"):
        reg.invite("trading", "maria", "write", days=days)


def test_a_malformed_expiry_is_a_refusal_not_a_crash(reg):
    """A hand-edited or truncated invites.json used to raise ValueError out of
    fromisoformat, which killed the serving thread instead of refusing the code."""
    reg.create("trading", admin="seb")
    code = reg.invite("trading", "maria", "write")
    p = reg.graph_dir("trading") / "invites.json"
    stored = json.loads(p.read_text(encoding="utf-8"))
    stored[_hash(code)]["expires"] = "not-a-date"
    p.write_text(json.dumps(stored), encoding="utf-8")

    with pytest.raises(GraphError, match="malformed"):
        reg.redeem("trading", code)


def test_invites_lists_the_open_ones_and_never_their_hashes(reg):
    """An admin needs to see who is still pending. The hash is the one thing in that
    file worth stealing, so it must not travel back out of it."""
    reg.create("trading", admin="seb")
    code = reg.invite("trading", "maria", "write", days=3, by="seb")

    listed = reg.invites("trading")

    assert listed == [{"name": "maria", "role": "write",
                       "expires": listed[0]["expires"], "by": "seb"}]
    assert _hash(code) not in json.dumps(listed)
    reg.redeem("trading", code)
    assert reg.invites("trading") == [], "a spent invite is still listed as open"


def test_revoking_a_user_takes_their_pending_invite_with_them(reg):
    """Revoking maria's token left the invite she had not yet redeemed alive, so she
    redeemed it the next day and was back in."""
    reg.create("trading", admin="seb")
    reg.mint("trading", "maria", "write")
    code = reg.invite("trading", "maria", "write", by="seb")

    reg.revoke("trading", "maria")

    with pytest.raises(GraphError, match="not valid"):
        reg.redeem("trading", code)


def test_revoking_an_admin_kills_the_invites_they_issued(reg):
    """A revoked admin's earlier invite still redeemed, and it redeemed as admin: the
    revocation ended their token and nothing they had already handed out."""
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
from knoten import contributors as C


@pytest.fixture
def pushed_signed_graph(reg, tmp_path, keys_dir, rules_yaml):
    """A hosted graph with a real graph in it, pushed through the real gate.

    Seven tests below built this by hand, twelve identical lines each. Returns a callable
    so the two things that actually vary stay visible at the call site: `branch` (a repo
    pushed only as `main` is the shape that made `head_graph` read a signed graph as
    unsigned) and `sign` (False leaves it phase-1, with no contributors.yaml at all).
    """
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


def test_head_graph_is_none_for_a_fresh_or_phase_1_repo(reg):
    reg.create("trading", admin="seb")
    assert reg.head_graph("trading") == (None, "")


def test_head_graph_reads_contributors_and_the_graph_name(reg, pushed_signed_graph):
    _, seb = pushed_signed_graph()

    contribs, name = reg.head_graph("trading")

    assert contribs == {"seb": {"key": pub_line(seb), "role": "admin"}}
    assert name == "test"


def test_invite_stores_and_redeem_returns_the_signed_blob(reg):
    reg.create("trading", admin="seb")
    code = reg.invite("trading", "maria", "write", by="seb", blob='{"x":1}', sig="SIG")

    user, role, token, extra = reg.redeem("trading", code)

    assert (user, role) == ("maria", "write")
    assert extra == {"blob": '{"x":1}', "sig": "SIG", "by": "seb"}
    assert reg.authenticate("trading", "maria", token) == "write"


def test_invites_listing_never_shows_the_signature_or_blob(reg):
    """The blob is harmless but the listing is for humans; keep it to who and when."""
    reg.create("trading", admin="seb")
    reg.invite("trading", "maria", "write", by="seb", blob='{"x":1}', sig="SIG")
    (entry,) = reg.invites("trading")
    assert set(entry) == {"name", "role", "expires", "by"}


# ---------------------------------------------------------------- head_graph: review fixes

def test_head_graph_resolves_a_repo_pushed_only_as_main(reg, pushed_signed_graph):
    """`remote create` runs `git push -u origin HEAD` -- whatever branch the user is on,
    `main` as often as `master`. A bare repo made by `git init --bare` still has HEAD
    pointing at refs/heads/master, which a push to `main` never creates: reading only
    `HEAD` said "unsigned" for a graph that is fully bootstrapped and signed, just on a
    differently named branch."""
    _, seb = pushed_signed_graph(branch="main")

    contribs, name = reg.head_graph("trading")

    assert contribs == {"seb": {"key": pub_line(seb), "role": "admin"}}
    assert name == "test"


def test_head_graph_refuses_a_repo_holding_two_graphs_at_the_tip(reg, tmp_path, rules_yaml):
    """A signed remote holds exactly one graph, because that is the one contributors.yaml
    an invite is checked against. Neither graph here is signed, so the gate has nothing to
    weigh a second constitution against and this shape can still be pushed."""
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
    """An absolute GIT_DIR left in the operator's shell outranks `-C`; head_graph must
    build its git env from server_git_env(), not `os.environ` wholesale, or a stray
    GIT_DIR points every read at a repo nobody asked for."""
    _, seb = pushed_signed_graph()

    other = tmp_path / "somewhere-else"
    git("init", "-q", "--bare", str(other), cwd=tmp_path)
    monkeypatch.setenv("GIT_DIR", str(other))

    contribs, name = reg.head_graph("trading")

    assert contribs == {"seb": {"key": pub_line(seb), "role": "admin"}}
    assert name == "test"


def test_head_graph_fixes_head_after_resolving_a_lone_branch(reg, pushed_signed_graph):
    """The branch fallback runs exactly once: after `head_graph` resolves a `main`-only
    repo, HEAD itself points there, so a branch that reaches this repo some other way
    cannot retroactively turn a working graph into "several branches and no HEAD"."""
    pushed_signed_graph(branch="main")

    reg.head_graph("trading")

    r = git("symbolic-ref", "HEAD", cwd=reg.repo("trading"))
    assert r.stdout.strip() == "refs/heads/main"


def test_head_graph_refuses_a_repo_with_several_branches_and_no_head(reg, pushed_signed_graph):
    """The gate now refuses a second branch, so this state cannot be reached by pushing:
    it is what a repo looks like after an operator wrote refs by hand, or restored a
    backup, and pointed HEAD at a branch that is not there. There is no single line of
    history to check an invite against and only a human can say which one was meant."""
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
    """`git rm -r nodes` leaves contributors.yaml behind. Reading that as "no graph, so
    phase-1" is how a signed graph turned back into an unsigned one: signed invites
    refused, UNSIGNED invites minted for any role, and /join no longer re-checking a
    signature it thought was not there. An admin may retire a graph; the server may not
    guess what the result means."""
    work, seb = pushed_signed_graph()
    git("rm", "-rq", "nodes", cwd=work)
    commit_signed(work, "seb retires the graph", seb)
    assert git("push", "-q", "origin", "master", cwd=work).returncode == 0

    with pytest.raises(GraphError, match="holds a contributors.yaml but no graph"):
        reg.head_graph("trading")


# ---------------------------------------------------------------- create(): review fixes

def test_create_installs_the_gate_even_under_a_stray_git_dir(tmp_path, monkeypatch):
    """`rev-parse --git-path hooks` answers relative to GIT_DIR when one is set, not
    `-C`: an absolute GIT_DIR in the daemon's own environment used to make
    `install_server` ask the WRONG repo where its hooks live, write the gate there, and
    leave the real hosted repo ungated -- an unsigned push into it was accepted."""
    reg = Registry(tmp_path / "data")
    other = tmp_path / "somewhere-else"
    git("init", "-q", "--bare", str(other), cwd=tmp_path)
    monkeypatch.setenv("GIT_DIR", str(other))

    reg.create("trading", admin="seb")

    assert (reg.repo("trading") / "hooks" / "pre-receive").exists()
    assert not (other / "hooks" / "pre-receive").exists()


def test_create_rolls_back_if_the_gate_did_not_land(tmp_path, monkeypatch):
    """A postcondition, not just a fixed environment: if `install_server` ever reports
    success without actually writing hooks/pre-receive, `create()` must not hand out a
    working admin token for a repo nothing is gating."""
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
    """An invite minted while the graph was still phase-1 authorises nothing once the
    graph has an admin key to check signatures against: it never carried one."""
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


def test_redeem_never_calls_contribs_for_when_the_code_is_bad(reg):
    """A bogus code must not pay for the git read `contribs_for` implies, and must not
    surface whatever a misconfigured hosted repo's own callable might raise -- it must
    never run at all before the code is confirmed to exist."""
    reg.create("trading", admin="seb")

    def boom():
        raise AssertionError("contribs_for must not be called for an invalid code")

    with pytest.raises(GraphError, match="not valid"):
        reg.redeem("trading", "bogus-code", boom)


def test_redeem_converts_a_confused_repos_error_into_the_uniform_refusal(reg):
    """A repo with several branches and no HEAD raises from inside `contribs_for`; that
    must not leak its own wording through /join, nor read any differently from a plain
    bad code -- both are the one refusal this route ever gives for a code that no
    longer works."""
    reg.create("trading", admin="seb")
    code = reg.invite("trading", "maria", "write", by="seb")

    def boom():
        raise GraphError("graph 'trading' has several branches and no HEAD")

    with pytest.raises(GraphError, match="^that invite code is not valid for this graph$"):
        reg.redeem("trading", code, boom)

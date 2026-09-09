import json
import os
import stat

import pytest
from conftest import commit_node, git, make_key, pub_line

from knoten import remote
from knoten import identity as C
from knoten.cli import main
from knoten.core import GraphError, today
from knoten.identity import key_dir, public_line
from knoten.remote import _explain, cred_lookup, cred_path, cred_store, credential_helper


@pytest.fixture(autouse=True)
def isolated_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOTEN_CREDENTIALS", str(tmp_path / "creds"))


# ---------------------------------------------------------------- the store

def test_store_and_lookup_round_trip_per_remote():
    cred_store("https://h.example/trading.git", "seb", "tok-1")
    cred_store("https://h.example/biology.git", "seb", "tok-2")
    assert cred_lookup("https://h.example/trading.git") == ("seb", "tok-1")
    assert cred_lookup("https://h.example/biology.git") == ("seb", "tok-2")
    assert cred_lookup("https://h.example/nope.git") is None
    assert cred_lookup("http://h.example/trading.git") is None


def test_a_schemeless_key_is_never_matched_by_stripping_the_scheme():
    """A fallback to the old `<netloc><path>` key leaked the owner secret."""
    p = cred_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("h.example:8899 owner SERVER-WIDE-SECRET\n"
                 "h.example:8899/trading.git maria tok\n", encoding="utf-8")
    assert cred_lookup("https://h.example:8899/") is None
    assert cred_lookup("https://h.example:8899") is None
    assert cred_lookup("https://h.example:8899/x.git") is None
    assert cred_lookup("https://h.example:8899/trading.git") is None
    assert credential_helper("protocol=https\nhost=h.example:8899\n") == ""
    assert p.read_text(encoding="utf-8").splitlines()[0].startswith("h.example:8899 owner")


# ---------------------------------------------------------------- git's protocol

def test_the_helper_will_not_hand_an_https_token_to_plain_http():
    cred_store("https://h.example/trading.git", "maria", "tok")
    assert credential_helper("protocol=http\nhost=h.example\npath=trading.git\n") == ""


def test_the_helper_never_hands_out_the_owner_secret():
    """The owner secret opens every graph on a server."""
    cred_store("owner://h.example", "owner", "the-owner-secret")
    assert credential_helper("protocol=https\nhost=h.example\n") == ""
    assert credential_helper("protocol=https\nhost=h.example\npath=\n") == ""
    assert credential_helper("protocol=owner\nhost=h.example\n") == ""


# ---------------------------------------------------------------- create, push, pull

@pytest.fixture
def shared(hub, local_graph, monkeypatch):
    """`local_graph` created on `hub` by its admin through the CLI, cwd inside it."""
    monkeypatch.chdir(local_graph)
    assert main(["remote", "create", "trading", "--on", hub.url, "--as", "seb",
                 "--owner-secret", hub.secret]) == 0
    return local_graph


@pytest.fixture
def shared_signed(hub, local_graph, monkeypatch):
    """`shared`, in phase 2: the graph was created with signing bootstrapped."""
    monkeypatch.chdir(local_graph)
    assert main(["remote", "create", "trading", "--on", hub.url, "--as", "seb",
                 "--owner-secret", hub.secret]) == 0
    return local_graph


def test_remote_create_with_the_wrong_owner_secret_is_one_line(hub, local_graph, monkeypatch, capsys):
    monkeypatch.chdir(local_graph)
    assert main(["remote", "create", "trading", "--on", hub.url, "--as", "seb", "--owner-secret", "no"]) == 1
    err = capsys.readouterr().err
    assert "owner secret" in err and "Traceback" not in err


def test_push_goes_through_the_gate(hub, shared, capsys):
    commit_node(shared, "hyp-x.md", "---\nid: hyp-x\ntype: hypothesis\nstatus: alive\n---\n\n# x\n")
    assert main(["push"]) == 1
    err = capsys.readouterr().err
    assert "live-claims-must-cite-their-gates" in err
    assert "hyp-x" not in git("log", "--oneline", cwd=hub.registry.repo("trading")).stdout
    git("reset", "-q", "--hard", "HEAD~1", cwd=shared)
    commit_node(shared, "hyp-y.md", "---\nid: hyp-y\ntype: hypothesis\nstatus: open\n---\n\n# y\n")
    assert main(["push"]) == 0
    assert "hyp-y" in git("log", "--oneline", cwd=hub.registry.repo("trading")).stdout


def test_push_refuses_uncommitted_changes_instead_of_pushing_nothing(hub, shared, capsys):
    """`knoten commit` writes a node; git has not seen it."""
    (shared / "nodes" / "hyp-u.md").write_text(
        "---\nid: hyp-u\ntype: hypothesis\nstatus: open\n---\n\n# u\n", encoding="utf-8")
    assert main(["push"]) == 1
    err = capsys.readouterr().err
    assert "not committed" in err and "git commit" in err and err.count("\n") == 1
    assert "hyp-u" not in git("log", "--oneline", cwd=hub.registry.repo("trading")).stdout
    git("add", "-A", cwd=shared)
    git("commit", "-qm", "hyp-u", cwd=shared)
    assert main(["push"]) == 0


def test_pull_puts_your_commit_on_top_of_theirs_and_the_gate_still_accepts_it(
        hub, shared, tmp_path, monkeypatch, capsys):
    """Two people push."""
    code = remote.invite(shared, "maria", "write")
    other, _, _, _ = remote.join(f"{hub.url}/trading", code, dest=str(tmp_path / "maria"))
    commit_node(other, "hyp-m.md", "---\nid: hyp-m\ntype: hypothesis\nstatus: open\n---\n\n# m\n")
    assert git("push", "-q", "origin", "master", cwd=other).returncode == 0
    monkeypatch.chdir(shared)
    commit_node(shared, "hyp-s.md", "---\nid: hyp-s\ntype: hypothesis\nstatus: open\n---\n\n# s\n")
    assert main(["push"]) == 1
    assert "moved on since your last pull" in capsys.readouterr().err
    assert main(["pull"]) == 0
    assert main(["push"]) == 0
    hosted = hub.registry.repo("trading")
    log = git("log", "--oneline", cwd=hosted).stdout
    assert "hyp-m" in log and "hyp-s" in log
    assert git("log", "--merges", "--oneline", cwd=hosted).stdout == ""


def test_remote_add_makes_a_second_machines_clone_sign(hub, shared, tmp_path, monkeypatch,
                                                        capsys):
    code = remote.invite(shared, "maria", "write")
    remote.join(f"{hub.url}/trading", code, dest=str(tmp_path / "maria"))
    second = tmp_path / "maria2"
    # The README's exact recipe: the helper must be wired DURING the clone, because a
    # hosted graph needs a token to be read at all.
    assert git("clone", "-q", "-c", "credential.helper=!knoten credential",
               "-c", "credential.useHttpPath=true", f"{hub.url}/trading.git", str(second),
               cwd=tmp_path).returncode == 0
    git("config", "user.name", "maria", cwd=second)
    git("config", "user.email", "m@x", cwd=second)
    monkeypatch.chdir(second)
    assert main(["remote", "add", f"{hub.url}/trading", "--as", "maria"]) == 0
    assert "signs as maria" in capsys.readouterr().out
    commit_node(second, "hyp-2.md", "---\nid: hyp-2\ntype: hypothesis\nstatus: open\n---\n\n# 2\n")
    assert main(["push"]) == 0
    assert "hyp-2" in git("log", "--oneline", cwd=hub.registry.repo("trading")).stdout


def test_remote_add_leaves_a_reader_unsigned_and_refuses_a_listed_name_without_its_key(
        hub, shared, tmp_path, monkeypatch, capsys, keys_dir):
    code = remote.invite(shared, "ravi", "read")
    remote.join(f"{hub.url}/trading", code, dest=str(tmp_path / "ravi"))
    plain = tmp_path / "plain"
    assert git("clone", "-q", str(hub.registry.repo("trading")), str(plain), cwd=tmp_path).returncode == 0
    monkeypatch.chdir(plain)
    assert main(["remote", "add", f"{hub.url}/trading", "--as", "ravi"]) == 0
    assert "signs as" not in capsys.readouterr().out
    assert git("config", "user.signingkey", cwd=plain).stdout.strip() == ""
    (keys_dir / "seb").rename(keys_dir / "seb.gone")     # the listed admin, key not here
    assert main(["remote", "add", f"{hub.url}/trading", "--as", "seb"]) == 1
    assert "different key for 'seb'" in capsys.readouterr().err


# ---------------------------------------------------------------- the friend's journey

def test_the_whole_journey(hub, shared, tmp_path, monkeypatch, capsys):
    """You create a remote and invite Maria."""
    assert main(["invite", "maria", "--role", "write"]) == 0
    code = capsys.readouterr().out.strip().split()[-1]
    monkeypatch.chdir(tmp_path)
    assert main(["join", f"{hub.url}/trading", "--invite", code]) == 0
    clone = tmp_path / "trading"
    assert (clone / "nodes" / "hyp-ok.md").exists()
    assert "maria" in capsys.readouterr().out
    git("config", "user.email", "m@m.m", cwd=clone); git("config", "user.name", "maria", cwd=clone)
    commit_node(clone, "hyp-m.md", "---\nid: hyp-m\ntype: hypothesis\nstatus: open\n---\n\n# m\n")
    monkeypatch.chdir(clone)
    assert main(["push"]) == 0
    monkeypatch.chdir(shared)
    assert main(["pull"]) == 0
    assert (shared / "nodes" / "hyp-m.md").exists()
    commit_node(clone, "hyp-bad.md", "---\nid: hyp-bad\ntype: hypothesis\nstatus: alive\n---\n\n# b\n")
    monkeypatch.chdir(clone)
    assert main(["push"]) == 1
    assert "live-claims-must-cite-their-gates" in capsys.readouterr().err


def test_join_with_a_read_invite_can_pull_but_not_push(hub, shared, tmp_path, monkeypatch,
                                                       capsys, keys_dir):
    """A reader is not listed."""
    main(["invite", "reader", "--role", "read"])
    code = capsys.readouterr().out.strip().split()[-1]
    before = (shared / "contributors.yaml").read_text(encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert main(["join", f"{hub.url}/trading", "--invite", code, "--dest", "r"]) == 0
    out = capsys.readouterr().out
    assert "can pull" in out
    clone = tmp_path / "r"
    assert (clone / "nodes" / "hyp-ok.md").exists()
    assert (clone / "contributors.yaml").read_text(encoding="utf-8") == before
    assert "reader" not in before
    assert not (keys_dir / "reader").exists(), "a reader was given a signing key"
    git("config", "user.email", "r@r.r", cwd=clone); git("config", "user.name", "r", cwd=clone)
    monkeypatch.chdir(clone)
    assert main(["pull"]) == 0
    commit_node(clone, "hyp-r.md", "---\nid: hyp-r\ntype: hypothesis\nstatus: open\n---\n\n# r\n")
    assert main(["push"]) == 1
    assert "read access, not write" in capsys.readouterr().err


def test_revoke_locks_a_contributor_out_on_their_next_push(hub, shared, tmp_path, monkeypatch, capsys):
    main(["invite", "maria"])
    code = capsys.readouterr().out.strip().split()[-1]
    admin = cred_lookup(f"{hub.url}/trading.git")  # admin's own token, before maria's join overwrites it
    monkeypatch.chdir(tmp_path)
    main(["join", f"{hub.url}/trading", "--invite", code])
    clone = tmp_path / "trading"
    git("config", "user.email", "m@m.m", cwd=clone); git("config", "user.name", "maria", cwd=clone)
    maria = cred_lookup(f"{hub.url}/trading.git")
    # The credential store is one machine's, keyed by remote URL: admin and maria are on
    # separate machines in reality, each with their own store for this same URL. Restore
    # each in turn to simulate that, since the test runs both in one shared file.
    cred_store(f"{hub.url}/trading.git", *admin)
    monkeypatch.chdir(shared)
    assert main(["revoke", "maria"]) == 0
    cred_store(f"{hub.url}/trading.git", *maria)
    commit_node(clone, "hyp-m.md", "---\nid: hyp-m\ntype: hypothesis\nstatus: open\n---\n\n# m\n")
    monkeypatch.chdir(clone)
    assert main(["push"]) == 1
    assert "credentials refused" in capsys.readouterr().err


def test_a_clone_failure_after_redemption_says_the_invite_is_spent(hub, shared, tmp_path, monkeypatch, capsys):
    """The server consumes the code before git clone runs."""
    main(["invite", "maria"])
    code = capsys.readouterr().out.strip().split()[-1]
    blocker = tmp_path / "taken"
    (blocker / "not-empty").mkdir(parents=True)          # git refuses a non-empty dest
    monkeypatch.chdir(tmp_path)
    assert main(["join", f"{hub.url}/trading", "--invite", code, "--dest", "taken"]) == 1
    err = capsys.readouterr().err
    assert "invite is spent" in err and "credentials are saved" in err
    assert "Traceback" not in err
    assert cred_lookup(f"{hub.url}/trading.git")[0] == "maria"


# ---------------------------------------------------------------- a server is not trusted

# ---------------------------------------------------------------- one host, many graphs

def test_a_push_that_fails_after_creation_says_the_graph_already_exists(hub, local_graph,
                                                                        monkeypatch, capsys):
    """The graph is created two calls before the push."""
    monkeypatch.chdir(local_graph)
    (local_graph / "nodes" / "hyp-x.md").write_text(
        "---\nid: hyp-x\ntype: hypothesis\nstatus: alive\n---\n\n# x\n", encoding="utf-8")
    git("add", "-A", cwd=local_graph)
    git("commit", "-qm", "broken", cwd=local_graph)
    assert main(["remote", "create", "trading", "--on", hub.url, "--as", "seb",
                 "--owner-secret", hub.secret]) == 1
    err = capsys.readouterr().err
    assert "EXISTS" in err and "knoten remote add" in err
    assert hub.registry.exists("trading"), "the message would be a lie"


# ---------------------------------------------------------------- the invite list

# ---------------------------------------------------------------- signed identity

def test_remote_create_refuses_when_you_are_not_in_an_existing_contributors_file(hub, local_graph, monkeypatch, keys_dir, capsys):
    other = make_key(keys_dir, "other")
    C.dump(local_graph, {"other": {"key": pub_line(other), "role": "admin"}})
    git("add", "-A", cwd=local_graph); git("commit", "-qm", "someone else's constitution", cwd=local_graph)
    monkeypatch.chdir(local_graph)
    assert main(["remote", "create", "trading", "--on", hub.url, "--as", "seb",
                 "--owner-secret", hub.secret]) == 1
    assert "not listed" in capsys.readouterr().err
    assert not hub.registry.exists("trading")          # refused before the server was asked
    assert not (key_dir() / "seb").exists()            # refused before a key was even made


# ---------------------------------------------------------------- invite signs, join adds itself

def test_the_whole_journey_signed(hub, shared_signed, tmp_path, monkeypatch, capsys):
    """Seb invites (signed by his key)."""
    assert main(["invite", "maria", "--role", "write"]) == 0
    code = capsys.readouterr().out.strip().split()[-1]
    monkeypatch.chdir(tmp_path)
    assert main(["join", f"{hub.url}/trading", "--invite", code]) == 0
    assert "signing key was made" in capsys.readouterr().out
    clone = tmp_path / "trading"
    c = C.load(clone)
    assert c["maria"]["role"] == "write" and c["maria"]["invited_by"] == "seb"
    assert c["maria"]["key"] == public_line(key_dir() / "maria")
    assert "joins as write" in git("log", "-1", "--format=%s", cwd=hub.registry.repo("trading")).stdout
    git("config", "user.email", "m@m.m", cwd=clone); git("config", "user.name", "maria", cwd=clone)
    commit_node(clone, "hyp-m.md", "---\nid: hyp-m\ntype: hypothesis\nstatus: open\n---\n\n# m\n")
    monkeypatch.chdir(clone)
    assert main(["push"]) == 0
    monkeypatch.chdir(shared_signed)
    assert main(["pull"]) == 0
    assert (shared_signed / "nodes" / "hyp-m.md").exists()


def test_invite_refuses_when_your_key_is_not_the_one_the_graph_lists(hub, shared_signed, keys_dir, capsys, monkeypatch):
    (key_dir() / "seb").unlink(); (key_dir() / "seb.pub").unlink()
    monkeypatch.chdir(shared_signed)
    assert main(["invite", "maria"]) == 1
    assert "different key" in capsys.readouterr().err
    assert not (key_dir() / "seb").exists()
    assert not (key_dir() / "seb.pub").exists()


def test_a_joiner_whose_push_is_refused_is_told_why(hub, shared_signed, tmp_path, monkeypatch, capsys):
    """The admin renames the graph between the invite and the join."""
    assert main(["invite", "maria"]) == 0
    code = capsys.readouterr().out.strip().split()[-1]
    text = (shared_signed / "graph.yaml").read_text(encoding="utf-8")
    (shared_signed / "graph.yaml").write_text(text.replace("name: test", "name: renamed"),
                                              encoding="utf-8")
    git("add", "-A", cwd=shared_signed); git("commit", "-qm", "rename the graph", cwd=shared_signed)
    assert main(["push"]) == 0
    monkeypatch.chdir(tmp_path)
    assert main(["join", f"{hub.url}/trading", "--invite", code]) == 1
    err = capsys.readouterr().err
    assert "different name, role or graph" in err
    assert "clone and credentials are in place" in err
    assert "Traceback" not in err


@pytest.fixture
def nested_local_graph(tmp_path, rules_yaml):
    repo = tmp_path / "admin" / "repo"
    root = repo / "g"
    (root / "nodes").mkdir(parents=True)
    (root / "graph.yaml").write_text(rules_yaml, encoding="utf-8")
    (root / "nodes" / "hyp-ok.md").write_text(
        "---\nid: hyp-ok\ntype: hypothesis\nstatus: open\n---\n\n# a claim\n", encoding="utf-8")
    for cmd in (["init", "-q", "-b", "master"], ["config", "user.email", "t@t.t"],
                ["config", "user.name", "t"], ["add", "-A"], ["commit", "-qm", "seed"]):
        assert git(*cmd, cwd=repo).returncode == 0, cmd
    return root


def test_join_finds_the_graph_in_a_monorepo_subdirectory(hub, nested_local_graph, tmp_path,
                                                          monkeypatch, capsys):
    """The hosted graph lives at `g/`, not the clone's root."""
    monkeypatch.chdir(nested_local_graph)
    assert main(["remote", "create", "trading", "--on", hub.url, "--as", "seb",
                 "--owner-secret", hub.secret]) == 0
    assert main(["invite", "maria", "--role", "write"]) == 0
    code = capsys.readouterr().out.strip().split()[-1]
    monkeypatch.chdir(tmp_path)
    assert main(["join", f"{hub.url}/trading", "--invite", code]) == 0
    clone = tmp_path / "trading"
    c = C.load(clone / "g")
    assert c is not None and c["maria"]["role"] == "write" and c["maria"]["invited_by"] == "seb"
    hosted = hub.registry.repo("trading")
    assert "joins as write" in git("log", "-1", "--format=%s", cwd=hosted).stdout
    files = git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD", cwd=hosted).stdout
    assert files.strip() == "g/contributors.yaml"


def test_revoke_is_recorded_in_the_graph_before_the_token_dies(hub, shared_signed, tmp_path, monkeypatch, capsys):
    assert main(["invite", "maria"]) == 0
    code = capsys.readouterr().out.strip().split()[-1]
    admin = cred_lookup(f"{hub.url}/trading.git")  # admin's own token, before maria's join overwrites it
    monkeypatch.chdir(tmp_path)
    assert main(["join", f"{hub.url}/trading", "--invite", code]) == 0
    clone = tmp_path / "trading"
    git("config", "user.email", "m@m.m", cwd=clone); git("config", "user.name", "maria", cwd=clone)
    maria = cred_lookup(f"{hub.url}/trading.git")
    # The credential store is one machine's, keyed by remote URL: admin and maria are on
    # separate machines in reality, each with their own store for this same URL. Restore
    # each in turn to simulate that, since the test runs both in one shared file.
    cred_store(f"{hub.url}/trading.git", *admin)
    monkeypatch.chdir(shared_signed)
    assert main(["revoke", "maria"]) == 0
    hosted = hub.registry.repo("trading")
    assert git("log", "-1", "--format=%s", cwd=hosted).stdout.strip() == "seb revokes maria"
    contribs, _ = hub.registry.head_graph("trading")
    assert contribs["maria"]["revoked"] == today()
    assert hub.registry.authenticate("trading", *maria) is None
    cred_store(f"{hub.url}/trading.git", *maria)
    commit_node(clone, "hyp-m.md", "---\nid: hyp-m\ntype: hypothesis\nstatus: open\n---\n\n# m\n")
    monkeypatch.chdir(clone)
    assert main(["push"]) == 1
    assert "credentials refused" in capsys.readouterr().err


def test_only_an_admin_can_revoke(hub, shared_signed, tmp_path, monkeypatch, capsys):
    assert main(["invite", "maria"]) == 0
    code = capsys.readouterr().out.strip().split()[-1]
    monkeypatch.chdir(tmp_path)
    assert main(["join", f"{hub.url}/trading", "--invite", code]) == 0
    clone = tmp_path / "trading"
    git("config", "user.email", "m@m.m", cwd=clone); git("config", "user.name", "maria", cwd=clone)
    monkeypatch.chdir(clone)
    assert main(["revoke", "seb"]) == 1
    assert "only an admin can revoke" in capsys.readouterr().err
    hosted = hub.registry.repo("trading")
    assert "revokes" not in git("log", "-1", "--format=%s", cwd=hosted).stdout


def test_a_failed_server_revoke_after_a_successful_push_is_not_swallowed(hub, shared_signed, tmp_path,
                                                                         monkeypatch, capsys):
    assert main(["invite", "maria"]) == 0
    code = capsys.readouterr().out.strip().split()[-1]
    admin = cred_lookup(f"{hub.url}/trading.git")  # admin's own token, before maria's join overwrites it
    monkeypatch.chdir(tmp_path)
    assert main(["join", f"{hub.url}/trading", "--invite", code]) == 0
    cred_store(f"{hub.url}/trading.git", *admin)
    monkeypatch.chdir(shared_signed)
    real_api = remote._api
    def flaky(url, body, auth=None):
        if url.endswith("/revoke"):
            raise GraphError("server exploded")
        return real_api(url, body, auth)
    monkeypatch.setattr(remote, "_api", flaky)
    assert main(["revoke", "maria"]) == 1
    err = capsys.readouterr().err
    assert "can still connect with a live token" in err
    assert "knoten revoke maria" in err
    # The graph mark itself is unaffected by the API call's failure: it already landed,
    # signed and pushed, before `/revoke` was ever called.
    assert C.load(shared_signed)["maria"]["revoked"] == today()


def test_revoke_refuses_when_this_clone_cannot_sign(hub, shared_signed, capsys, monkeypatch):
    git("config", "--unset", "user.signingkey", cwd=shared_signed)
    monkeypatch.chdir(shared_signed)
    assert main(["revoke", "ghost"]) == 1
    err = capsys.readouterr().err
    assert "no signing key configured" in err
    assert "knoten key seb" in err


# ---------------------------------------------------------------- the first walk through sharing
# Each of these was found by sharing a real graph the way the README says to.

def test_remote_create_refuses_to_replace_an_origin_that_is_not_the_server(hub, local_graph, monkeypatch, capsys):
    """A graph inside a project repo: `remote create` would have swapped the project's
    GitHub origin for the graph server and pushed the whole project through the gate."""
    git("remote", "add", "origin", "git@github.com:someone/project.git", cwd=local_graph)
    monkeypatch.chdir(local_graph)
    assert main(["remote", "create", "trading", "--on", hub.url, "--as", "seb",
                 "--owner-secret", hub.secret]) == 1
    err = capsys.readouterr().err
    assert "git@github.com:someone/project.git" in err and "its own repository" in err
    assert git("remote", "get-url", "origin", cwd=local_graph).stdout.strip() == "git@github.com:someone/project.git"
    assert git("log", "-1", "--format=%s", cwd=local_graph).stdout.strip() == "seed"   # nothing bootstrapped
    assert not (local_graph / C.FILE).exists()


def test_join_points_at_the_graph_not_the_clone_in_a_monorepo(hub, nested_local_graph, tmp_path, monkeypatch, capsys):
    """`cd trading && knoten frontier` found no graph.yaml; the graph is at trading/g."""
    monkeypatch.chdir(nested_local_graph)
    assert main(["remote", "create", "trading", "--on", hub.url, "--as", "seb",
                 "--owner-secret", hub.secret]) == 0
    assert main(["invite", "maria", "--role", "write"]) == 0
    code = capsys.readouterr().out.strip().split()[-1]
    monkeypatch.chdir(tmp_path)
    assert main(["join", f"{hub.url}/trading", "--invite", code]) == 0
    assert "cd trading/g && knoten frontier" in capsys.readouterr().out


def test_serve_shows_the_secret_even_when_its_output_is_a_file(tmp_path):
    """`nohup knoten serve --data d > serve.log &` is how it runs on a server. With stdout
    block-buffered the secret and the address reached the log only at shutdown."""
    import subprocess, sys, time
    log = tmp_path / "serve.log"
    with log.open("w") as out:
        p = subprocess.Popen([sys.executable, "-c", "import sys; from knoten.cli import main; sys.exit(main(sys.argv[1:]))",
                              "serve", "--data", str(tmp_path / "d"), "--bind", "127.0.0.1:0"],
                             stdout=out, stderr=subprocess.STDOUT)
        try:
            for _ in range(50):
                time.sleep(0.1)
                if "serving" in log.read_text(encoding="utf-8"):
                    break
        finally:
            p.terminate(); p.wait(timeout=10)
    text = log.read_text(encoding="utf-8")
    secret = (tmp_path / "d" / "owner").read_text(encoding="utf-8").strip()
    assert secret in text and "serving" in text, text

"""The client side of a remote graph: a credential store git calls through its own
credential-helper protocol, and commands that wrap git plus four JSON calls."""
import json
import os
import stat

import pytest
from conftest import commit_node, git, make_key, pub_line

from knoten import remote
from knoten import contributors as C
from knoten.cli import main
from knoten.core import GraphError, today
from knoten.keys import key_dir, public_line
from knoten.remote import _explain, cred_lookup, cred_path, cred_store, credential_helper


@pytest.fixture(autouse=True)
def isolated_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOTEN_CREDENTIALS", str(tmp_path / "creds"))


# ---------------------------------------------------------------- the store

def test_store_and_lookup_round_trip_per_remote():
    """The key carries the scheme: `http://h/x.git` is a different remote from
    `https://h/x.git`, and a token scoped to the second must not answer for the first."""
    cred_store("https://h.example/trading.git", "seb", "tok-1")
    cred_store("https://h.example/biology.git", "seb", "tok-2")

    assert cred_lookup("https://h.example/trading.git") == ("seb", "tok-1")
    assert cred_lookup("https://h.example/biology.git") == ("seb", "tok-2")
    assert cred_lookup("https://h.example/nope.git") is None
    assert cred_lookup("http://h.example/trading.git") is None


def test_storing_the_same_remote_again_replaces_it():
    cred_store("https://h.example/trading.git", "seb", "old")
    cred_store("https://h.example/trading.git", "seb", "new")

    assert cred_lookup("https://h.example/trading.git") == ("seb", "new")
    assert cred_path().read_text().count("trading.git") == 1


def test_the_store_is_private_to_the_user():
    cred_store("https://h.example/trading.git", "seb", "tok")

    assert stat.S_IMODE(cred_path().stat().st_mode) == 0o600


def test_the_store_is_made_private_again_if_it_was_not():
    """os.open's mode applies only when the file is created. A credentials file that
    already existed with looser bits (a manual copy, a bad umask) kept them on every
    later write, leaking every token to other local users."""
    p = cred_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("h.example/old.git seb tok\n", encoding="utf-8")
    os.chmod(p, 0o644)

    cred_store("https://h.example/trading.git", "seb", "tok")

    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_a_schemeless_key_is_never_matched_by_stripping_the_scheme():
    """A fallback to the old `<netloc><path>` key leaked the owner secret. Without a
    scheme, `owner://h:8899` and `https://h:8899` collapse to the same string, so a plain
    `git fetch` against the bare host matched the owner line and got the key to every
    graph on the server. The lookup is exact, and nothing rewrites keys."""
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


def test_lookup_with_no_store_is_none_not_an_error():
    assert cred_lookup("https://h.example/trading.git") is None


# ---------------------------------------------------------------- git's protocol

def test_the_helper_answers_gits_request_for_a_known_remote():
    """git writes key=value lines and reads username/password back. `path` is only
    sent when credential.useHttpPath is on, which the client sets, so one host can
    hold many graphs with different tokens."""
    cred_store("https://h.example/trading.git", "maria", "tok")

    out = credential_helper("protocol=https\nhost=h.example\npath=trading.git\n")

    assert out == "username=maria\npassword=tok\n"


def test_the_helper_will_not_hand_an_https_token_to_plain_http():
    """The stored key used to be host plus path with no scheme, so the token a user
    holds for an https server answered git's request for the same host over plain http:
    a downgrade, and the token crossed the wire in the clear."""
    cred_store("https://h.example/trading.git", "maria", "tok")

    assert credential_helper("protocol=http\nhost=h.example\npath=trading.git\n") == ""


def test_the_helper_never_hands_out_the_owner_secret():
    """The owner secret opens every graph on a server. It lives under `owner://<host>`,
    a scheme git never asks about, so no git request -- including the bare-host one git
    sends when useHttpPath is off -- can be answered with it."""
    cred_store("owner://h.example", "owner", "the-owner-secret")

    assert credential_helper("protocol=https\nhost=h.example\n") == ""
    assert credential_helper("protocol=https\nhost=h.example\npath=\n") == ""
    assert credential_helper("protocol=owner\nhost=h.example\n") == ""


def test_the_helper_says_nothing_for_an_unknown_remote():
    """Empty output tells git to fall through to its next helper or to prompt. Anything
    else, including an error, would break every non-knoten remote on the machine."""
    assert credential_helper("protocol=https\nhost=h.example\npath=other.git\n") == ""


def test_the_cli_exposes_the_helper_on_stdin(monkeypatch, capsys):
    import io
    cred_store("https://h.example/trading.git", "maria", "tok")
    monkeypatch.setattr("sys.stdin", io.StringIO("protocol=https\nhost=h.example\npath=trading.git\n"))

    assert main(["credential", "get"]) == 0
    assert capsys.readouterr().out == "username=maria\npassword=tok\n"


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


def test_remote_create_puts_the_graph_on_the_server_and_wires_the_clone(capsys, hub, shared):
    """One command: the graph exists on the server, the admin's token is stored, origin
    points at it, and the seed commit is already there."""
    assert hub.registry.exists("trading")
    assert "seed" in git("log", "--oneline", cwd=hub.registry.repo("trading")).stdout
    assert cred_lookup(f"{hub.url}/trading.git")[0] == "seb"
    assert cred_lookup(f"owner://{hub.url.removeprefix('http://')}") == ("owner", hub.secret)
    assert git("remote", "get-url", "origin", cwd=shared).stdout.strip() == f"{hub.url}/trading.git"
    assert git("config", "credential.helper", cwd=shared).stdout.strip() == "!knoten credential"
    assert f"{hub.url}/trading" in capsys.readouterr().out


def test_remote_create_remembers_the_owner_secret_for_the_next_graph(hub, local_graph, tmp_path, monkeypatch):
    monkeypatch.chdir(local_graph)
    main(["remote", "create", "trading", "--on", hub.url, "--as", "seb", "--owner-secret", hub.secret])

    second = tmp_path / "biology"
    (second / "nodes").mkdir(parents=True)
    (second / "graph.yaml").write_text("name: biology\n", encoding="utf-8")
    for c in (["init", "-q", "-b", "master"], ["config", "user.email", "t@t.t"],
              ["config", "user.name", "t"], ["add", "-A"], ["commit", "-qm", "seed"]):
        git(*c, cwd=second)
    monkeypatch.chdir(second)

    assert main(["remote", "create", "biology", "--on", hub.url, "--as", "seb"]) == 0
    assert hub.registry.exists("biology")


def test_remote_create_outside_a_git_repo_says_git_init(hub, tmp_path, monkeypatch, capsys):
    g = tmp_path / "g"
    (g / "nodes").mkdir(parents=True)
    (g / "graph.yaml").write_text("name: g\n", encoding="utf-8")
    monkeypatch.chdir(g)

    assert main(["remote", "create", "g", "--on", hub.url, "--as", "seb", "--owner-secret", hub.secret]) == 1
    assert "git init" in capsys.readouterr().err


def test_remote_create_with_the_wrong_owner_secret_is_one_line(hub, local_graph, monkeypatch, capsys):
    monkeypatch.chdir(local_graph)

    assert main(["remote", "create", "trading", "--on", hub.url, "--as", "seb", "--owner-secret", "no"]) == 1
    err = capsys.readouterr().err
    assert "owner secret" in err and "Traceback" not in err


def test_remote_create_with_no_secret_and_no_terminal_is_one_line(hub, local_graph, monkeypatch, capsys):
    """getpass raises EOFError when stdin is not a terminal. From a script or CI that
    was a traceback instead of the one line every other refusal gives. The prompt itself
    is stubbed rather than pointing stdin at a StringIO, which makes getpass warn about
    not being able to control echo on the terminal."""
    def no_terminal(*_a, **_k):
        raise EOFError
    monkeypatch.chdir(local_graph)
    monkeypatch.setattr("knoten.remote.getpass.getpass", no_terminal)

    assert main(["remote", "create", "trading", "--on", hub.url, "--as", "seb"]) == 1
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


def test_pull_brings_a_collaborators_node_down(hub, shared, tmp_path, monkeypatch):
    """A minted token alone no longer earns a push on a signed graph: the graph is
    signed, so a collaborator has to be listed and sign, which is what `join` is for."""
    code = remote.invite(shared, "maria", "write")
    other, _, _ = remote.join(f"{hub.url}/trading", code, dest=str(tmp_path / "maria"))
    commit_node(other, "hyp-m.md", "---\nid: hyp-m\ntype: hypothesis\nstatus: open\n---\n\n# m\n")
    assert git("push", "-q", "origin", "master", cwd=other).returncode == 0

    monkeypatch.chdir(shared)
    assert main(["pull"]) == 0
    assert (shared / "nodes" / "hyp-m.md").exists()


def test_push_with_a_read_token_is_explained_not_dumped(hub, shared, tmp_path, monkeypatch, capsys):
    """git says `The requested URL returned error: 403`. The user should read `read
    access, not write`."""
    tok = hub.registry.mint("trading", "reader", "read")
    cred_store(f"{hub.url}/trading.git", "reader", tok)
    commit_node(shared, "hyp-z.md", "---\nid: hyp-z\ntype: hypothesis\nstatus: open\n---\n\n# z\n")

    assert main(["push"]) == 1
    assert "read access, not write" in capsys.readouterr().err


def test_a_rule_message_containing_401_is_not_mistaken_for_a_credential_problem(hub, shared, capsys):
    """The gate's remote: lines share stderr with git's own. A node id with 401 in it
    used to make the summary say "credentials refused" for a plain rule violation."""
    commit_node(shared, "hyp-401-alive.md",
                "---\nid: hyp-401-alive\ntype: hypothesis\nstatus: alive\n---\n\n# x\n")

    assert main(["push"]) == 1
    err = capsys.readouterr().err
    assert "live-claims-must-cite-their-gates" in err
    assert "credentials refused" not in err
    assert "refused the push" in err


def test_explain_is_not_fooled_by_digits_in_gits_own_url_line():
    """`_explain` used to bare-substring-search for "401"/"403", which also matched
    git's own `fatal: unable to access '...'` line whenever the port or graph name in
    the URL happened to contain those three digits. A port of 34012 turned a real 403
    into "credentials refused"; a port of 35403 turned a real 401 into "read access,
    not write". Neither port carries the phrase git actually uses for the error."""
    read_only = (
        "fatal: unable to access 'http://127.0.0.1:34012/trading.git/': "
        "The requested URL returned error: 403"
    )
    assert "read access, not write" in _explain(read_only)

    bad_token = (
        "fatal: unable to access 'http://127.0.0.1:35403/trading.git/': "
        "The requested URL returned error: 401"
    )
    assert "credentials refused" in _explain(bad_token)


def test_push_without_a_remote_says_so(local_graph, monkeypatch, capsys):
    monkeypatch.chdir(local_graph)
    assert main(["push"]) == 1
    assert "remote create" in capsys.readouterr().err


def test_remote_add_points_an_existing_clone_at_a_remote(hub, local_graph, monkeypatch):
    monkeypatch.chdir(local_graph)
    assert main(["remote", "add", f"{hub.url}/trading"]) == 0

    assert git("remote", "get-url", "origin", cwd=local_graph).stdout.strip() == f"{hub.url}/trading.git"
    assert git("config", "credential.useHttpPath", cwd=local_graph).stdout.strip() == "true"


# ---------------------------------------------------------------- the friend's journey

def test_the_whole_journey(hub, shared, tmp_path, monkeypatch, capsys):
    """You create a remote and invite Maria. Maria joins, adds a node, pushes. You pull
    and it is there. She pushes a broken one and it is not. Every step through the CLI."""
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


def test_join_with_a_read_invite_can_pull_but_not_push(hub, shared, tmp_path, monkeypatch, capsys):
    main(["invite", "reader", "--role", "read"])
    code = capsys.readouterr().out.strip().split()[-1]
    monkeypatch.chdir(tmp_path)
    main(["join", f"{hub.url}/trading", "--invite", code, "--dest", "r"])
    clone = tmp_path / "r"
    git("config", "user.email", "r@r.r", cwd=clone); git("config", "user.name", "r", cwd=clone)
    commit_node(clone, "hyp-r.md", "---\nid: hyp-r\ntype: hypothesis\nstatus: open\n---\n\n# r\n")
    monkeypatch.chdir(clone)

    assert main(["push"]) == 1
    assert "read access, not write" in capsys.readouterr().err
    assert main(["pull"]) == 0


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


def test_only_an_admin_can_invite(hub, shared, tmp_path, monkeypatch, capsys):
    main(["invite", "maria"])
    code = capsys.readouterr().out.strip().split()[-1]
    monkeypatch.chdir(tmp_path)
    main(["join", f"{hub.url}/trading", "--invite", code])
    monkeypatch.chdir(tmp_path / "trading")

    assert main(["invite", "friend-of-maria"]) == 1
    assert "only an admin" in capsys.readouterr().err


def test_a_spent_or_wrong_code_is_one_readable_line(hub, shared, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    assert main(["join", f"{hub.url}/trading", "--invite", "nope"]) == 1
    err = capsys.readouterr().err
    assert "not valid" in err and "Traceback" not in err
    assert not (tmp_path / "trading").exists()


def test_a_clone_failure_after_redemption_says_the_invite_is_spent(hub, shared, tmp_path, monkeypatch, capsys):
    """The server consumes the code before git clone runs. When the clone then failed,
    the user saw only git's error, retried the same code, and was refused for a reason
    that looked unrelated. Now the message says the credentials are saved and how to
    finish by hand."""
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

def test_a_hostile_join_reply_is_refused_and_writes_nothing(hub, tmp_path, monkeypatch):
    """The credentials file is one line per remote, so a `name` carrying a newline
    appends a second line to it: a credential for a host the user never named. The
    server picks that field, so the client checks it before it reaches disk. `_api` is
    stubbed for this one test because no honest server will send this."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(remote, "_api", lambda *a, **k: {
        "name": "x\nevil.example/other.git attacker stolen-token",
        "role": "admin", "token": "t"})

    with pytest.raises(GraphError, match="malformed"):
        remote.join(f"{hub.url}/trading", "code")

    assert not cred_path().exists(), "a refused reply still wrote credentials"


@pytest.mark.parametrize("reply", [
    pytest.param({"role": "write", "token": "t"}, id="no-name"),
    pytest.param({"name": "maria", "role": "owner", "token": "t"}, id="role-off-the-list"),
    pytest.param({"name": "maria", "role": "write", "token": ""}, id="empty-token"),
    pytest.param({"name": "maria", "role": "write", "token": "a b"}, id="token-with-a-space"),
])
def test_every_field_of_a_join_reply_is_checked(hub, tmp_path, monkeypatch, reply):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(remote, "_api", lambda *a, **k: reply)

    with pytest.raises(GraphError, match="malformed"):
        remote.join(f"{hub.url}/trading", "code")


# ---------------------------------------------------------------- one host, many graphs

def test_two_graphs_on_one_host_each_push_with_their_own_token(hub, local_graph, tmp_path,
                                                               monkeypatch):
    """One credentials file, one host, two graphs, two people. Without
    credential.useHttpPath git asks only about the host, and whichever token was stored
    last answers for both graphs -- so one of the two pushes with the other's identity,
    or is refused for no visible reason."""
    monkeypatch.chdir(local_graph)
    assert main(["remote", "create", "trading", "--on", hub.url, "--as", "seb",
                 "--owner-secret", hub.secret]) == 0

    second = tmp_path / "biology"
    (second / "nodes").mkdir(parents=True)
    (second / "graph.yaml").write_text("name: biology\n", encoding="utf-8")
    for c in (["init", "-q", "-b", "master"], ["config", "user.email", "m@m.m"],
              ["config", "user.name", "maria"], ["add", "-A"], ["commit", "-qm", "seed"]):
        git(*c, cwd=second)
    monkeypatch.chdir(second)
    assert main(["remote", "create", "biology", "--on", hub.url, "--as", "maria"]) == 0

    commit_node(second, "hyp-b.md", "---\nid: hyp-b\ntype: hypothesis\nstatus: open\n---\n\n# b\n")
    assert main(["push"]) == 0
    monkeypatch.chdir(local_graph)
    commit_node(local_graph, "hyp-t.md",
                "---\nid: hyp-t\ntype: hypothesis\nstatus: open\n---\n\n# t\n")
    assert main(["push"]) == 0

    assert cred_lookup(f"{hub.url}/trading.git")[0] == "seb"
    assert cred_lookup(f"{hub.url}/biology.git")[0] == "maria"
    assert "hyp-t" in git("log", "--oneline", cwd=hub.registry.repo("trading")).stdout
    assert "hyp-b" in git("log", "--oneline", cwd=hub.registry.repo("biology")).stdout


def test_the_owner_secret_can_come_from_the_environment(hub, local_graph, monkeypatch):
    """`--owner-secret` puts the secret in `ps` output for every other user on the
    machine. The environment is the way to pass it without a terminal."""
    def never(*_a, **_k):
        raise AssertionError("prompted despite KNOTEN_OWNER_SECRET being set")
    monkeypatch.chdir(local_graph)
    monkeypatch.setenv("KNOTEN_OWNER_SECRET", hub.secret)
    monkeypatch.setattr("knoten.remote.getpass.getpass", never)

    assert main(["remote", "create", "trading", "--on", hub.url, "--as", "seb"]) == 0
    assert hub.registry.exists("trading")


def test_a_push_that_fails_after_creation_says_the_graph_already_exists(hub, local_graph,
                                                                        monkeypatch, capsys):
    """The graph is created two calls before the push. When the push then failed the user
    saw git's error alone, read it as "nothing happened", re-ran the command and met
    "already exists" with no idea which half had worked."""
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

def test_invites_lists_who_has_not_arrived_yet(hub, shared, capsys):
    """An invite is a bearer secret sitting on the server until it is used. An admin who
    cannot list them cannot tell a forgotten one from a revoked one."""
    assert main(["invite", "maria", "--role", "read", "--expires", "3"]) == 0
    code = capsys.readouterr().out.strip().split()[-1]

    assert main(["invites"]) == 0
    out = capsys.readouterr().out
    assert "maria" in out and "read" in out
    assert code not in out, "the list handed the invite code back out"

    assert main(["invites", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)["invites"]
    assert listed[0]["name"] == "maria" and listed[0]["by"] == "seb"


def test_invites_is_empty_once_they_are_all_redeemed(hub, shared, capsys):
    """Redeemed straight through the registry, not `knoten join`: joining rewrites this
    machine's one credentials file for this URL, and the admin needs their own token back
    to ask the question."""
    main(["invite", "maria"])
    code = capsys.readouterr().out.strip().split()[-1]
    hub.registry.redeem("trading", code)

    assert main(["invites"]) == 0
    assert "no open invites" in capsys.readouterr().out


def test_only_an_admin_can_list_the_invites(hub, shared, tmp_path, monkeypatch, capsys):
    main(["invite", "maria"])
    code = capsys.readouterr().out.strip().split()[-1]
    monkeypatch.chdir(tmp_path)
    main(["join", f"{hub.url}/trading", "--invite", code])
    monkeypatch.chdir(tmp_path / "trading")
    capsys.readouterr()

    assert main(["invites"]) == 1
    assert "only an admin" in capsys.readouterr().err


# ---------------------------------------------------------------- signed identity

def test_remote_create_writes_the_admin_into_contributors_and_signs(hub, local_graph, monkeypatch):
    """The first push is the bootstrap: contributors.yaml listing the creator as admin,
    in a commit signed by the creator, which the gate accepts on that basis alone."""
    monkeypatch.chdir(local_graph)
    assert main(["remote", "create", "trading", "--on", hub.url, "--as", "seb",
                 "--owner-secret", hub.secret]) == 0

    c = C.load(local_graph)
    assert c == {"seb": {"key": public_line(key_dir() / "seb"), "role": "admin"}}
    assert git("config", "commit.gpgsign", cwd=local_graph).stdout.strip() == "true"
    contribs, name = hub.registry.head_graph("trading")
    assert contribs == c


def test_after_create_a_plain_push_is_signed_and_lands(hub, shared_signed, capsys):
    """Nobody has to remember -S. The clone is configured to sign, and the gate checks."""
    commit_node(shared_signed, "hyp-y.md", "---\nid: hyp-y\ntype: hypothesis\nstatus: open\n---\n\n# y\n")
    assert main(["push"]) == 0
    assert "hyp-y" in git("log", "--oneline", cwd=hub.registry.repo("trading")).stdout


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


def test_remote_create_does_not_stage_unrelated_files_in_the_enclosing_repo(hub, local_graph, monkeypatch):
    """`_bootstrap`'s commit must add only contributors.yaml. `git add -A` in the
    enclosing repo would also stage (and remote_create would then push) any unrelated
    scratch file or secret the monorepo layout puts next to this graph, with no listing
    or confirmation."""
    (local_graph / "secret.txt").write_text("shh", encoding="utf-8")
    monkeypatch.chdir(local_graph)

    assert main(["remote", "create", "trading", "--on", hub.url, "--as", "seb",
                 "--owner-secret", hub.secret]) == 0

    hosted = git("ls-tree", "-r", "--name-only", "HEAD", cwd=hub.registry.repo("trading")).stdout
    assert "secret.txt" not in hosted
    assert (local_graph / "secret.txt").exists()
    assert "secret.txt" in git("status", "--porcelain", cwd=local_graph).stdout


def test_remote_create_bootstrap_commit_failure_is_one_line(hub, local_graph, monkeypatch, capsys):
    """git's "Please tell me who you are" refusal is several lines; only the first, plus
    a hint, belongs in the one line every other refusal here gives."""
    git("config", "--unset", "user.email", cwd=local_graph)
    monkeypatch.chdir(local_graph)

    assert main(["remote", "create", "trading", "--on", hub.url, "--as", "seb",
                 "--owner-secret", hub.secret]) == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "could not commit" in err


def test_knoten_key_prints_the_public_line(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert main(["key", "seb"]) == 0
    out = capsys.readouterr().out
    assert "ssh-ed25519 AAAA" in out and str(key_dir() / "seb") in out
    assert main(["key", "seb"]) == 0
    assert capsys.readouterr().out == out           # same key, second time


# ---------------------------------------------------------------- invite signs, join adds itself

def test_the_whole_journey_signed(hub, shared_signed, tmp_path, monkeypatch, capsys):
    """Seb invites (signed by his key). Maria joins: her key is made, her clone signs,
    her own commit adds her to contributors.yaml with the invite, and the gate lets that
    in because the invite is seb's and the commit is hers. Then she pushes a node."""
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
    """Seb's token on a machine without seb's key: the token gets in, the invite cannot
    be signed, and the server would refuse it anyway. The refusal itself must not mint a
    fresh, mismatched keypair under seb's name -- `ensure_key` never regenerates an
    existing key, so a wrong one made here would be wrong forever."""
    (key_dir() / "seb").unlink(); (key_dir() / "seb.pub").unlink()
    monkeypatch.chdir(shared_signed)

    assert main(["invite", "maria"]) == 1
    assert "different key" in capsys.readouterr().err
    assert not (key_dir() / "seb").exists()
    assert not (key_dir() / "seb.pub").exists()


def test_a_joiner_whose_push_is_refused_is_told_why(hub, shared_signed, tmp_path, monkeypatch, capsys):
    """The admin renames the graph between the invite and the join. `/join` only
    re-verifies the SIGNER (still seb, still an active admin), so redemption succeeds --
    the mismatch is caught only by the gate, on the join commit itself, checking the
    blob's graph name against the PARENT commit's `graph.yaml`. The message must be the
    gate's own reason, not git's generic refusal, and must say the clone and credentials
    are already in place."""
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


def test_invite_refuses_an_absurd_expires_before_it_can_overflow(hub, shared_signed, capsys):
    """`datetime.timedelta(days=...)` raises a raw `OverflowError` for a large enough
    number -- long before the server ever gets a chance to enforce the very same bound
    itself. The client must catch it first, in the server's own words."""
    assert main(["invite", "maria", "--expires", "999999999999"]) == 1
    err = capsys.readouterr().err
    assert "days must be between 1 and 365" in err and "Traceback" not in err


@pytest.fixture
def nested_local_graph(tmp_path, rules_yaml):
    """Like `local_graph`, but the graph lives one directory down inside its own repo, at
    `g/` -- the monorepo layout `_bootstrap`, `Registry.head_graph` and the gate already
    all support (see `tests/test_gate.py`'s `bare` fixture for the same shape)."""
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
    """The hosted graph lives at `g/`, not the clone's root. `join` has to find it there
    the same way the gate does, add the newcomer to `g/contributors.yaml`, and stage
    exactly that path -- not `contributors.yaml` at the (nonexistent) clone root."""
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
    """Two things end access: the mark in contributors.yaml (the gate refuses her key)
    and the token (the server refuses her connection). The mark comes first so the
    record exists even if the API call then fails."""
    assert main(["invite", "maria"]) == 0
    code = capsys.readouterr().out.strip().split()[-1]
    monkeypatch.chdir(tmp_path)
    assert main(["join", f"{hub.url}/trading", "--invite", code]) == 0
    clone = tmp_path / "trading"
    git("config", "user.email", "m@m.m", cwd=clone); git("config", "user.name", "maria", cwd=clone)

    monkeypatch.chdir(shared_signed)
    assert main(["revoke", "maria"]) == 0

    contribs, _ = hub.registry.head_graph("trading")
    assert contribs["maria"]["revoked"] == today()
    assert hub.registry.authenticate("trading", "maria", "anything") is None
    commit_node(clone, "hyp-m.md", "---\nid: hyp-m\ntype: hypothesis\nstatus: open\n---\n\n# m\n")
    monkeypatch.chdir(clone)
    assert main(["push"]) == 1


def test_revoking_a_name_the_graph_does_not_list_is_one_line(hub, shared_signed, monkeypatch, capsys):
    monkeypatch.chdir(shared_signed)
    assert main(["revoke", "ghost"]) == 1
    assert "not listed" in capsys.readouterr().err

"""The client side of a remote graph: a credential store git calls through its own
credential-helper protocol, and commands that wrap git plus four JSON calls."""
import os
import stat
import subprocess

import pytest

from knoten.cli import main
from knoten.core import GraphError
from knoten.remote import cred_lookup, cred_path, cred_store, credential_helper


def git(*args, cwd, env=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          env={**os.environ, **(env or {})})


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

def commit_node(work, name, text):
    (work / "nodes" / name).write_text(text, encoding="utf-8")
    git("add", "-A", cwd=work)
    git("commit", "-qm", name, cwd=work)


@pytest.fixture
def shared(hub, local_graph, monkeypatch):
    """`local_graph` created on `hub` by its admin through the CLI, cwd inside it."""
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
    assert cred_lookup(hub.url) == ("owner", hub.secret)
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
    was a traceback instead of the one line every other refusal gives."""
    import io
    monkeypatch.chdir(local_graph)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

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
    tok = hub.registry.mint("trading", "maria", "write")
    other = tmp_path / "maria"
    url = f"http://maria:{tok}@{hub.url.removeprefix('http://')}/trading.git"
    git("clone", "-q", url, str(other), cwd=tmp_path)
    git("config", "user.email", "m@m.m", cwd=other); git("config", "user.name", "maria", cwd=other)
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


def test_push_without_a_remote_says_so(local_graph, monkeypatch, capsys):
    monkeypatch.chdir(local_graph)
    assert main(["push"]) == 1
    assert "remote create" in capsys.readouterr().err


def test_remote_add_points_an_existing_clone_at_a_remote(hub, local_graph, monkeypatch):
    monkeypatch.chdir(local_graph)
    assert main(["remote", "add", f"{hub.url}/trading"]) == 0

    assert git("remote", "get-url", "origin", cwd=local_graph).stdout.strip() == f"{hub.url}/trading.git"
    assert git("config", "credential.useHttpPath", cwd=local_graph).stdout.strip() == "true"

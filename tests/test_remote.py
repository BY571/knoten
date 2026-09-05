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

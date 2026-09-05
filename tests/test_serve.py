"""knoten's server does one thing per request: decide whether this token may do this,
then hand the request to `git http-backend`, which ships with git and speaks the smart
HTTP protocol. Everything hard (packfiles, refs, negotiation, hooks) is git's. These
tests therefore drive real git clients at a real server.
"""
import base64
import json
import os
import subprocess
import urllib.error
import urllib.request

import pytest

ALIVE_NO_GATE = "---\nid: hyp-x\ntype: hypothesis\nstatus: alive\n---\n\n# x\n"


def git(*args, cwd, env=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          env={**os.environ, **(env or {})})


def clone_url(hub, graph, user, token):
    return f"http://{user}:{token}@{hub.url.removeprefix('http://')}/{graph}.git"


def commit_node(work, name, text):
    (work / "nodes" / name).write_text(text, encoding="utf-8")
    git("add", "-A", cwd=work)
    git("commit", "-qm", name, cwd=work)


def auth_refused(r):
    """git phrases a 401 three ways: `401` when it cannot retry, `Authentication failed`
    when the credentials it sent were rejected, `could not read Username` when it had
    none and prompts are off. All three are the server saying no."""
    return r.returncode != 0 and any(
        s in r.stderr for s in ("401", "Authentication failed", "Username"))


@pytest.fixture
def trading(hub, local_graph):
    """A graph on the server with its admin's token, pushed once from `local_graph`."""
    admin = hub.registry.create("trading", admin="seb")
    git("remote", "add", "origin", clone_url(hub, "trading", "seb", admin), cwd=local_graph)
    r = git("push", "-q", "origin", "master", cwd=local_graph)
    assert r.returncode == 0, r.stderr
    return {"admin": admin, "work": local_graph}


# ---------------------------------------------------------------- reading and writing

def test_an_admin_can_push_and_the_server_holds_it(hub, trading):
    repo = hub.registry.repo("trading")
    assert "seed" in git("log", "--oneline", cwd=repo).stdout


def test_a_read_token_can_clone_but_not_push(hub, trading, tmp_path):
    """git's own protocol separates reading from writing by endpoint. Read access is
    never getting past the door for git-receive-pack, nothing subtler."""
    tok = hub.registry.mint("trading", "reader", "read")
    dest = tmp_path / "reader-clone"

    r = git("clone", "-q", clone_url(hub, "trading", "reader", tok), str(dest), cwd=tmp_path)
    assert r.returncode == 0, r.stderr

    git("config", "user.email", "r@r.r", cwd=dest); git("config", "user.name", "r", cwd=dest)
    commit_node(dest, "hyp-r.md", "---\nid: hyp-r\ntype: hypothesis\nstatus: open\n---\n\n# r\n")
    r = git("push", "origin", "master", cwd=dest)

    assert r.returncode != 0
    assert "403" in r.stderr
    assert "hyp-r" not in git("log", "--oneline", cwd=hub.registry.repo("trading")).stdout


def test_no_token_is_a_401_challenge(hub, trading, tmp_path):
    r = git("clone", "-q", f"{hub.url}/trading.git", str(tmp_path / "x"), cwd=tmp_path)

    assert auth_refused(r), r.stderr


def test_a_wrong_token_is_refused(hub, trading, tmp_path):
    r = git("clone", "-q", clone_url(hub, "trading", "seb", "wrong"), str(tmp_path / "x"),
            cwd=tmp_path)

    assert auth_refused(r), r.stderr


def test_an_unknown_graph_looks_like_a_wrong_token(hub, trading, tmp_path):
    """The server must not confirm which graphs exist to someone without a token."""
    r = git("clone", "-q", clone_url(hub, "biology", "seb", trading["admin"]),
            str(tmp_path / "x"), cwd=tmp_path)

    assert auth_refused(r), r.stderr


# ---------------------------------------------------------------- the gate, over HTTP

def test_the_rule_gate_refuses_a_broken_push_over_http(hub, trading):
    """PR #32's pre-receive hook, unchanged, reached through git-http-backend. The
    rule's own message must reach the pusher."""
    work = trading["work"]
    commit_node(work, "hyp-x.md", ALIVE_NO_GATE)

    r = git("push", "origin", "master", cwd=work)

    assert r.returncode != 0
    assert "live-claims-must-cite-their-gates" in r.stderr
    assert "hyp-x" not in git("log", "--oneline", cwd=hub.registry.repo("trading")).stdout


def test_a_clean_push_after_a_refused_one_lands(hub, trading):
    work = trading["work"]
    commit_node(work, "hyp-x.md", ALIVE_NO_GATE)
    git("push", "origin", "master", cwd=work)
    git("reset", "-q", "--hard", "HEAD~1", cwd=work)
    commit_node(work, "hyp-y.md", "---\nid: hyp-y\ntype: hypothesis\nstatus: open\n---\n\n# y\n")

    r = git("push", "origin", "master", cwd=work)

    assert r.returncode == 0, r.stderr
    assert "hyp-y" in git("log", "--oneline", cwd=hub.registry.repo("trading")).stdout


def test_a_second_clone_sees_what_the_first_pushed(hub, trading, tmp_path):
    tok = hub.registry.mint("trading", "maria", "write")
    dest = tmp_path / "maria"
    git("clone", "-q", clone_url(hub, "trading", "maria", tok), str(dest), cwd=tmp_path)

    assert (dest / "nodes" / "hyp-ok.md").exists()


def test_a_crashed_backend_is_a_500_not_a_silent_200(hub, trading, monkeypatch):
    """A refusal by the gate arrives in the sideband with exit 0, so a non-zero exit
    with no Status line can only be the backend dying. That used to relay as 200 with
    an empty body. Real git cannot be made to crash headerless on demand, which is why
    this one test fakes the subprocess: it is testing the relay, not git."""
    import subprocess as sp
    from knoten import serve as serve_mod

    def dead(*args, **kwargs):
        return sp.CompletedProcess(args, 128, stdout=b"", stderr=b"fatal: boom")
    monkeypatch.setattr(serve_mod.subprocess, "run", dead)

    req = urllib.request.Request(
        f"{hub.url}/trading.git/info/refs?service=git-upload-pack",
        headers={"Authorization": "Basic " + base64.b64encode(
            f"seb:{trading['admin']}".encode()).decode()})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)

    assert e.value.code == 500
    assert "http-backend failed" in json.loads(e.value.read())["error"]

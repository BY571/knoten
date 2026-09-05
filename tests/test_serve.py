"""knoten's server does one thing per request: decide whether this token may do this,
then hand the request to `git http-backend`, which ships with git and speaks the smart
HTTP protocol. Everything hard (packfiles, refs, negotiation, hooks) is git's. These
tests therefore drive real git clients at a real server.
"""
import base64
import http.client
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


def test_a_chunked_push_is_refused_not_dumped_as_a_bare_500(hub, trading):
    """_body only ever reads Content-Length bytes. A chunked push (git goes chunked
    above http.postBuffer, default 1 MiB) used to hand http-backend an empty stdin,
    which died, leaving the client with an inscrutable 500 and no idea why."""
    host, port = hub.url.removeprefix("http://").rsplit(":", 1)
    cred = base64.b64encode(f"seb:{trading['admin']}".encode()).decode()
    conn = http.client.HTTPConnection(host, int(port), timeout=5)
    conn.putrequest("POST", "/trading.git/git-receive-pack")
    conn.putheader("Content-Type", "application/x-git-receive-pack-request")
    conn.putheader("Authorization", "Basic " + cred)
    conn.putheader("Transfer-Encoding", "chunked")
    conn.endheaders()
    conn.send(b"0\r\n\r\n")
    r = conn.getresponse()
    body = json.loads(r.read())
    conn.close()

    assert r.status == 411, body
    assert "http.postBuffer" in body["error"]


# ---------------------------------------------------------------- the api

def api(hub, path, body, auth=None):
    """Raw urllib, deliberately not the knoten client: the server is being tested."""
    req = urllib.request.Request(hub.url + path, data=json.dumps(body).encode(),
                                 method="POST", headers={"Content-Type": "application/json"})
    if auth:
        cred = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        req.add_header("Authorization", "Basic " + cred)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_the_owner_secret_creates_a_graph_and_gets_the_admin_token(hub):
    status, body = api(hub, "/graphs", {"name": "biology", "admin": "seb"}, ("owner", hub.secret))

    assert status == 201
    assert hub.registry.authenticate("biology", "seb", body["token"]) == "admin"
    assert (hub.registry.repo("biology") / "hooks" / "pre-receive").exists()


def test_creating_a_graph_without_the_owner_secret_is_refused(hub):
    status, body = api(hub, "/graphs", {"name": "biology", "admin": "seb"}, ("owner", "nope"))
    assert status == 401
    assert not hub.registry.exists("biology")

    status, _ = api(hub, "/graphs", {"name": "biology", "admin": "seb"})
    assert status == 401


def test_a_bad_graph_name_is_a_400_and_creates_nothing(hub):
    status, body = api(hub, "/graphs", {"name": "../etc", "admin": "seb"}, ("owner", hub.secret))

    assert status == 400
    assert "not a valid graph name" in body["error"]
    assert list((hub.data / "graphs").iterdir()) == []


def test_an_admin_can_invite_and_the_invitee_can_join(hub, trading):
    status, body = api(hub, "/trading/invite", {"name": "maria", "role": "write", "days": 7},
                       ("seb", trading["admin"]))
    assert status == 200, body

    status, joined = api(hub, "/trading/join", {"code": body["code"]})

    assert status == 200, joined
    assert joined["name"] == "maria" and joined["role"] == "write"
    assert hub.registry.authenticate("trading", "maria", joined["token"]) == "write"


def test_a_write_token_cannot_invite(hub, trading):
    tok = hub.registry.mint("trading", "maria", "write")

    status, body = api(hub, "/trading/invite", {"name": "x", "role": "write"}, ("maria", tok))

    assert status == 403
    assert "only an admin" in body["error"]


def test_joining_twice_with_one_code_fails_the_second_time(hub, trading):
    _, inv = api(hub, "/trading/invite", {"name": "maria", "role": "read"}, ("seb", trading["admin"]))
    api(hub, "/trading/join", {"code": inv["code"]})

    status, body = api(hub, "/trading/join", {"code": inv["code"]})

    assert status == 400
    assert "not valid" in body["error"]


def test_join_on_an_unknown_graph_looks_like_a_wrong_code(hub, trading):
    """/join needs no credentials, so it must not confirm which graphs exist: a
    nonexistent graph and a wrong code get byte-identical 400 bodies."""
    status_unknown, body_unknown = api(hub, "/biology/join", {"code": "x"}, None)
    status_known, body_known = api(hub, "/trading/join", {"code": "x"}, None)

    assert status_unknown == 400 and status_known == 400
    assert body_unknown == body_known


def test_revoke_ends_a_contributors_access(hub, trading, tmp_path):
    tok = hub.registry.mint("trading", "maria", "write")

    status, _ = api(hub, "/trading/revoke", {"name": "maria"}, ("seb", trading["admin"]))
    assert status == 200

    r = git("clone", "-q", clone_url(hub, "trading", "maria", tok), str(tmp_path / "x"), cwd=tmp_path)
    assert auth_refused(r), r.stderr


def test_malformed_json_is_a_400_not_a_traceback(hub, trading):
    req = urllib.request.Request(hub.url + "/trading/join", data=b"{not json",
                                 method="POST", headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)

    assert e.value.code == 400
    assert "not JSON" in json.loads(e.value.read())["error"]


def test_a_json_body_that_is_not_an_object_is_a_400(hub, trading):
    """`[1, 2, 3]` parses as JSON and then crashed the thread on `.get`. The client saw
    a closed connection, not a refusal."""
    status, body = api(hub, "/trading/invite", [1, 2, 3], ("seb", trading["admin"]))
    assert status == 400
    assert "JSON object" in body["error"]


def test_a_malformed_or_negative_content_length_is_a_400_not_a_hang(hub, trading):
    """"Content-Length: abc" used to raise ValueError inside _body, unguarded, killing
    the thread with no response. "Content-Length: -1" used to reach rfile.read(-1),
    which reads until the socket closes -- on a connection the client never closes,
    a thread parked forever with no credentials required to trigger it."""
    host, port = hub.url.removeprefix("http://").rsplit(":", 1)
    for bad in ("abc", "-1"):
        conn = http.client.HTTPConnection(host, int(port), timeout=5)
        conn.putrequest("POST", "/trading/join")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", bad)
        conn.endheaders()
        r = conn.getresponse()
        body = json.loads(r.read())
        conn.close()

        assert r.status == 400, (bad, body)
        assert "invalid" in body["error"]


def test_a_non_numeric_days_is_a_400(hub, trading):
    status, body = api(hub, "/trading/invite",
                       {"name": "maria", "role": "write", "days": "banana"}, ("seb", trading["admin"]))
    assert status == 400
    assert "whole number" in body["error"]


def test_unknown_paths_are_404(hub):
    status, _ = api(hub, "/trading/steal", {})
    assert status == 404


# ---------------------------------------------------------------- the cli

def test_serve_prints_the_owner_secret_exactly_once(tmp_path, monkeypatch, capsys):
    """Shown on first run, when the directory is created, and never again: the secret
    on a terminal scrollback is one thing, in every restart's log is another."""
    from http.server import ThreadingHTTPServer
    from knoten.cli import main

    monkeypatch.setattr(ThreadingHTTPServer, "serve_forever", lambda self: None)

    assert main(["serve", "--data", str(tmp_path / "d"), "--bind", "127.0.0.1:0"]) == 0
    first = capsys.readouterr().out
    assert main(["serve", "--data", str(tmp_path / "d"), "--bind", "127.0.0.1:0"]) == 0
    second = capsys.readouterr().out

    secret = (tmp_path / "d" / "owner").read_text().strip()
    assert secret in first
    assert secret not in second
    assert "serving" in second


def test_serve_warns_when_bound_to_a_non_local_address(tmp_path, monkeypatch, capsys):
    from http.server import ThreadingHTTPServer
    from knoten.cli import main

    monkeypatch.setattr(ThreadingHTTPServer, "serve_forever", lambda self: None)
    main(["serve", "--data", str(tmp_path / "d"), "--bind", "0.0.0.0:0"])

    assert "plain HTTP" in capsys.readouterr().err


def test_serve_refuses_a_non_numeric_port_before_creating_the_owner_secret(tmp_path, capsys):
    """The secret used to be written before the bind was parsed, so a typo in --bind
    created it, crashed, and never showed it; every later run then saw an existing
    file and stayed silent. Fail before writing anything."""
    from knoten.cli import main

    assert main(["serve", "--data", str(tmp_path / "d"), "--bind", "127.0.0.1:abc"]) == 1
    err = capsys.readouterr().err
    assert "numeric port" in err and "Traceback" not in err
    assert not (tmp_path / "d" / "owner").exists()


# ---------------------------------------------------------------- isolation and races

def test_two_graphs_on_one_server_do_not_share_tokens(hub, trading, tmp_path):
    """A token for `trading` opens nothing on `biology`, including reading. One server,
    many graphs, no cross-talk, or the invite model means nothing."""
    hub.registry.create("biology", admin="seb")

    r = git("clone", "-q", clone_url(hub, "biology", "seb", trading["admin"]),
            str(tmp_path / "x"), cwd=tmp_path)

    assert auth_refused(r), r.stderr


def test_concurrent_joins_do_not_lose_each_other(hub, trading):
    """Eight invites redeemed at the same moment. A read-modify-write on tokens.json
    without the lock drops some of them, silently, and the file still parses."""
    import concurrent.futures

    codes = [hub.registry.invite("trading", f"user-{i}", "write") for i in range(8)]

    def redeem(code):
        _, body = api(hub, "/trading/join", {"code": code})
        return body["name"], body["token"]

    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        results = list(pool.map(redeem, codes))

    for name, token in results:
        assert hub.registry.authenticate("trading", name, token) == "write", f"{name} lost"
    on_disk = json.loads((hub.registry.graph_dir("trading") / "tokens.json").read_text())
    assert len(on_disk) == 9          # seb + eight


def test_a_traversal_in_the_url_is_404_not_a_file(hub, trading):
    """`/../../etc.git` must never reach GIT_PROJECT_ROOT. The regex refuses it before
    the registry sees it; this pins that the regex stays strict."""
    req = urllib.request.Request(hub.url + "/../../etc.git/info/refs?service=git-upload-pack")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)

    assert e.value.code in (401, 404)
    e.value.close()  # unread, it leaves the socket for the GC to warn about later


def test_serve_closes_its_socket_when_it_stops(tmp_path, monkeypatch):
    """`serve_forever` returning is not the socket closing. Left open, the port stays
    bound until the interpreter exits."""
    import warnings
    from http.server import ThreadingHTTPServer
    from knoten.cli import main

    monkeypatch.setattr(ThreadingHTTPServer, "serve_forever", lambda self: None)
    with warnings.catch_warnings():
        warnings.simplefilter("error", ResourceWarning)
        assert main(["serve", "--data", str(tmp_path / "d"), "--bind", "127.0.0.1:0"]) == 0
        import gc; gc.collect()

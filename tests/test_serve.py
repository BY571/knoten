"""knoten's server does one thing per request: decide whether this token may do this,
then hand the request to `git http-backend`, which ships with git and speaks the smart
HTTP protocol. Everything hard (packfiles, refs, negotiation, hooks) is git's. These
tests therefore drive real git clients at a real server.
"""
import base64
import http.client
import json
import socket
import urllib.error
import urllib.request

import pytest
from conftest import commit_node, git

ALIVE_NO_GATE = "---\nid: hyp-x\ntype: hypothesis\nstatus: alive\n---\n\n# x\n"


def clone_url(hub, graph, user, token):
    return f"http://{user}:{token}@{hub.url.removeprefix('http://')}/{graph}.git"


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


def test_a_force_push_is_refused_by_the_shared_repo(hub, trading, tmp_path):
    """A `write` collaborator's stray `--force` wiped the shared graph, and the repo kept
    no reflog to recover it from. The refusal belongs in the config of the repo everyone
    pushes to, not in the discipline of every clone."""
    tok = hub.registry.mint("trading", "maria", "write")
    dest = tmp_path / "maria"
    git("clone", "-q", clone_url(hub, "trading", "maria", tok), str(dest), cwd=tmp_path)
    git("config", "user.email", "m@m.m", cwd=dest); git("config", "user.name", "m", cwd=dest)
    repo = hub.registry.repo("trading")
    before = git("rev-parse", "master", cwd=repo).stdout.strip()
    git("commit", "-q", "--amend", "-m", "rewritten history", cwd=dest)

    r = git("push", "-f", "origin", "master", cwd=dest)

    assert r.returncode != 0
    assert git("rev-parse", "master", cwd=repo).stdout.strip() == before


def test_deleting_a_branch_on_the_server_is_refused(hub, trading):
    """A shared graph is append-only. This is deliberately the opposite of
    `test_deleting_a_branch_is_not_treated_as_a_push`, which drives the raw hook on a
    bare repo with none of this config: the HOOK must still tolerate an all-zero oid, and
    the REPO must still refuse the deletion that carries it."""
    work = trading["work"]
    assert git("push", "-q", "origin", "master:scratch", cwd=work).returncode == 0

    r = git("push", "origin", "--delete", "scratch", cwd=work)

    assert r.returncode != 0
    assert "scratch" in git("branch", cwd=hub.registry.repo("trading")).stdout


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
    this one test fakes the subprocess: it is testing the relay, not git.

    The fake replaces knoten's own bound name, not `subprocess.run`: patching the module
    hands the fake to every thread in the process, and a handler thread outliving an
    earlier test would pick it up."""
    import subprocess as sp
    from knoten import serve as serve_mod

    def dead(*args, **kwargs):
        return sp.CompletedProcess(args, 128, stdout=b"", stderr=b"fatal: boom")
    monkeypatch.setattr(serve_mod, "_run", dead)

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


@pytest.mark.parametrize("bad", [
    pytest.param("abc", id="not-a-number"),
    pytest.param("-1", id="negative"),
    pytest.param("999999999", id="past-the-push-ceiling"),
])
def test_a_malformed_or_oversized_content_length_is_a_400_not_a_hang(hub, trading, bad):
    """"Content-Length: abc" used to raise ValueError inside _body, unguarded, killing
    the thread with no response. "Content-Length: -1" used to reach rfile.read(-1),
    which reads until the socket closes -- on a connection the client never closes,
    a thread parked forever with no credentials required to trigger it."""
    host, port = hub.url.removeprefix("http://").rsplit(":", 1)
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
    """`/../../etc.git` must never reach GIT_PROJECT_ROOT. Sent down a raw socket, not
    through urllib: urllib normalises `..` away in the CLIENT, so the old version of this
    test sent `/etc.git/...` and would have passed against a server with no check at all."""
    host, port = hub.url.removeprefix("http://").rsplit(":", 1)
    with socket.create_connection((host, int(port)), timeout=5) as sock:
        sock.sendall(b"GET /../../etc.git/info/refs?service=git-upload-pack HTTP/1.0\r\n\r\n")
        first = b""
        while b"\r\n" not in first:
            chunk = sock.recv(4096)
            if not chunk:
                break
            first += chunk

    assert first.split(b"\r\n")[0] == b"HTTP/1.0 404 Not Found", first[:120]


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


# ---------------------------------------------------------------- hostile requests

def test_a_body_that_is_not_utf8_is_a_400_not_a_dead_thread(hub, trading):
    """UnicodeDecodeError is not a JSONDecodeError. Raw bytes on the unauthenticated
    /join route escaped the handler and killed the serving thread, so the caller got a
    closed connection and the server lost a thread per request."""
    req = urllib.request.Request(hub.url + "/trading/join", data=b"\xff\xfe\x00binary",
                                 method="POST", headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)

    assert e.value.code == 400
    assert "not JSON" in json.loads(e.value.read())["error"]


def test_a_percent_encoded_service_does_not_get_a_read_token_past_the_write_gate(hub, trading):
    """The gate used to look for the literal `git-receive-pack` in the raw query string.
    git decodes the query, so `service=git-receive%2Dpack` asks for exactly the same
    service while carrying none of the letters the check searched for, and a `read` token
    got the receive-pack advertisement."""
    tok = hub.registry.mint("trading", "reader", "read")
    req = urllib.request.Request(
        f"{hub.url}/trading.git/info/refs?service=git-receive%2Dpack",
        headers={"Authorization": "Basic " + base64.b64encode(
            f"reader:{tok}".encode()).decode()})

    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)

    assert e.value.code == 403
    assert "read access" in json.loads(e.value.read())["error"]


def test_a_chunked_body_on_the_api_is_a_411_not_a_silent_empty_object(hub, trading):
    """Only the git routes refused chunked. /join read Content-Length bytes, got none,
    parsed `{}` and refused the code the caller had actually sent -- a wrong answer
    dressed as the right one."""
    host, port = hub.url.removeprefix("http://").rsplit(":", 1)
    conn = http.client.HTTPConnection(host, int(port), timeout=5)
    conn.putrequest("POST", "/trading/join")
    conn.putheader("Content-Type", "application/json")
    conn.putheader("Transfer-Encoding", "chunked")
    conn.endheaders()
    conn.send(b"0\r\n\r\n")
    r = conn.getresponse()
    body = json.loads(r.read())
    conn.close()

    assert r.status == 411, body
    assert "chunked" in body["error"]


def test_an_absurd_invite_lifetime_is_a_400_not_a_dropped_connection(hub, trading):
    """`days: 999999999999` reached timedelta, which raised OverflowError and killed the
    thread: the client saw the connection close with no status at all."""
    status, body = api(hub, "/trading/invite",
                       {"name": "maria", "role": "write", "days": 999999999999},
                       ("seb", trading["admin"]))

    assert status == 400
    assert "between 1 and 365" in body["error"]


# ---------------------------------------------------------------- signed invites

from conftest import commit_signed, make_key, pub_line
from knoten import contributors as C
from knoten.keys import INVITE_NS, sign


@pytest.fixture
def signed_trading(hub, trading, keys_dir):
    """`trading` bootstrapped: contributors.yaml lists seb as admin, pushed signed.
    Returns the fixture dict plus seb's private key under "seb_key"."""
    work = trading["work"]
    seb = make_key(keys_dir, "seb")
    C.dump(work, {"seb": {"key": pub_line(seb), "role": "admin"}})
    commit_signed(work, "seb creates the graph", seb)
    r = git("push", "-q", "origin", "master", cwd=work)
    assert r.returncode == 0, r.stderr
    return {**trading, "seb_key": seb}


def invite_body(priv, graph, name, role):
    blob = C.invite_blob(graph, name, role, "2099-01-01", "n0nce")
    return {"name": name, "role": role, "days": 7, "blob": blob.decode(),
            "sig": sign(priv, blob, INVITE_NS)}


def test_a_signed_graph_refuses_an_unsigned_invite(hub, signed_trading):
    status, body = api(hub, "/trading/invite", {"name": "maria", "role": "write"},
                       ("seb", signed_trading["admin"]))
    assert status == 400
    assert "signed" in body["error"]


def test_a_stolen_admin_token_cannot_mint_an_invite(hub, signed_trading, keys_dir):
    """The token says who is connecting; the key says who is authorising. An invite
    signed by any key but the admin's own is refused even with the admin's token."""
    eve = make_key(keys_dir, "eve")
    status, body = api(hub, "/trading/invite", invite_body(eve, "test", "maria", "write"),
                       ("seb", signed_trading["admin"]))
    assert status == 403
    assert "your own signing key" in body["error"]


def test_an_admin_signed_invite_is_stored_and_handed_to_the_joiner(hub, signed_trading):
    body = invite_body(signed_trading["seb_key"], "test", "maria", "write")
    status, got = api(hub, "/trading/invite", body, ("seb", signed_trading["admin"]))
    assert status == 200, got

    status, joined = api(hub, "/trading/join", {"code": got["code"]})

    assert status == 200
    assert joined["blob"] == body["blob"] and joined["sig"] == body["sig"] and joined["by"] == "seb"
    assert hub.registry.authenticate("trading", "maria", joined["token"]) == "write"


def test_an_invite_whose_blob_disagrees_with_the_request_is_refused(hub, signed_trading):
    """The signed bytes say maria/write; the request says maria/admin. The signature is
    real, the request is not what was signed."""
    body = invite_body(signed_trading["seb_key"], "test", "maria", "write")
    body["role"] = "admin"
    status, got = api(hub, "/trading/invite", body, ("seb", signed_trading["admin"]))
    assert status == 400
    assert "different name, role or graph" in got["error"]


def test_a_phase_1_graph_still_invites_without_a_signature(hub, trading):
    status, got = api(hub, "/trading/invite", {"name": "maria", "role": "write"},
                      ("seb", trading["admin"]))
    assert status == 200
    status, joined = api(hub, "/trading/join", {"code": got["code"]})
    assert status == 200 and joined["blob"] == "" and joined["sig"] == ""


# ---------------------------------------------------------------- signed invites: review fixes

def test_a_revoked_admins_earlier_invite_is_refused_at_join_not_only_at_the_gate(hub, signed_trading):
    """The gate refuses the eventual join COMMIT once seb is revoked, but that is not
    enough on its own: without re-checking at redeem, the holder already had a live
    TOKEN the moment /join answered, and a token reads a private graph whether or not
    its holder ever gets as far as committing."""
    body = invite_body(signed_trading["seb_key"], "test", "maria", "write")
    status, got = api(hub, "/trading/invite", body, ("seb", signed_trading["admin"]))
    assert status == 200, got

    work, seb = signed_trading["work"], signed_trading["seb_key"]
    C.dump(work, {"seb": {"key": pub_line(seb), "role": "admin", "revoked": "2020-01-01"}})
    commit_signed(work, "seb steps down", seb)
    r = git("push", "-q", "origin", "master", cwd=work)
    assert r.returncode == 0, r.stderr

    status, joined = api(hub, "/trading/join", {"code": got["code"]})

    assert status == 400
    assert "no longer valid" in joined["error"]
    assert hub.registry.authenticate("trading", "maria", joined.get("token", "")) is None


def test_an_unsigned_graph_refuses_an_invite_carrying_a_signature(hub, trading):
    status, body = api(hub, "/trading/invite",
                       {"name": "maria", "role": "write", "blob": "x", "sig": "y"},
                       ("seb", trading["admin"]))
    assert status == 400
    assert "not signed" in body["error"]


def test_invite_fields_over_the_size_cap_are_refused(hub, trading):
    status, body = api(hub, "/trading/invite",
                       {"name": "maria", "role": "write", "blob": "x" * 4097, "sig": ""},
                       ("seb", trading["admin"]))
    assert status == 400
    assert "too large" in body["error"]

    status, body = api(hub, "/trading/invite",
                       {"name": "maria", "role": "write", "blob": "", "sig": "y" * 8193},
                       ("seb", trading["admin"]))
    assert status == 400
    assert "too large" in body["error"]


def test_a_lone_surrogate_in_an_invite_field_is_a_400_not_a_500(hub, signed_trading):
    """A crafted \\ud800 escape in the JSON body decodes fine through json.loads but not
    through .encode(): unguarded, that reached the catch-all as a bare 500."""
    status, body = api(hub, "/trading/invite",
                       {"name": "maria", "role": "write", "blob": "\ud800", "sig": "x"},
                       ("seb", signed_trading["admin"]))
    assert status == 400


def test_an_admin_token_not_listed_in_contributors_gets_a_clear_refusal(hub, signed_trading):
    """A registry admin token and a listed admin in contributors.yaml are different
    things: this admin's own token authenticates, but the graph never named them, so
    there is no key of theirs to check a signature against at all."""
    rogue = hub.registry.mint("trading", "rogue", "admin")
    body = invite_body(signed_trading["seb_key"], "test", "maria", "write")

    status, got = api(hub, "/trading/invite", body, ("rogue", rogue))

    assert status == 403
    assert "not a listed admin" in got["error"]


# ---------------------------------------------------------------- the invite list

def test_an_admin_can_list_the_open_invites(hub, trading):
    """An admin who cannot see who is pending cannot tell a forgotten invite from a
    revoked one. The hashes stay on the server."""
    _, invited = api(hub, "/trading/invite", {"name": "maria", "role": "write"},
                     ("seb", trading["admin"]))

    status, body = api(hub, "/trading/invites", {}, ("seb", trading["admin"]))

    assert status == 200, body
    assert body["invites"][0]["name"] == "maria"
    assert body["invites"][0]["role"] == "write"
    assert body["invites"][0]["by"] == "seb", "the list does not say who invited them"
    assert invited["code"] not in json.dumps(body)
    assert "hash" not in json.dumps(body)


def test_a_write_token_cannot_list_the_invites(hub, trading):
    tok = hub.registry.mint("trading", "maria", "write")

    status, body = api(hub, "/trading/invites", {}, ("maria", tok))

    assert status == 403
    assert "only an admin" in body["error"]


# ---------------------------------------------------------------- the access log

def test_a_join_leaves_a_line_in_the_access_log(hub, trading, capfd):
    """An owner has to be able to answer "who got access to this graph, and when". Reads
    stay out of it, or the log is a `git fetch` poll loop and nothing else."""
    _, invited = api(hub, "/trading/invite", {"name": "maria", "role": "write"},
                     ("seb", trading["admin"]))
    capfd.readouterr()

    api(hub, "/trading/join", {"code": invited["code"]})

    err = capfd.readouterr().err
    assert "join:write" in err
    assert "trading" in err and "maria" in err


# ---------------------------------------------------------------- the push itself

def test_a_token_revoked_between_the_advertisement_and_the_push_is_refused(hub, trading,
                                                                            tmp_path):
    """Every request is authenticated on its own. git makes two: `info/refs` to see what
    the server has, then the POST that carries the pack. A token checked only at the
    first would let a revoked contributor finish a push they had already started."""
    tok = hub.registry.mint("trading", "maria", "write")
    dest = tmp_path / "maria"
    git("clone", "-q", clone_url(hub, "trading", "maria", tok), str(dest), cwd=tmp_path)
    git("config", "user.email", "m@m.m", cwd=dest); git("config", "user.name", "m", cwd=dest)
    commit_node(dest, "hyp-m.md", "---\nid: hyp-m\ntype: hypothesis\nstatus: open\n---\n\n# m\n")

    hub.registry.revoke("trading", "maria")
    r = git("push", "origin", "master", cwd=dest)

    assert auth_refused(r), r.stderr
    assert "hyp-m" not in git("log", "--oneline", cwd=hub.registry.repo("trading")).stdout


def test_an_annotated_tag_carrying_a_broken_tree_is_refused(hub, trading):
    """An annotated tag is its own object, not a commit, and it reaches the hook as the
    new oid. A gate that only knows how to read a commit would let a published, broken
    snapshot onto the server."""
    work = trading["work"]
    commit_node(work, "hyp-x.md", ALIVE_NO_GATE)
    git("tag", "-a", "v1", "-m", "a broken release", cwd=work)

    r = git("push", "origin", "v1", cwd=work)

    assert r.returncode != 0
    assert "live-claims-must-cite-their-gates" in r.stderr
    assert "v1" not in git("tag", cwd=hub.registry.repo("trading")).stdout


def test_a_body_announced_with_expect_100_continue_is_answered_before_it_arrives(hub, trading):
    """A client may withhold the body until the server answers `100 Continue`.
    BaseHTTPRequestHandler answers that in handle_expect_100, BEFORE anything reads the
    body: an override that reads the body first, or that refuses the expectation, turns
    such a request into a deadlock rather than a refusal.

    Hand-built, not driven through git: git appends a bare `Expect:` to its own requests,
    which tells libcurl never to send the header, so no `git push` can reach this path."""
    host, port = hub.url.removeprefix("http://").rsplit(":", 1)
    _, invited = api(hub, "/trading/invite", {"name": "maria", "role": "write"},
                     ("seb", trading["admin"]))
    body = json.dumps({"code": invited["code"]}).encode()

    conn = http.client.HTTPConnection(host, int(port), timeout=5)
    conn.putrequest("POST", "/trading/join")
    conn.putheader("Content-Type", "application/json")
    conn.putheader("Content-Length", str(len(body)))
    conn.putheader("Expect", "100-continue")
    conn.endheaders()
    conn.send(body)
    r = conn.getresponse()          # http.client swallows the interim 100 for us
    got = json.loads(r.read())
    conn.close()

    assert r.status == 200, got
    assert got["name"] == "maria"


def test_a_push_too_big_for_one_read_still_lands(hub, trading):
    """Every other push here fits in a single buffer. A 64 KB body of incompressible text
    does not, so this is the one that would catch a `_body` that stops at the first
    `read()` -- and a graph with a plot pasted into a node pushes at exactly this size."""
    import random
    filler = random.Random(0).randbytes(32768).hex()
    work = trading["work"]
    commit_node(work, "hyp-big.md",
                "---\nid: hyp-big\ntype: hypothesis\nstatus: open\n---\n\n" + filler + "\n")

    r = git("push", "origin", "master", cwd=work)

    assert r.returncode == 0, r.stderr
    assert "hyp-big" in git("log", "--oneline", cwd=hub.registry.repo("trading")).stdout


# ---------------------------------------------------------------- the gate stays installed

@pytest.fixture
def hooks_path_hub(tmp_path, monkeypatch):
    """A server whose account carries `core.hooksPath` in ~/.gitconfig: the shape husky,
    the pre-commit framework and most monorepos leave behind on a developer machine.

    The gate is installed by one git and enforced by another. The CGI environment keeps
    HOME, so receive-pack reads ~/.gitconfig; if the git that installed the hook resolved
    hooksPath differently, the hook sits where the enforcing git never looks and every
    push lands unchecked with rc 0. A gate that fails OPEN reports green forever.
    """
    import threading
    from types import SimpleNamespace

    from knoten.registry import Registry
    from knoten.serve import make_server

    home = tmp_path / "daemon-home"
    elsewhere = home / "unrelated-hooks"
    elsewhere.mkdir(parents=True)
    (home / ".gitconfig").write_text(f"[core]\n\thooksPath = {elsewhere}\n", encoding="utf-8")
    monkeypatch.setenv("KNOTEN_CREDENTIALS", str(tmp_path / "credentials"))
    monkeypatch.setenv("HOME", str(home))          # before the Registry, which installs the gate
    # Undo the autouse isolation for the SERVER side only: this fixture exists to put a
    # global core.hooksPath in front of the code, and pinning it away here would make the
    # test pass without `Registry.create` passing SERVER_GIT_ENV to install_server. The
    # pushing client still gets GIT_ISOLATION, which `git()` merges per subprocess.
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    monkeypatch.delenv("GIT_CONFIG_NOSYSTEM", raising=False)

    reg = Registry(tmp_path / "data")
    srv = make_server(reg, "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address
    yield SimpleNamespace(url=f"http://{host}:{port}", registry=reg,
                          secret=reg.owner_secret(), elsewhere=elsewhere)
    srv.shutdown()
    srv.server_close()


def test_a_global_hooks_path_on_the_server_does_not_disable_the_gate(hooks_path_hub,
                                                                     local_graph):
    """The install asked git where hooks go under one config and receive-pack answered
    under another, so the hook was written to repo.git/hooks while git looked in
    ~/.gitconfig's core.hooksPath. A broken push landed with rc 0 and the server reported
    success. Both gits now run under the same SERVER_GIT_ENV."""
    hub = hooks_path_hub
    admin = hub.registry.create("trading", admin="seb")
    git("remote", "add", "origin", clone_url(hub, "trading", "seb", admin), cwd=local_graph)
    assert git("push", "-q", "origin", "master", cwd=local_graph).returncode == 0
    commit_node(local_graph, "hyp-x.md", ALIVE_NO_GATE)

    r = git("push", "origin", "master", cwd=local_graph)

    assert r.returncode != 0, "the gate failed OPEN under core.hooksPath"
    assert "live-claims-must-cite-their-gates" in r.stderr
    assert "hyp-x" not in git("log", "--oneline", cwd=hub.registry.repo("trading")).stdout
    assert (hub.registry.repo("trading") / "hooks" / "pre-receive").exists()
    assert list(hub.elsewhere.iterdir()) == [], "the gate was installed outside the repo"


def test_a_newline_in_a_username_cannot_forge_a_log_line(hub, trading, capfd):
    """The username comes off the wire and is logged before it is authenticated, so a
    newline in it used to write a second line into the access log: an attacker could
    invent pushes that never happened, by anyone they liked."""
    host, port = hub.url.removeprefix("http://").rsplit(":", 1)
    cred = base64.b64encode(b"a\nforged line 1.2.3.4 trading admin push:tok").decode()
    capfd.readouterr()

    conn = http.client.HTTPConnection(host, int(port), timeout=5)
    conn.request("POST", "/trading.git/git-receive-pack", body=b"",
                 headers={"Authorization": "Basic " + cred,
                          "Content-Type": "application/x-git-receive-pack-request"})
    r = conn.getresponse()
    r.read()
    conn.close()

    err = capfd.readouterr().err
    lines = [l for l in err.splitlines() if l.strip()]

    assert r.status == 401
    assert len(lines) == 1, err          # a second line would be the forgery, indistinguishable
    # The whole payload collapses into the one field it belongs in: it survives as a
    # value, never as a record. Pinned exactly, because "the word is gone" would also
    # pass if the username were dropped, and knowing who tried is the point of the log.
    assert lines[0].split()[3] == "aforgedline1.2.3.4tradingadminpush"


def test_serve_replaces_an_empty_owner_file_and_shows_the_new_secret(tmp_path, monkeypatch,
                                                                     capsys):
    """A zero-byte `owner` file is what a crash between the create and the write leaves.
    It used to count as "already made": serve printed nothing, check_owner had nothing to
    compare against, and every POST /graphs was a 401 forever with no way to tell why."""
    from http.server import ThreadingHTTPServer

    from knoten.cli import main
    from knoten.registry import Registry

    data = tmp_path / "d"
    data.mkdir()
    (data / "owner").write_text("", encoding="utf-8")
    monkeypatch.setattr(ThreadingHTTPServer, "serve_forever", lambda self: None)

    assert main(["serve", "--data", str(data), "--bind", "127.0.0.1:0"]) == 0

    out = capsys.readouterr().out
    secret = (data / "owner").read_text(encoding="utf-8").strip()
    assert secret and secret in out, out
    assert Registry(data).check_owner(secret)


def test_an_over_long_graph_name_is_refused_the_way_an_unknown_one_is(hub, trading, tmp_path):
    """The length cap belongs on create and mint. Raised on the read path it made a
    65-character name a 400 where every unknown graph is a 401, which tells an outsider
    something about a name they were not able to open."""
    long_name = "a" * 65
    r = git("clone", "-q", clone_url(hub, long_name, "seb", trading["admin"]),
            str(tmp_path / "x"), cwd=tmp_path)

    assert auth_refused(r), r.stderr


def test_join_on_an_over_long_name_is_byte_identical_to_a_wrong_code(hub, trading):
    """/join needs no credentials, so every refusal it gives has to look the same. An
    over-length name raising made its body differ from the wrong-code body, which is the
    name oracle the identical bodies exist to close."""
    status_long, body_long = api(hub, "/" + "a" * 65 + "/join", {"code": "x"})
    status_wrong, body_wrong = api(hub, "/trading/join", {"code": "x"})

    assert status_long == status_wrong == 400
    assert body_long == body_wrong


def test_a_push_the_gate_refused_is_not_logged_as_a_200(hub, trading, capfd):
    """http-backend exits 0 and answers 200 when the gate refuses; the refusal is in the
    sideband. Logged by status alone, every rejected push read as a successful one, which
    is exactly the question the log is kept for."""
    work = trading["work"]
    commit_node(work, "hyp-x.md", ALIVE_NO_GATE)
    capfd.readouterr()

    r = git("push", "origin", "master", cwd=work)

    assert r.returncode != 0
    err = capfd.readouterr().err
    pushes = [l for l in err.splitlines() if "git-receive-pack" in l]
    assert pushes and pushes[-1].endswith(" refused"), err


def test_a_failure_after_the_headers_are_sent_does_not_answer_twice(hub, trading, capfd,
                                                                    monkeypatch):
    """The catch-all turns anything unforeseen into a 500. Once a status line is on the
    wire a second response is not a refusal, it is garbage appended to the body of the
    first, and the client parses it as content."""
    from knoten import serve as serve_mod

    def half_answer(self, name, sub, query):
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")
        raise RuntimeError("boom, after the headers")
    monkeypatch.setattr(serve_mod._Handler, "_git", half_answer)
    capfd.readouterr()

    req = urllib.request.Request(
        f"{hub.url}/trading.git/info/refs?service=git-upload-pack",
        headers={"Authorization": "Basic " + base64.b64encode(
            f"seb:{trading['admin']}".encode()).decode()})
    with urllib.request.urlopen(req) as r:
        body = r.read()

    assert r.status == 200
    assert body == b"ok", "a second response was appended to the first"
    err = capfd.readouterr().err
    assert len([l for l in err.splitlines() if "knoten serve:" in l]) == 1, err


# ---------------------------------------------------------------- what the comments claim

def test_a_git_variable_in_the_daemons_environment_does_not_reach_the_backend(hub, trading,
                                                                              tmp_path,
                                                                              monkeypatch):
    """os.environ used to be handed to http-backend wholesale, so a GIT_DIR or a
    GIT_COMMITTER_NAME left in the operator's shell reached the backend and the hook it
    runs against a tree an attacker chose. GIT_DIR is the loud one: it points git at a
    different repository entirely."""
    monkeypatch.setenv("GIT_DIR", "/nonexistent")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "not-this-person")

    r = git("clone", "-q", clone_url(hub, "trading", "seb", trading["admin"]),
            str(tmp_path / "x"), cwd=tmp_path)

    assert r.returncode == 0, r.stderr
    assert (tmp_path / "x" / "nodes" / "hyp-ok.md").exists()


def test_an_unforeseen_error_is_a_500_not_a_dropped_connection(hub, trading, monkeypatch,
                                                               capfd):
    """Before the catch-all, anything the handler did not expect killed the thread and
    left the client holding a connection that never answers, which reads to a user as a
    hung network rather than as a server that broke."""
    def boom(*_a, **_k):
        raise RuntimeError("something nobody planned for")
    monkeypatch.setattr(hub.registry, "authenticate", boom)
    capfd.readouterr()

    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(f"{hub.url}/trading.git/info/refs?service=git-upload-pack")

    assert e.value.code == 500
    assert json.loads(e.value.read())["error"] == "knoten: internal error"
    assert "RuntimeError" in capfd.readouterr().err


def test_every_connection_gets_the_read_timeout(hub, trading, monkeypatch):
    """A valid Content-Length whose body never arrives parked a thread on rfile.read
    forever: no credentials required, one thread per connection, until there are none.
    The class attribute is only half of it. StreamRequestHandler.setup() is what puts it
    on the socket, so this checks the socket, not the constant."""
    from knoten import serve as serve_mod

    seen = []
    original = serve_mod._Handler.do_GET

    def spy(self):
        seen.append(self.connection.gettimeout())
        return original(self)
    monkeypatch.setattr(serve_mod._Handler, "do_GET", spy)

    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(hub.url + "/nothing-here")
    e.value.read()

    assert serve_mod._Handler.timeout == 30
    assert seen == [30]


def test_a_path_that_reads_like_a_refusal_does_not_log_as_one(hub, trading, rules_yaml,
                                                              capfd):
    """The verdict used to be grepped out of the whole response, which carries the hook's
    stderr, and the hook echoes back the paths in the pushed tree. A graph in a directory
    named `denying x` therefore made a push that LANDED log as refused, and every phrase
    the grep looked for is chosen by whoever is pushing."""
    work = trading["work"]
    spoof = work / "denying x"
    (spoof / "nodes").mkdir(parents=True)
    (spoof / "graph.yaml").write_text(rules_yaml, encoding="utf-8")
    (spoof / "nodes" / "hyp-s.md").write_text(
        "---\nid: hyp-s\ntype: hypothesis\nstatus: open\n---\n\n# s\n", encoding="utf-8")
    git("add", "-A", cwd=work)
    git("commit", "-qm", "a second graph in a directory named like a refusal", cwd=work)
    capfd.readouterr()

    r = git("push", "origin", "master", cwd=work)

    assert r.returncode == 0, r.stderr
    # The hook's stderr travels to the CLIENT on band 2, inside the same response the
    # verdict is read from. If it were not there, this test would spoof nothing.
    assert "denying x" in r.stderr, r.stderr
    err = capfd.readouterr().err
    pushes = [l for l in err.splitlines() if "git-receive-pack" in l]
    assert pushes and pushes[-1].endswith(" 200"), err


def test_an_error_escaping_the_handler_is_one_line_not_a_traceback(hub, capfd):
    """socketserver prints a whole traceback block for anything that escapes the handler.
    Nobody reads a stack to find out that a client hung up, and it is unreadable next to
    the access log. Reached by a client that closed before the response was written, and
    by a handler thread outliving the request that started it."""
    capfd.readouterr()

    try:
        raise BrokenPipeError("the client hung up")
    except BrokenPipeError:
        hub.server.handle_error(None, ("127.0.0.1", 4242))

    err = capfd.readouterr().err
    lines = [l for l in err.splitlines() if l.strip()]
    assert len(lines) == 1, err
    assert lines[0] == "knoten serve: BrokenPipeError: the client hung up from 127.0.0.1:4242"
    assert "Traceback" not in err

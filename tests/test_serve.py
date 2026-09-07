import base64
import http.client
import json
import socket
import time
import urllib.error
import urllib.request

import pytest
from conftest import commit_node, git

ALIVE_NO_GATE = "---\nid: hyp-x\ntype: hypothesis\nstatus: alive\n---\n\n# x\n"


def clone_url(hub, graph, user, token):
    return f"http://{user}:{token}@{hub.url.removeprefix('http://')}/{graph}.git"


def auth_refused(r):
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

def test_a_read_token_can_clone_but_not_push(hub, trading, tmp_path):
    """git's own protocol separates reading from writing by endpoint."""
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


def test_an_unknown_graph_looks_like_a_wrong_token(hub, trading, tmp_path):
    """The server must not confirm which graphs exist to someone without a token."""
    r = git("clone", "-q", clone_url(hub, "biology", "seb", trading["admin"]),
            str(tmp_path / "x"), cwd=tmp_path)

    assert auth_refused(r), r.stderr


def test_a_force_push_is_refused_by_the_shared_repo(hub, trading, tmp_path):
    tok = hub.registry.mint("trading", "maria", "write")
    dest = tmp_path / "maria"
    git("clone", "-q", clone_url(hub, "trading", "maria", tok), str(dest), cwd=tmp_path)
    git("config", "user.email", "m@m.m", cwd=dest); git("config", "user.name", "m", cwd=dest)
    repo = hub.registry.repo("trading")
    before = git("rev-parse", "master", cwd=repo).stdout.strip()
    git("commit", "-q", "--amend", "-m", "rewritten history", cwd=dest)
    r = git("push", "-f", "origin", "master", cwd=dest)
    assert r.returncode != 0
    assert "not a fast-forward" in r.stderr
    assert git("rev-parse", "master", cwd=repo).stdout.strip() == before


# ---------------------------------------------------------------- the gate, over HTTP

def test_the_rule_gate_refuses_a_broken_push_over_http(hub, trading):
    """PR #32's pre-receive hook, unchanged, reached through git-http-backend."""
    work = trading["work"]
    commit_node(work, "hyp-x.md", ALIVE_NO_GATE)
    r = git("push", "origin", "master", cwd=work)
    assert r.returncode != 0
    assert "live-claims-must-cite-their-gates" in r.stderr
    assert "hyp-x" not in git("log", "--oneline", cwd=hub.registry.repo("trading")).stdout


def test_a_chunked_push_is_refused_not_dumped_as_a_bare_500(hub, trading):
    """_body only ever reads Content-Length bytes."""
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


def test_malformed_json_is_a_400_not_a_traceback(hub, trading):
    req = urllib.request.Request(hub.url + "/trading/join", data=b"{not json",
                                 method="POST", headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 400
    assert "not JSON" in json.loads(e.value.read())["error"]


# ---------------------------------------------------------------- the cli

# ---------------------------------------------------------------- isolation and races

def test_a_traversal_in_the_url_is_404_not_a_file(hub, trading):
    """`/../../etc.git` must never reach GIT_PROJECT_ROOT."""
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


# ---------------------------------------------------------------- hostile requests

def test_a_percent_encoded_service_does_not_get_a_read_token_past_the_write_gate(hub, trading):
    """The gate used to look for the literal `git-receive-pack` in the raw query string."""
    tok = hub.registry.mint("trading", "reader", "read")
    req = urllib.request.Request(
        f"{hub.url}/trading.git/info/refs?service=git-receive%2Dpack",
        headers={"Authorization": "Basic " + base64.b64encode(
            f"reader:{tok}".encode()).decode()})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 403
    assert "read access" in json.loads(e.value.read())["error"]


def test_an_absurd_invite_lifetime_is_a_400_not_a_dropped_connection(hub, trading):
    status, body = api(hub, "/trading/invite",
                       {"name": "maria", "role": "write", "days": 999999999999},
                       ("seb", trading["admin"]))
    assert status == 400
    assert "between 1 and 365" in body["error"]


# ---------------------------------------------------------------- signed invites

from conftest import commit_signed, make_key, pub_line
from knoten import identity as C
from knoten.core import GraphError
from knoten.identity import INVITE_NS, sign


@pytest.fixture
def signed_trading(hub, trading, keys_dir):
    """`trading` bootstrapped: contributors.yaml lists seb as admin, pushed signed."""
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
    assert "invites must carry the admin's signature" in body["error"]


def test_a_stolen_admin_token_cannot_mint_an_invite(hub, signed_trading, keys_dir):
    """The token says who is connecting; the key says who is authorising."""
    eve = make_key(keys_dir, "eve")
    status, body = api(hub, "/trading/invite", invite_body(eve, "test", "maria", "write"),
                       ("seb", signed_trading["admin"]))
    assert status == 403
    assert "your own signing key" in body["error"]


def test_an_invite_whose_blob_disagrees_with_the_request_is_refused(hub, signed_trading):
    """The signed bytes say maria/write; the request says maria/admin."""
    body = invite_body(signed_trading["seb_key"], "test", "maria", "write")
    body["role"] = "admin"
    status, got = api(hub, "/trading/invite", body, ("seb", signed_trading["admin"]))
    assert status == 400
    assert "different name, role or graph" in got["error"]


# ---------------------------------------------------------------- signed invites: review fixes

def test_a_revoked_admins_earlier_invite_is_refused_at_join_not_only_at_the_gate(hub, signed_trading):
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
    # Not "is this token refused" -- there must be no token in the reply at all.
    assert "token" not in joined


@pytest.mark.parametrize("blob, sig", [
    pytest.param("x" * 4097, "", id="blob-over-4096-bytes"),
    pytest.param("", "y" * 8193, id="sig-over-8192-bytes"),
    # 2049 two-byte characters is 4098 BYTES: over the cap, though under it counted as
    # code points, and bytes are what gets stored and signed over.
    pytest.param("é" * 2049, "", id="blob-over-4096-bytes-in-two-byte-characters"),
])
def test_invite_fields_over_the_size_cap_are_refused(hub, trading, blob, sig):
    status, body = api(hub, "/trading/invite",
                       {"name": "maria", "role": "write", "blob": blob, "sig": sig},
                       ("seb", trading["admin"]))
    assert status == 400
    assert "too large" in body["error"]


def test_an_invite_issued_before_the_graph_was_signed_is_refused_at_join(hub, trading, keys_dir):
    status, got = api(hub, "/trading/invite", {"name": "maria", "role": "write"},
                      ("seb", trading["admin"]))
    assert status == 200
    work = trading["work"]
    seb = make_key(keys_dir, "seb")
    C.dump(work, {"seb": {"key": pub_line(seb), "role": "admin"}})
    commit_signed(work, "seb signs the graph after the fact", seb)
    r = git("push", "-q", "origin", "master", cwd=work)
    assert r.returncode == 0, r.stderr
    status, joined = api(hub, "/trading/join", {"code": got["code"]})
    assert status == 400
    assert "issued before this graph was signed" in joined["error"]


# ------------------------------------------- who may lay down the first constitution

def test_a_write_token_cannot_bootstrap_a_hosted_graphs_contributors_file(hub, trading,
                                                                          tmp_path, keys_dir):
    tok = hub.registry.mint("trading", "maria", "write")
    dest = tmp_path / "maria"
    git("clone", "-q", clone_url(hub, "trading", "maria", tok), str(dest), cwd=tmp_path)
    git("config", "user.email", "m@m.m", cwd=dest); git("config", "user.name", "m", cwd=dest)
    maria = make_key(keys_dir, "maria")
    C.dump(dest, {"maria": {"key": pub_line(maria), "role": "admin"}})
    commit_signed(dest, "maria writes herself a constitution", maria)
    r = git("push", "origin", "master", cwd=dest)
    assert r.returncode != 0
    assert "only the graph's admin token may bootstrap" in r.stderr
    assert hub.registry.head_graph("trading")[0] is None, "the graph is still unsigned"


def test_a_bootstrap_may_not_bundle_anything_else(hub, trading, keys_dir):
    """Same restriction a join carries."""
    work = trading["work"]
    seb = make_key(keys_dir, "seb")
    C.dump(work, {"seb": {"key": pub_line(seb), "role": "admin"}})
    (work / "nodes" / "hyp-extra.md").write_text(
        "---\nid: hyp-extra\ntype: hypothesis\nstatus: open\n---\n\n# extra\n",
        encoding="utf-8")
    commit_signed(work, "seb signs the graph and slips a node in", seb)
    r = git("push", "origin", "master", cwd=work)
    assert r.returncode != 0
    assert "may change nothing else" in r.stderr
    assert hub.registry.head_graph("trading")[0] is None, "the graph is still unsigned"


def test_a_write_token_cannot_plant_a_second_constitution_through_the_hub(hub, signed_trading,
                                                                         tmp_path, keys_dir):
    work, seb = signed_trading["work"], signed_trading["seb_key"]
    maria_key = make_key(keys_dir, "maria")
    contribs = C.load(work)
    contribs["maria"] = {"key": pub_line(maria_key), "role": "write"}
    C.dump(work, contribs)
    commit_signed(work, "seb adds maria", seb)
    assert git("push", "-q", "origin", "master", cwd=work).returncode == 0
    tok = hub.registry.mint("trading", "maria", "write")
    dest = tmp_path / "maria"
    git("clone", "-q", clone_url(hub, "trading", "maria", tok), str(dest), cwd=tmp_path)
    git("config", "user.email", "m@m.m", cwd=dest); git("config", "user.name", "m", cwd=dest)
    (dest / "mine").mkdir()
    C.dump(dest / "mine", {"maria": {"key": pub_line(maria_key), "role": "admin"}})
    commit_signed(dest, "maria plants a constitution of her own", maria_key)
    r = git("push", "origin", "master", cwd=dest)
    assert r.returncode != 0
    assert "starts a second contributors.yaml" in r.stderr
    assert set(hub.registry.head_graph("trading")[0]) == {"seb", "maria"}
    status, got = api(hub, "/trading/invite", invite_body(seb, "test", "friend", "write"),
                      ("seb", signed_trading["admin"]))
    assert status == 200, got
    assert got["code"], "the admin's own route stopped working"


def test_a_graph_whose_nodes_are_gone_is_broken_not_unsigned(hub, signed_trading):
    """An admin may retire their graph's contents; the gate allows exactly that."""
    work = signed_trading["work"]
    git("rm", "-rq", "nodes", cwd=work)
    commit_signed(work, "seb retires the graph", signed_trading["seb_key"])
    assert git("push", "-q", "origin", "master", cwd=work).returncode == 0
    with pytest.raises(GraphError, match="but no graph"):
        hub.registry.head_graph("trading")
    status, body = api(hub, "/trading/invite", {"name": "maria", "role": "write"},
                       ("seb", signed_trading["admin"]))
    assert status == 400 and "code" not in body
    # The refusal reaches an ordinary contributor. The server's own data directory is not
    # theirs to learn from it.
    assert "--git-dir" not in body["error"]
    assert str(hub.data) not in body["error"]


# ---------------------------------------------------------------- the invite list

# ---------------------------------------------------------------- the access log

# ---------------------------------------------------------------- the push itself

def test_a_token_revoked_between_the_advertisement_and_the_push_is_refused(hub, trading,
                                                                            tmp_path):
    """Every request is authenticated on its own."""
    tok = hub.registry.mint("trading", "maria", "write")
    dest = tmp_path / "maria"
    git("clone", "-q", clone_url(hub, "trading", "maria", tok), str(dest), cwd=tmp_path)
    git("config", "user.email", "m@m.m", cwd=dest); git("config", "user.name", "m", cwd=dest)
    commit_node(dest, "hyp-m.md", "---\nid: hyp-m\ntype: hypothesis\nstatus: open\n---\n\n# m\n")
    hub.registry.revoke("trading", "maria")
    r = git("push", "origin", "master", cwd=dest)
    assert auth_refused(r), r.stderr
    assert "hyp-m" not in git("log", "--oneline", cwd=hub.registry.repo("trading")).stdout


# ---------------------------------------------------------------- the gate stays installed

@pytest.fixture
def hooks_path_hub(tmp_path, monkeypatch):
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
    """A zero-byte `owner` file is what a crash between the create and the write leaves."""
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


def test_a_push_the_gate_refused_is_not_logged_as_a_200(hub, trading, capfd):
    work = trading["work"]
    commit_node(work, "hyp-x.md", ALIVE_NO_GATE)
    capfd.readouterr()
    r = git("push", "origin", "master", cwd=work)
    assert r.returncode != 0
    err = capfd.readouterr().err
    pushes = [l for l in err.splitlines() if "git-receive-pack" in l]
    assert pushes and pushes[-1].endswith(" refused"), err


# ---------------------------------------------------------------- what the comments claim

def test_a_path_that_reads_like_a_refusal_does_not_log_as_one(hub, trading, rules_yaml,
                                                              capfd):
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



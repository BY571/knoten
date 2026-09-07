import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

RULES = """\
name: test
rules:
  - id: live-claims-must-cite-their-gates
    when_status: alive
    when_type: hypothesis
    require_edge: kn:survivedGate
    message: An unchallenged claim is not a finding, it is a hope.
"""


@pytest.fixture
def rules_yaml():
    return RULES


@pytest.fixture
def graph(tmp_path):
    """A minimal graph on disk. Returns a helper to add nodes and set rules."""

    class Graph:
        root = tmp_path

        def rules(self, text=RULES):
            (tmp_path / "graph.yaml").write_text(text, encoding="utf-8")
            return self

        def node(self, nid, frontmatter, body="# body\n"):
            (tmp_path / "nodes").mkdir(exist_ok=True)
            (tmp_path / "nodes" / f"{nid}.md").write_text(
                f"---\n{frontmatter.strip()}\n---\n\n{body}", encoding="utf-8"
            )
            return self

        def attachment(self, nid, name, content="x"):
            d = tmp_path / "attachments" / nid
            d.mkdir(parents=True, exist_ok=True)
            (d / name).write_text(content, encoding="utf-8")
            return self

        def read(self, nid):
            return (tmp_path / "nodes" / f"{nid}.md").read_text(encoding="utf-8")

    (tmp_path / "nodes").mkdir()
    return Graph().rules()


COMPRESSIBLE_YAML = """\
name: t
statuses: [open, alive, dead, retracted, superseded, active]
node_types: [question, experiment, finding, gate, hypothesis, note, principle, source]
tags: [lr, batch]
rules: []
"""


def compressible_graph(graph, n=2, gates=2, tag=None, start=1, through=None):
    """One question, `gates` gates, `n` alive findings under it, each surviving one gate
    in turn, stamped a day apart. `through` roots them via an experiment; `tag` tags them,
    which is the other thing a cluster forms on."""
    graph.rules(COMPRESSIBLE_YAML)
    graph.node("question-q", "id: question-q\ntype: question\nstatus: open", "# Q\n")
    names = [f"gate-{c}" for c in "abcdefgh"[:gates]]
    for g in names:
        graph.node(g, f"id: {g}\ntype: gate\nstatus: active", f"# {g}\n")
    if through:
        graph.node(through, f"id: {through}\ntype: experiment\nstatus: alive\nlinks:\n"
                            "  - {rel: prov:wasDerivedFrom, to: question-q}", "# E\n")
    for i in range(start, start + n):
        gate = names[(i - start) % len(names)] if names else None
        graph.node(f"finding-{i}",
                   f"id: finding-{i}\ntype: finding\nstatus: alive\n"
                   f"created: 2026-01-{i + 1:02d}\n"
                   + (f"tags: [{tag}]\n" if tag else "")
                   + "links:\n"
                   + f"  - {{rel: prov:wasDerivedFrom, to: {through or 'question-q'}}}\n"
                   + (f"  - {{rel: kn:survivedGate, to: {gate}}}\n" if gate else ""),
                   f"# {i}\n\nThe claim {i}.\n")
    return graph


GIT_ISOLATION = {
    # The developer's own git config must not reach these tests: a global credential
    # helper would answer the server's 401 with cached credentials and pass a test that
    # should fail, and a global signing setup would make commits in fixtures fail.
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
}


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch):
    """The developer's own ~/.gitconfig, out of every test, in-process calls included."""
    for k, v in GIT_ISOLATION.items():
        monkeypatch.setenv(k, v)


def git(*args, cwd, env=None):
    """Every test file drives real git."""
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          env={**os.environ, **GIT_ISOLATION, **(env or {})})


def commit_node(work, name, text):
    """Write one node into a graph and commit it. The unit of almost every push here."""
    (work / "nodes" / name).write_text(text, encoding="utf-8")
    git("add", "-A", cwd=work)
    git("commit", "-qm", name, cwd=work)


@pytest.fixture
def hub(tmp_path, monkeypatch):
    """A real knoten server on a random localhost port, and an isolated credential store."""
    import threading
    from types import SimpleNamespace
    from knoten.registry import Registry
    from knoten.serve import make_server
    for k, v in GIT_ISOLATION.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("KNOTEN_CREDENTIALS", str(tmp_path / "credentials"))
    reg = Registry(tmp_path / "data")
    srv = make_server(reg, "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address
    yield SimpleNamespace(url=f"http://{host}:{port}", registry=reg, server=srv,
                          secret=reg.owner_secret(), data=tmp_path / "data")
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def local_graph(tmp_path, rules_yaml):
    root = tmp_path / "admin" / "trading"
    (root / "nodes").mkdir(parents=True)
    (root / "graph.yaml").write_text(rules_yaml, encoding="utf-8")
    (root / "nodes" / "hyp-ok.md").write_text(
        "---\nid: hyp-ok\ntype: hypothesis\nstatus: open\n---\n\n# a claim\n", encoding="utf-8")
    for cmd in (["init", "-q", "-b", "master"], ["config", "user.email", "t@t.t"],
                ["config", "user.name", "t"], ["add", "-A"], ["commit", "-qm", "seed"]):
        assert git(*cmd, cwd=root).returncode == 0, cmd
    return root


# ---------------------------------------------------------------- phase 2: signing

@pytest.fixture(autouse=True)
def keys_dir(tmp_path, monkeypatch):
    """Every signing key a test makes lands under tmp_path."""
    d = tmp_path / "keys"
    monkeypatch.setenv("KNOTEN_KEYS", str(d))
    return d


def make_key(directory, name):
    """A real ed25519 keypair at directory/name, via ssh-keygen, no passphrase."""
    import subprocess
    directory.mkdir(parents=True, exist_ok=True)
    priv = directory / name
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(priv)],
                   check=True, capture_output=True)
    return priv


@pytest.fixture
def keypair(keys_dir):
    """The admin's key, where `knoten` itself would put it."""
    return make_key(keys_dir, "seb")


def pub_line(priv):
    """`ssh-ed25519 AAAA...`: the two fields git and allowed_signers use."""
    return " ".join(priv.with_suffix(".pub").read_text(encoding="utf-8").split()[:2])


def commit_signed(repo, message, priv):
    import subprocess
    env = {**os.environ, **GIT_ISOLATION}
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, env=env, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "gpg.format=ssh",
                    "-c", f"user.signingkey={priv}", "-c", "commit.gpgsign=true",
                    "commit", "-q", "-m", message], check=True, env=env, capture_output=True)
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, env=env,
                          capture_output=True, text=True).stdout.strip()

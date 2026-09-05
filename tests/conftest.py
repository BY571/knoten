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


GIT_ISOLATION = {
    # The developer's own git config must not reach these tests: a global credential
    # helper would answer the server's 401 with cached credentials and pass a test that
    # should fail, and a global signing setup would make commits in fixtures fail.
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
}


def git(*args, cwd, env=None):
    """Every test file drives real git. One spelling of the call, so the isolation above
    cannot be present in two files and missing in the third -- which is what happened:
    the server-hook tests ran against the developer's own global config."""
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          env={**os.environ, **GIT_ISOLATION, **(env or {})})


def commit_node(work, name, text):
    """Write one node into a graph and commit it. The unit of almost every push here."""
    (work / "nodes" / name).write_text(text, encoding="utf-8")
    git("add", "-A", cwd=work)
    git("commit", "-qm", name, cwd=work)


@pytest.fixture
def hub(tmp_path, monkeypatch):
    """A real knoten server on a random localhost port, and an isolated credential store.

    Real because the failures worth catching live in the seam between git's client, the
    HTTP layer and git-http-backend, and none of them are visible to a mock.
    """
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
    yield SimpleNamespace(url=f"http://{host}:{port}", registry=reg,
                          secret=reg.owner_secret(), data=tmp_path / "data")
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def local_graph(tmp_path, rules_yaml):
    """A git repo whose root is a graph with one committed node: what a user has on disk
    the moment they decide to share it.

    Nested under "admin/", not directly in tmp_path: a friend joining a graph named
    "trading" clones to tmp_path/"trading" by default, and that must never collide with
    the admin's own on-disk checkout of the same graph, which happens to share this
    tmp_path in tests."""
    root = tmp_path / "admin" / "trading"
    (root / "nodes").mkdir(parents=True)
    (root / "graph.yaml").write_text(rules_yaml, encoding="utf-8")
    (root / "nodes" / "hyp-ok.md").write_text(
        "---\nid: hyp-ok\ntype: hypothesis\nstatus: open\n---\n\n# a claim\n", encoding="utf-8")
    for cmd in (["init", "-q", "-b", "master"], ["config", "user.email", "t@t.t"],
                ["config", "user.name", "t"], ["add", "-A"], ["commit", "-qm", "seed"]):
        assert git(*cmd, cwd=root).returncode == 0, cmd
    return root

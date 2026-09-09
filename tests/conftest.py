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
    # The developer's own git config must not reach these tests: a global signing setup
    # would make commits in fixtures fail, and a global hooks path would hide the hook.
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

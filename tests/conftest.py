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

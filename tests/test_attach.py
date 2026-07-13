"""Attachments rewrite the file on disk. That makes them the one place knoten can
destroy a node the user hand-wrote."""
import yaml

import pytest

from knoten.cli import attach, detach
from knoten.core import GraphError, load

FM = """\
id: hyp-x
type: hypothesis
status: dead
results:
  acc: 0.741
  n_independent: 1319
"""


def frontmatter_of(text):
    return yaml.safe_load(text.split("---")[1])


def test_two_attaches_do_not_corrupt_the_frontmatter(graph, tmp_path):
    """Finding 5: the second attach orphaned the previous list item, leaving a
    stray `- a.txt` INSIDE the preceding block (`results:`). It accumulated with
    every attach and left the file as invalid YAML."""
    graph.node("hyp-x", FM)
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("a", encoding="utf-8")
    b.write_text("b", encoding="utf-8")

    attach(graph.root, "hyp-x", [str(a)])
    attach(graph.root, "hyp-x", [str(b)])

    fm = frontmatter_of(graph.read("hyp-x"))
    assert fm["attachments"] == ["a.txt", "b.txt"]
    assert fm["results"] == {"acc": 0.741, "n_independent": 1319}


def test_attach_preserves_hand_written_frontmatter_verbatim(graph, tmp_path):
    """We surgically edit the attachments block rather than re-dumping the YAML,
    so comments and formatting the human wrote survive."""
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead  # still dead\nresults:\n  acc: 0.741")
    f = tmp_path / "a.txt"
    f.write_text("a", encoding="utf-8")

    attach(graph.root, "hyp-x", [str(f)])

    assert "status: dead  # still dead" in graph.read("hyp-x")


def test_detach_removes_the_file_and_the_listing(graph, tmp_path):
    graph.node("hyp-x", FM)
    f = tmp_path / "a.txt"
    f.write_text("a", encoding="utf-8")
    attach(graph.root, "hyp-x", [str(f)])

    detach(graph.root, "hyp-x", "a.txt")

    assert "attachments" not in frontmatter_of(graph.read("hyp-x"))
    assert not (graph.root / "attachments" / "hyp-x" / "a.txt").exists()


def test_detaching_the_last_image_removes_the_empty_section(graph, tmp_path):
    graph.node("hyp-x", FM)
    img = tmp_path / "plot.png"
    img.write_bytes(b"\x89PNG")
    attach(graph.root, "hyp-x", [str(img)])
    assert "## Attachments" in graph.read("hyp-x")

    detach(graph.root, "hyp-x", "plot.png")

    assert "## Attachments" not in graph.read("hyp-x")


def test_images_are_embedded_once_even_if_reattached(graph, tmp_path):
    graph.node("hyp-x", FM)
    img = tmp_path / "plot.png"
    img.write_bytes(b"\x89PNG")

    attach(graph.root, "hyp-x", [str(img)])
    attach(graph.root, "hyp-x", [str(img)])

    assert graph.read("hyp-x").count("![plot.png]") == 1
    assert frontmatter_of(graph.read("hyp-x"))["attachments"] == ["plot.png"]


def test_attach_to_a_node_without_frontmatter_fails_cleanly(graph, tmp_path):
    """Finding 6: this crashed with a bare AttributeError on `m.group(1)`."""
    (graph.root / "nodes" / "nofm.md").write_text("# just prose\n", encoding="utf-8")
    f = tmp_path / "a.txt"
    f.write_text("a", encoding="utf-8")

    with pytest.raises(GraphError, match="frontmatter"):
        attach(graph.root, "nofm", [str(f)])

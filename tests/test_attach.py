"""Attachments rewrite the file on disk. That makes them the one place knoten can
destroy a node the user hand-wrote."""
import yaml

import pytest

from knoten.cli import attach, detach
from knoten.core import GraphError, load
from knoten.validate import check

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

def test_attach_survives_a_zero_indent_attachments_list(graph, tmp_path):
    """`yaml.dump` emits list items at ZERO indent by default. A dropper that requires
    leading whitespace orphans them into the block above, leaving the node UNPARSEABLE —
    which takes the whole graph down with it, since load() raises rather than skips."""
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead\nattachments:\n- old.png")
    graph.attachment("hyp-x", "old.png")
    new = tmp_path / "new.txt"
    new.write_text("x", encoding="utf-8")

    attach(graph.root, "hyp-x", [str(new)])

    assert frontmatter_of(graph.read("hyp-x"))["attachments"] == ["new.txt", "old.png"]
    assert load(graph.root)["hyp-x"].attachments == ["new.txt", "old.png"]


@pytest.mark.parametrize("name", ["plot #1.png", "run: final.png", "123"])
def test_a_filename_needing_quotes_gets_them(graph, tmp_path, name):
    """Written raw, `plot #1.png` becomes the YAML comment `plot`, `run: final.png` a
    mapping, and `123` an int. All are legal filenames."""
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead")
    f = tmp_path / name
    f.write_text("x", encoding="utf-8")

    attach(graph.root, "hyp-x", [str(f)])

    assert frontmatter_of(graph.read("hyp-x"))["attachments"] == [name]
    assert load(graph.root)["hyp-x"].attachments == [name]
    assert check(load(graph.root), graph.root) == []


def test_two_files_with_the_same_basename_are_refused(graph, tmp_path):
    """Both land on attachments/<id>/plot.png — the second silently overwrote the first
    while the caller was told two files were attached."""
    a, b = tmp_path / "a" / "plot.png", tmp_path / "b" / "plot.png"
    for f in (a, b):
        f.parent.mkdir()
        f.write_text(f.parent.name, encoding="utf-8")
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead")

    with pytest.raises(GraphError, match="plot.png"):
        attach(graph.root, "hyp-x", [str(a), str(b)])


def test_attaching_a_directory_is_refused_before_anything_is_copied(graph, tmp_path):
    """`exists()` is true for a directory, so the pre-check passed and copy2 died halfway,
    leaving an orphan in attachments/."""
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead")
    good = tmp_path / "good.py"
    good.write_text("x", encoding="utf-8")
    (tmp_path / "adir").mkdir()

    with pytest.raises(GraphError, match="adir"):
        attach(graph.root, "hyp-x", [str(good), str(tmp_path / "adir")])

    assert not (graph.root / "attachments" / "hyp-x" / "good.py").exists()


@pytest.mark.parametrize("nid", ["../../outside", "sub/dir", "UPPER"])
def test_attach_and_detach_refuse_an_id_that_escapes_the_graph(graph, tmp_path, nid):
    """The MCP surface guarded the id because it comes from an LLM; the CLI did not,
    because a human types it. So `knoten attach ../../x f` wrote OUTSIDE the graph, and
    `knoten detach ../../x f` DELETED a file outside it. Every id -> path conversion now
    goes through core.node_path."""
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")

    with pytest.raises(GraphError, match="valid node id"):
        attach(graph.root, nid, [str(f)])
    with pytest.raises(GraphError, match="valid node id"):
        detach(graph.root, nid, "a.txt")

    assert not (graph.root.parent / "attachments").exists()

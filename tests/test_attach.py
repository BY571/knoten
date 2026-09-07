"""Attachments rewrite the file on disk."""
import yaml

import pytest

from knoten import attachments
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
    graph.node("hyp-x", FM)
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("a", encoding="utf-8")
    b.write_text("b", encoding="utf-8")
    attach(graph.root, "hyp-x", [str(a)])
    attach(graph.root, "hyp-x", [str(b)])
    fm = frontmatter_of(graph.read("hyp-x"))
    assert fm["attachments"] == ["a.txt", "b.txt"]
    assert fm["results"] == {"acc": 0.741, "n_independent": 1319}


def test_detach_removes_the_file_and_the_listing(graph, tmp_path):
    graph.node("hyp-x", FM)
    f = tmp_path / "a.txt"
    f.write_text("a", encoding="utf-8")
    attach(graph.root, "hyp-x", [str(f)])
    detach(graph.root, "hyp-x", "a.txt")
    assert "attachments" not in frontmatter_of(graph.read("hyp-x"))
    assert not (graph.root / "attachments" / "hyp-x" / "a.txt").exists()


def test_images_are_embedded_once_even_if_reattached(graph, tmp_path):
    graph.node("hyp-x", FM)
    img = tmp_path / "plot.png"
    img.write_bytes(b"\x89PNG")
    attach(graph.root, "hyp-x", [str(img)])
    attach(graph.root, "hyp-x", [str(img)])
    assert graph.read("hyp-x").count("![plot.png]") == 1
    assert frontmatter_of(graph.read("hyp-x"))["attachments"] == ["plot.png"]


def test_attach_survives_a_zero_indent_attachments_list(graph, tmp_path):
    """`yaml.dump` emits list items at ZERO indent by default."""
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead\nattachments:\n- old.png")
    graph.attachment("hyp-x", "old.png")
    new = tmp_path / "new.txt"
    new.write_text("x", encoding="utf-8")
    attach(graph.root, "hyp-x", [str(new)])
    assert frontmatter_of(graph.read("hyp-x"))["attachments"] == ["new.txt", "old.png"]
    assert load(graph.root)["hyp-x"].attachments == ["new.txt", "old.png"]


@pytest.mark.parametrize("name", ["plot #1.png", "run: final.png", "123"])
def test_a_filename_needing_quotes_gets_them(graph, tmp_path, name):
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead")
    f = tmp_path / name
    f.write_text("x", encoding="utf-8")
    attach(graph.root, "hyp-x", [str(f)])
    assert frontmatter_of(graph.read("hyp-x"))["attachments"] == [name]
    assert load(graph.root)["hyp-x"].attachments == [name]
    assert check(load(graph.root), graph.root) == []


def test_two_files_with_the_same_basename_are_refused(graph, tmp_path):
    a, b = tmp_path / "a" / "plot.png", tmp_path / "b" / "plot.png"
    for f in (a, b):
        f.parent.mkdir()
        f.write_text(f.parent.name, encoding="utf-8")
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead")
    with pytest.raises(GraphError, match="plot.png"):
        attach(graph.root, "hyp-x", [str(a), str(b)])


def test_attaching_a_directory_is_refused_before_anything_is_copied(graph, tmp_path):
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead")
    good = tmp_path / "good.py"
    good.write_text("x", encoding="utf-8")
    (tmp_path / "adir").mkdir()
    with pytest.raises(GraphError, match="adir"):
        attach(graph.root, "hyp-x", [str(good), str(tmp_path / "adir")])
    assert not (graph.root / "attachments" / "hyp-x" / "good.py").exists()


@pytest.mark.parametrize("nid", ["../../outside", "sub/dir", "UPPER", "", "."])
def test_attach_and_detach_refuse_an_id_that_escapes_the_graph(graph, tmp_path, nid):
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(GraphError, match="valid node id"):
        attach(graph.root, nid, [str(f)])
    with pytest.raises(GraphError, match="valid node id"):
        detach(graph.root, nid, "a.txt")
    assert not (graph.root.parent / "attachments").exists()


# ------------------------------------------------- guards that warn rather than refuse

def test_a_symlink_is_attached_by_content_and_flagged(graph, tmp_path):
    graph.node("hyp-x", FM)
    real = tmp_path / "real.py"; real.write_text("secret", encoding="utf-8")
    link = tmp_path / "link.py"; link.symlink_to(real)
    res = attachments.attach(graph.root, "hyp-x", [str(link)])
    assert res.added == ["link.py"]
    assert "symlink" in " ".join(res.warnings)



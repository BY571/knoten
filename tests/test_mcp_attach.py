"""Agents could read attachments and never write them, so "the graph writes itself"
quietly stopped at prose. An agent that runs an experiment produces a script and a plot
and had no way to put either in the graph."""
import pytest


pytest.importorskip("knoten.mcp_server",
                    reason="the agent server needs mcp>=2; the rest of the suite "
                           "does not")
from knoten import mcp_server as M
from knoten.core import load
from knoten.validate import check


@pytest.fixture(autouse=True)
def cwd(graph, monkeypatch):
    monkeypatch.chdir(graph.root)
    monkeypatch.delenv("KNOTEN_GRAPH", raising=False)
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead")
    return graph


def attach(nid, files):
    return M.knoten_attach(id=nid, files=[str(f) for f in files])


def test_an_agent_can_attach_the_script_that_killed_it(cwd, tmp_path):
    script = tmp_path / "self_consistency.py"
    script.write_text("print('the kill')\n", encoding="utf-8")

    res = attach("hyp-x", [script])

    assert res["status"] == "ATTACHED"
    assert res["attached"] == ["attachments/hyp-x/self_consistency.py"]
    assert (cwd.root / "attachments" / "hyp-x" / "self_consistency.py").exists()
    assert load(cwd.root)["hyp-x"].attachments == ["self_consistency.py"]


def test_the_graph_is_still_valid_after_attaching(cwd, tmp_path):
    """A listed attachment that isn't on disk is a violation. Attaching must not
    create one."""
    script = tmp_path / "x.py"
    script.write_text("x\n", encoding="utf-8")

    attach("hyp-x", [script])

    assert check(load(cwd.root), cwd.root) == []


def test_an_image_is_embedded_so_it_renders_on_github(cwd, tmp_path):
    img = tmp_path / "accuracy_vs_budget.png"
    img.write_bytes(b"\x89PNG")

    res = attach("hyp-x", [img])

    assert res["embedded"] == ["accuracy_vs_budget.png"]
    assert "![accuracy_vs_budget.png]" in cwd.read("hyp-x")


def test_two_attaches_do_not_corrupt_the_node(cwd, tmp_path):
    a, b = tmp_path / "a.py", tmp_path / "b.py"
    a.write_text("a", encoding="utf-8")
    b.write_text("b", encoding="utf-8")

    attach("hyp-x", [a])
    attach("hyp-x", [b])

    assert load(cwd.root)["hyp-x"].attachments == ["a.py", "b.py"]
    assert check(load(cwd.root), cwd.root) == []


def test_attaching_to_an_unknown_node_is_rejected(cwd, tmp_path):
    f = tmp_path / "x.py"
    f.write_text("x", encoding="utf-8")

    res = attach("hyp-nope", [f])

    assert res["status"] == "REJECTED"
    assert "hyp-nope" in res["reason"]


@pytest.mark.parametrize("nid", ["../evil", "sub/dir", ""])
def test_attach_rejects_an_id_that_is_not_kebab_case(cwd, tmp_path, nid):
    """This test used to pass vacuously — those ids are already rejected by the node
    existence check, so it would still have passed with the ID_RE guard deleted. Plant a
    node file at the traversal target so ONLY the guard can stop it."""
    f = tmp_path / "x.py"
    f.write_text("x", encoding="utf-8")
    victim = cwd.root / "nodes" / "evil.md"
    victim.write_text("---\nid: evil\ntype: hypothesis\nstatus: dead\n---\n# x\n",
                      encoding="utf-8")

    res = attach(nid, [f])

    assert res["status"] == "REJECTED"
    assert "id" in res["reason"].lower()
    assert not (cwd.root / "nodes" / "attachments").exists()


def test_a_directory_is_refused_as_a_json_error_not_a_traceback(cwd, tmp_path):
    """The error guard only wrapped _root()/load(). A directory raised IsADirectoryError
    straight out of the tool — an MCP server must answer, not explode."""
    d = tmp_path / "adir"
    d.mkdir()

    res = attach("hyp-x", [d])

    assert res["status"] == "REJECTED"
    assert "adir" in res["reason"]


def test_a_broken_graph_yaml_is_reported_as_an_error_not_a_traceback(cwd):
    """A typo'd rule key made knoten_validate raise GraphError out of the tool."""
    (cwd.root / "graph.yaml").write_text(
        "rules:\n  - id: r\n    when: {status: alive}\n", encoding="utf-8")

    res = M.knoten_validate()

    assert "error" in res
    assert "when" in res["error"]


def test_a_symlink_is_attached_by_content_and_flagged(cwd, tmp_path):
    real = tmp_path / "real.py"
    real.write_text("secret", encoding="utf-8")
    link = tmp_path / "link.py"
    link.symlink_to(real)

    res = attach("hyp-x", [link])

    assert res["status"] == "ATTACHED"
    assert "symlink" in " ".join(res["warnings"])


def test_a_missing_source_file_is_rejected_and_nothing_is_written(cwd, tmp_path):
    res = attach("hyp-x", [tmp_path / "ghost.py"])

    assert res["status"] == "REJECTED"
    assert "ghost.py" in res["reason"]
    assert load(cwd.root)["hyp-x"].attachments == []


def test_a_huge_file_is_flagged(cwd, tmp_path):
    """git is for code and plots, not datasets."""
    big = tmp_path / "train.parquet"
    big.write_bytes(b"0" * 1_200_000)

    res = attach("hyp-x", [big])

    assert res["status"] == "ATTACHED"
    assert "train.parquet" in " ".join(res["warnings"])

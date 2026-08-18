"""The CLI could read the graph but not write to it — commit and update existed only
on the MCP surface. An agent with a shell had to install an SDK to file a claim."""
import json

import pytest

from knoten.cli import main
from knoten.core import load

RULES = "name: t\nstatuses: [open, dead]\nnode_types: [hypothesis]\nrules: []\n"


@pytest.fixture
def open_node(graph, monkeypatch):
    """A single open hypothesis node, chdir'd into its graph. Shared setup for the
    update tests below."""
    graph.rules(RULES).node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: open", "# c\n")
    monkeypatch.chdir(graph.root)
    return graph


@pytest.fixture
def rejected_commit(graph, tmp_path, monkeypatch):
    """A commit that graph.yaml's rules will refuse — a bogus status. Returns the argv
    list so each test can append --json and read its own streams."""
    graph.rules("name: t\nstatuses: [open]\nnode_types: [hypothesis]\nrules: []\n")
    monkeypatch.chdir(graph.root)
    (tmp_path / "fm").write_text("type: hypothesis\nstatus: bogus", encoding="utf-8")
    (tmp_path / "body").write_text("# x\n", encoding="utf-8")
    return ["commit", "hyp-x", "--frontmatter", str(tmp_path / "fm"),
            "--body", str(tmp_path / "body")]


def test_commit_writes_a_node_from_files(graph, tmp_path, monkeypatch, capsys):
    graph.rules(RULES)
    monkeypatch.chdir(graph.root)
    (tmp_path / "fm").write_text("type: hypothesis\nstatus: open", encoding="utf-8")
    (tmp_path / "body").write_text("# A claim\n", encoding="utf-8")

    code = main(["commit", "hyp-x", "--frontmatter", str(tmp_path / "fm"),
                 "--body", str(tmp_path / "body")])

    assert code == 0
    assert load(graph.root)["hyp-x"].status == "open"


def test_commit_reads_frontmatter_and_body_from_stdin(graph, monkeypatch, capsys):
    graph.rules(RULES)
    monkeypatch.chdir(graph.root)
    reads = iter(["type: hypothesis\nstatus: open", "# A claim\n"])
    monkeypatch.setattr("sys.stdin.read", lambda: next(reads))

    code = main(["commit", "hyp-x", "--frontmatter", "-", "--body", "-"])

    assert code == 0
    assert load(graph.root)["hyp-x"].status == "open"


def test_a_rejected_commit_exits_nonzero(graph, rejected_commit):
    code = main(rejected_commit)

    assert code == 1
    assert not (graph.root / "nodes" / "hyp-x.md").exists()


def test_a_rejected_commit_reason_is_on_stderr(rejected_commit, capsys):
    main(rejected_commit)
    out, err = capsys.readouterr()

    assert out == ""
    assert "bogus" in err


def test_a_rejected_commit_json_payload_is_on_stdout(rejected_commit, capsys):
    code = main(rejected_commit + ["--json"])
    out, err = capsys.readouterr()

    assert code == 1
    assert err == ""
    assert json.loads(out)["status"] == "REJECTED"


def test_update_moves_the_status(open_node):
    assert main(["update", "hyp-x", "--status", "dead"]) == 0
    assert load(open_node.root)["hyp-x"].status == "dead"


def test_update_records_results_and_links(graph, monkeypatch):
    graph.rules("name: t\nstatuses: [open, dead]\nnode_types: [hypothesis, method]\n"
                "rules: []\n")
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: open", "# c\n")
    graph.node("method-x", "id: method-x\ntype: method\nstatus: open", "# gate\n")
    monkeypatch.chdir(graph.root)

    code = main(["update", "hyp-x", "--result", "acc=0.7", "--result", "note=fine",
                 "--link", "kn:killedByGate=method-x"])

    assert code == 0
    node = load(graph.root)["hyp-x"]
    assert node.results == {"acc": 0.7, "note": "fine"}
    assert node.links == [{"rel": "kn:killedByGate", "to": "method-x"}]


def test_update_appends_from_a_file(open_node, tmp_path):
    (tmp_path / "note").write_text("## Why it died\nnoise\n", encoding="utf-8")

    code = main(["update", "hyp-x", "--status", "dead", "--append", str(tmp_path / "note")])

    assert code == 0
    assert "Why it died" in (open_node.root / "nodes" / "hyp-x.md").read_text(encoding="utf-8")


def test_update_reports_a_refusal_without_a_traceback(open_node, capsys):
    assert main(["update", "hyp-x", "--status", "bogus"]) == 1
    assert "Traceback" not in capsys.readouterr().err


def test_a_refused_update_json_payload_is_on_stdout(open_node, capsys):
    code = main(["update", "hyp-x", "--status", "bogus", "--json"])
    out, err = capsys.readouterr()

    assert code == 1
    assert err == ""
    assert json.loads(out)["status"] == "REJECTED"


@pytest.mark.parametrize("flag,bad", [("--result", "acc"), ("--link", "method-x")])
def test_a_malformed_kv_flag_is_a_graph_error_not_a_crash(open_node, flag, bad):
    assert main(["update", "hyp-x", flag, bad]) == 1


def test_a_typod_input_path_is_an_error_not_a_traceback(graph, monkeypatch, capsys):
    """`_read()` raises FileNotFoundError for a bad --frontmatter/--body/--append path.
    A typo'd path is ordinary user error, not something `main()` should let escape as a
    traceback with no exit-code contract. Same stream contract as `show`: prose to
    stderr, --json payload to stdout."""
    graph.rules(RULES)
    monkeypatch.chdir(graph.root)

    code = main(["commit", "hyp-x", "--frontmatter", "/nonexistent/fm",
                "--body", "/nonexistent/body"])
    out, err = capsys.readouterr()

    assert code == 1
    assert out == ""
    assert "Traceback" not in err
    assert err.startswith("knoten: ")

    code = main(["commit", "hyp-x", "--frontmatter", "/nonexistent/fm",
                "--body", "/nonexistent/body", "--json"])
    out, err = capsys.readouterr()

    assert code == 1
    assert err == ""
    assert json.loads(out)["error"]

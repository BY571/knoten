"""The CLI is the first thing a new user touches. It should never hand them a
traceback. `main()` returns an exit code; the console script exits with it."""
import pytest

from knoten.cli import main


def test_missing_argument_is_an_error_not_a_traceback(graph, monkeypatch, capsys):
    """`knoten query` with no term raised a raw IndexError at the user."""
    monkeypatch.chdir(graph.root)

    with pytest.raises(SystemExit) as e:
        main(["query"])

    assert e.value.code != 0
    assert "Traceback" not in capsys.readouterr().err


def test_help_works_outside_a_graph(tmp_path, monkeypatch, capsys):
    """`_root()` ran before dispatch, so `--help` died with 'no graph.yaml found'
    — exactly when a new user needs help most."""
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as e:
        main(["--help"])

    assert e.value.code == 0
    assert "knoten" in capsys.readouterr().out


def test_running_outside_a_graph_is_a_clean_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    assert main(["validate"]) == 1
    assert "graph.yaml" in capsys.readouterr().err


def test_path_reports_an_unknown_node_as_unknown(graph, monkeypatch, capsys):
    """A typo'd node name reported 'no path', which reads as 'they are
    unconnected' rather than 'that node does not exist'."""
    monkeypatch.chdir(graph.root)
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead")

    assert main(["path", "hyp-x", "hyp-typo"]) == 1
    assert "hyp-typo" in capsys.readouterr().err


def test_validate_returns_nonzero_on_violation(graph, monkeypatch):
    monkeypatch.chdir(graph.root)
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: alive")

    assert main(["validate"]) == 1


def test_validate_returns_zero_on_a_clean_graph(graph, monkeypatch):
    monkeypatch.chdir(graph.root)
    graph.node("method-gate", "id: method-gate\ntype: method")

    assert main(["validate"]) == 0


def test_a_broken_node_is_reported_not_crashed_on(graph, monkeypatch, capsys):
    monkeypatch.chdir(graph.root)
    (graph.root / "nodes" / "bad.md").write_text("---\nid: bad\n  oops:\n---\n", encoding="utf-8")

    assert main(["validate"]) == 1
    assert "bad.md" in capsys.readouterr().err


def test_init_creates_a_graph_that_validates(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["init", "my-topic"]) == 0

    monkeypatch.chdir(tmp_path / "my-topic")
    assert main(["validate"]) == 0


def test_init_refuses_a_name_that_escapes_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert main(["init", "../evil"]) == 1
    assert not (tmp_path.parent / "evil").exists()

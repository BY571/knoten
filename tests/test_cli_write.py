import json

import pytest
from conftest import compressible_graph

from knoten.cli import main
from knoten.core import load

RULES = "name: t\nstatuses: [open, dead]\nnode_types: [hypothesis]\nrules: []\n"


@pytest.fixture
def open_node(graph, monkeypatch):
    """A single open hypothesis node, chdir'd into its graph."""
    graph.rules(RULES).node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: open", "# c\n")
    monkeypatch.chdir(graph.root)
    return graph


@pytest.fixture
def rejected_commit(graph, tmp_path, monkeypatch):
    """A commit that graph.yaml's rules will refuse — a bogus status."""
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


def test_a_rejected_commit_json_payload_is_on_stdout(rejected_commit, capsys):
    code = main(rejected_commit + ["--json"])
    out, err = capsys.readouterr()
    assert code == 1
    assert err == ""
    assert json.loads(out)["status"] == "REJECTED"


def test_update_records_results_and_links(graph, monkeypatch):
    graph.rules("name: t\nstatuses: [open, dead]\nnode_types: [hypothesis, gate]\n"
                "rules: []\n")
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: open", "# c\n")
    graph.node("gate-x", "id: gate-x\ntype: gate\nstatus: open", "# gate\n")
    monkeypatch.chdir(graph.root)
    code = main(["update", "hyp-x", "--result", "acc=0.7", "--result", "note=fine",
                 "--link", "kn:killedByGate=gate-x"])
    assert code == 0
    node = load(graph.root)["hyp-x"]
    assert node.results == {"acc": 0.7, "note": "fine"}
    assert node.links == [{"rel": "kn:killedByGate", "to": "gate-x"}]


def test_update_reports_a_refusal_without_a_traceback(open_node, capsys):
    assert main(["update", "hyp-x", "--status", "bogus"]) == 1
    assert "Traceback" not in capsys.readouterr().err


def test_a_refused_update_json_payload_is_on_stdout(open_node, capsys):
    code = main(["update", "hyp-x", "--status", "bogus", "--json"])
    out, err = capsys.readouterr()
    assert code == 1
    assert err == ""
    assert json.loads(out)["status"] == "REJECTED"


def test_a_typod_input_path_is_an_error_not_a_traceback(graph, monkeypatch, capsys):
    """`_read()` raises FileNotFoundError for a bad --frontmatter/--body/--append path."""
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


def test_field_closes_a_node_the_graph_demands_a_cause_for(graph, monkeypatch, tmp_path):
    """Issue #12 end to end, through the surface an agent actually uses."""
    graph.rules("""\
name: t
statuses: [open, dead]
node_types: [hypothesis]
rules:
  - id: deaths-must-name-a-cause
    when_status: dead
    require_field_one_of: {cause: [no_signal, weak_baseline]}
    message: name the cause.
""").node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: open", "# c\n")
    monkeypatch.chdir(graph.root)
    (tmp_path / "app").write_text("## Why it died\nnoise\n", encoding="utf-8")

    code = main(["update", "hyp-x", "--status", "dead",
                 "--append", str(tmp_path / "app"), "--field", "cause=weak_baseline"])

    assert code == 0
    assert load(graph.root)["hyp-x"].frontmatter["cause"] == "weak_baseline"


NUMERIC_VOCAB = """\
name: t
statuses: [open, dead]
node_types: [hypothesis]
rules:
  - id: deaths-must-name-a-seed
    when_status: dead
    require_field_one_of: {seed: [1, 2, 3]}
    message: name the seed.
"""


def test_a_field_is_stored_as_typed_not_coerced(graph, monkeypatch):
    graph.rules(NUMERIC_VOCAB).node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: open",
                                    "# c\n")
    monkeypatch.chdir(graph.root)
    assert main(["update", "hyp-x", "--status", "dead", "--field", "seed=2"]) == 0
    assert load(graph.root)["hyp-x"].frontmatter["seed"] == "2"


def test_new_scaffolds_a_required_field_blank_so_validate_still_names_it(tmp_path, monkeypatch):
    """`new` + `validate` is meant to be a checklist."""
    monkeypatch.chdir(tmp_path)
    main(["init", "demo"])
    root = tmp_path / "demo"
    monkeypatch.chdir(root)
    main(["new", "source", "src-a"])
    assert "origin:\n" in (root / "nodes" / "src-a.md").read_text()
    assert main(["validate"]) == 1


@pytest.fixture
def four_findings(graph, monkeypatch, tmp_path):
    compressible_graph(graph, n=4)
    fm = tmp_path / "fm.yaml"
    fm.write_text("type: finding\nstatus: alive\nlinks:\n"
                  "  - {rel: npx:supersedes, to: finding-1}\n"
                  "  - {rel: npx:supersedes, to: finding-2}\n"
                  "  - {rel: npx:supersedes, to: finding-3}\n"
                  "  - {rel: kn:survivedGate, to: gate-a}\n"
                  "  - {rel: kn:survivedGate, to: gate-b}\n", encoding="utf-8")
    body = tmp_path / "body.md"
    body.write_text("# G\n\n## Covers\n- finding-1: a\n- finding-2: b\n- finding-3: c\n",
                    encoding="utf-8")
    monkeypatch.chdir(graph.root)
    return fm, body


def test_a_compression_is_rewarded_in_the_commit_output(four_findings, capsys):
    fm, body = four_findings
    assert main(["commit", "finding-g", "--frontmatter", str(fm), "--body", str(body)]) == 0
    out = capsys.readouterr().out
    assert "compressed 3 findings into 1 under question-q" in out
    assert "survived 2 gates, one more than any of them faced alone" in out
    assert "this graph now stands on 1 rule and 1 specific" in out


def test_a_single_replacement_says_so_in_one_line(four_findings, tmp_path, capsys):
    fm, body = four_findings
    fm.write_text("type: finding\nstatus: alive\nlinks:\n"
                  "  - {rel: npx:supersedes, to: finding-4}\n"
                  "  - {rel: kn:survivedGate, to: gate-b}\n", encoding="utf-8")
    body.write_text("# R\n\n## Covers\n- finding-4: it\n", encoding="utf-8")
    assert main(["commit", "finding-r", "--frontmatter", str(fm), "--body", str(body)]) == 0
    out = capsys.readouterr().out
    assert "replaces finding-4; finding-4 is now superseded" in out
    assert "compressed" not in out


PRINCIPLE_COMP = """\
name: t
statuses: [open, alive, superseded]
node_types: [question, principle, gate]
compressible: [principle]
rules: []
"""


def test_a_compression_of_principles_names_its_own_type(graph, monkeypatch, tmp_path, capsys):
    graph.rules(PRINCIPLE_COMP)
    graph.node("question-q", "id: question-q\ntype: question\nstatus: open", "# Q\n")
    graph.node("gate-a", "id: gate-a\ntype: gate\nstatus: open", "# A\n")
    graph.node("principle-1", "id: principle-1\ntype: principle\nstatus: alive\nlinks:\n"
                              "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
                              "  - {rel: kn:survivedGate, to: gate-a}", "# 1\n")
    graph.node("principle-2", "id: principle-2\ntype: principle\nstatus: alive\nlinks:\n"
                              "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
                              "  - {rel: kn:survivedGate, to: gate-a}", "# 2\n")
    fm = tmp_path / "fm.yaml"
    fm.write_text("type: principle\nstatus: alive\nlinks:\n"
                  "  - {rel: npx:supersedes, to: principle-1}\n"
                  "  - {rel: npx:supersedes, to: principle-2}\n"
                  "  - {rel: kn:survivedGate, to: gate-a}\n", encoding="utf-8")
    body = tmp_path / "body.md"
    body.write_text("# G\n\n## Covers\n- principle-1: a\n- principle-2: b\n", encoding="utf-8")
    monkeypatch.chdir(graph.root)
    assert main(["commit", "principle-g", "--frontmatter", str(fm), "--body", str(body)]) == 0
    out = capsys.readouterr().out
    assert "compressed 2 principles into 1 under question-q" in out


# ---------------------------------------------------------------- knoten idea

def _fresh(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    main(["init", "demo"])
    monkeypatch.chdir(tmp_path / "demo")
    return tmp_path / "demo"


def test_idea_wires_the_question_and_own_intuition_so_the_rules_pass(tmp_path, monkeypatch):
    """An idea must cite the question it serves and a source."""
    root = _fresh(tmp_path, monkeypatch)
    main(["idea", "try a longer holding period"])
    main(["idea", "try a shorter one"])
    node = next(root.joinpath("nodes").glob("idea-try-longer*.md")).read_text()   # stop words drop out of the slug
    assert "prov:wasDerivedFrom, to: question-demo" in node
    assert "prov:wasDerivedFrom, to: source-own-intuition" in node
    assert (root / "nodes" / "source-own-intuition.md").exists()
    assert len(list(root.joinpath("nodes").glob("source-*.md"))) == 1
    assert main(["validate"]) == 0


def test_idea_from_a_named_node_cites_that_instead(tmp_path, monkeypatch):
    root = _fresh(tmp_path, monkeypatch)
    (tmp_path / "fm.yaml").write_text("type: source\nstatus: alive\norigin: https://x\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("# a paper\n", encoding="utf-8")
    assert main(["commit", "source-paper", "--frontmatter", str(tmp_path / "fm.yaml"),
                 "--body", str(tmp_path / "b.md")]) == 0
    assert main(["idea", "what the paper suggests", "--from", "source-paper"]) == 0
    node = next(root.joinpath("nodes").glob("idea-what*.md")).read_text()
    assert "to: source-paper" in node and "own-intuition" not in node
    assert not (root / "nodes" / "source-own-intuition.md").exists()
    assert main(["validate"]) == 0


def test_idea_refuses_an_origin_that_does_not_exist(tmp_path, monkeypatch, capsys):
    _fresh(tmp_path, monkeypatch)
    assert main(["idea", "x", "--from", "nope"]) == 1
    assert "nope" in capsys.readouterr().err

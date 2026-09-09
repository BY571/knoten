"""The CLI is the first thing a new user touches."""
import json
from pathlib import Path

import pytest

from knoten import ops
from knoten.cli import _parser, main
from knoten.core import load
from knoten.validate import check


def test_missing_argument_is_an_error_not_a_traceback(graph, monkeypatch, capsys):
    """`knoten query` with no term raised a raw IndexError at the user."""
    monkeypatch.chdir(graph.root)

    with pytest.raises(SystemExit) as e:
        main(["query"])

    assert e.value.code != 0
    assert "Traceback" not in capsys.readouterr().err


def test_running_outside_a_graph_is_a_clean_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["validate"]) == 1
    assert "graph.yaml" in capsys.readouterr().err


def test_path_error_in_json_mode_is_a_stdout_payload_not_stderr_prose(graph, monkeypatch, capsys):
    monkeypatch.chdir(graph.root)
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead")
    code = main(["path", "hyp-x", "hyp-typo", "--json"])
    out, err = capsys.readouterr()
    assert code == 1
    assert err == ""
    assert "hyp-typo" in json.loads(out)["error"]


def test_validate_returns_nonzero_on_violation(graph, monkeypatch):
    monkeypatch.chdir(graph.root)
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: alive")
    assert main(["validate"]) == 1


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


def _init(tmp_path, monkeypatch, name="t"):
    monkeypatch.chdir(tmp_path)
    assert main(["init", name]) == 0
    root = tmp_path / name
    monkeypatch.chdir(root)
    return root


def test_a_fresh_graph_validates_clean_and_declares_the_round(tmp_path, monkeypatch):
    root = _init(tmp_path, monkeypatch)
    assert main(["validate"]) == 0
    text = (root / "graph.yaml").read_text(encoding="utf-8")
    for rid in ["ideas-come-from-sources", "hypotheses-come-from-ideas",
                "experiments-test-a-hypothesis", "experiments-must-record-what-they-measured",
                "findings-come-from-experiments", "findings-cite-the-run-rather-than-repeat-it"]:
        assert f"id: {rid}" in text
    assert "Cite the question this idea serves." in text


def test_a_fresh_graph_refuses_a_finding_no_experiment_produced(tmp_path, monkeypatch, capsys):
    _init(tmp_path, monkeypatch)
    (tmp_path / "fm.yaml").write_text("type: finding\nstatus: open\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("# f\n", encoding="utf-8")
    assert main(["commit", "finding-x", "--frontmatter", str(tmp_path / "fm.yaml"),
                 "--body", str(tmp_path / "b.md")]) == 1
    assert "findings-come-from-experiments" in capsys.readouterr().err


def test_init_refuses_a_name_that_escapes_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["init", "../evil"]) == 1
    assert not (tmp_path.parent / "evil").exists()


# ---------------------------------------------------------------- knoten new


def test_new_scaffolds_a_node_that_already_passes_the_rules(graph, monkeypatch, capsys):
    """The happy path was: hand-write frontmatter, get rejected, guess, retry."""
    monkeypatch.chdir(graph.root)
    graph.node("gate-cost", "id: gate-cost\ntype: gate")
    assert main(["new", "hypothesis", "hyp-my-idea"]) == 0
    n = load(graph.root)["hyp-my-idea"]
    assert n.type == "hypothesis"
    assert n.status == "open"           # not yet alive: it has survived nothing
    assert check(load(graph.root), graph.root) == []


def test_new_refuses_to_overwrite(graph, monkeypatch):
    monkeypatch.chdir(graph.root)
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead")
    assert main(["new", "hypothesis", "hyp-x"]) == 1


def test_new_refuses_an_id_that_is_not_kebab_case(graph, monkeypatch):
    monkeypatch.chdir(graph.root)
    assert main(["new", "hypothesis", "../evil"]) == 1
    assert not (graph.root.parent / "evil.md").exists()


# ---------------------------------------------------------------- query


def test_query_ranks_the_node_matching_both_tokens_first(graph, monkeypatch, capsys):
    monkeypatch.chdir(graph.root)
    graph.node("hyp-a", "id: hyp-a\ntype: hypothesis\nstatus: dead", body="# about decoding\n")
    graph.node("hyp-b", "id: hyp-b\ntype: hypothesis\nstatus: dead", body="# about prompting\n")
    graph.node("hyp-c", "id: hyp-c\ntype: hypothesis\nstatus: dead",
               body="# about decoding and prompting\n")
    main(["query", "decoding prompting"])
    out = capsys.readouterr().out
    assert out.index("hyp-c") < out.index("hyp-a")
    assert out.index("hyp-c") < out.index("hyp-b")


def retracted_graph(graph):
    graph.node("hyp-wrong", "id: hyp-wrong\ntype: hypothesis\nstatus: retracted")
    graph.node("ret-oops", "id: ret-oops\ntype: hypothesis\nstatus: alive\nlinks:\n"
                           "  - {rel: npx:retracts, to: hyp-wrong}\n"
                           "  - {rel: kn:survivedGate, to: gate-cost}")
    graph.node("gate-cost", "id: gate-cost\ntype: gate")
    return graph


# ------------------------------------------------------------------ index

def indexed(graph):
    (graph.root / "graph.yaml").write_text(
        "name: t\ntags: [decoding, prompting]\nrules: []\n", encoding="utf-8")
    graph.node("hyp-a", "id: hyp-a\ntype: hypothesis\nstatus: open\ntags: [decoding]",
               "# Alpha beats greedy\n")
    graph.node("hyp-b", "id: hyp-b\ntype: hypothesis\nstatus: dead\ntags: [prompting]",
               "# Beta improves accuracy\n")
    return graph


def test_index_filters_on_a_frontmatter_field(graph, monkeypatch, capsys):
    monkeypatch.chdir(indexed(graph).root)
    graph.node("hyp-w", "id: hyp-w\ntype: hypothesis\nstatus: dead\ncause: weak_baseline",
               "# Weak\n")
    main(["index", "--where", "cause=weak_baseline"])
    out = capsys.readouterr().out
    assert "hyp-w" in out
    assert "hyp-a" not in out


def test_a_malformed_where_is_a_clean_error(graph, monkeypatch, capsys):
    monkeypatch.chdir(indexed(graph).root)
    assert main(["index", "--where", "cause"]) == 1
    assert "cause" in capsys.readouterr().err


def test_the_question_comes_before_everything_else(tmp_path, monkeypatch):
    """Column order is the research order."""
    from knoten import viz
    monkeypatch.chdir(tmp_path)
    main(["init", "demo"])
    root = tmp_path / "demo"
    # Edges matter: `source` is cited by the idea and cites nothing, which made it a
    # SHELF — and shelves used to be sorted ahead of everything, so `source` overtook
    # `question`. With no edges at all there are no shelves and this passed vacuously.
    (root / "nodes" / "source-a-paper.md").write_text(
        "---\nid: source-a-paper\ntype: source\nstatus: open\n---\n\n# x\n", encoding="utf-8")
    (root / "nodes" / "idea-a.md").write_text(
        "---\nid: idea-a\ntype: idea\nstatus: open\nlinks:\n"
        "  - {rel: prov:wasDerivedFrom, to: source-a-paper}\n---\n\n# x\n", encoding="utf-8")
    cols, _, _ = viz.roles(load(root))
    assert cols[0] == "question"
    assert cols.index("source") < cols.index("idea")


def test_frontier_prints_the_shape_first_and_the_compressible_band_before_open(graph, monkeypatch, capsys):
    graph.rules("""\
name: t
statuses: [open, alive, active]
node_types: [question, finding, gate, hypothesis]
rules: []
""")
    graph.node("question-q", "id: question-q\ntype: question\nstatus: open", "# Q\n")
    graph.node("gate-h", "id: gate-h\ntype: gate\nstatus: active", "# H\n")
    graph.node("hyp-open", "id: hyp-open\ntype: hypothesis\nstatus: open", "# Open one\n")
    for i in range(3):
        graph.node(f"finding-{i}", f"id: finding-{i}\ntype: finding\nstatus: alive\nlinks:\n"
                                   "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
                                   "  - {rel: kn:survivedGate, to: gate-h}", f"# {i}\n")
    monkeypatch.chdir(graph.root)
    assert main(["frontier"]) == 0
    out = capsys.readouterr().out
    head, rest = out.split("\n", 1)
    assert "0 rules over 3 specifics" in head and "1 compressible cluster" in head
    assert rest.index("COMPRESSIBLE") < rest.index("OPEN")
    assert "question-q  ·  gate-h  ·  3 alive findings" in rest
    assert "finding-0, finding-1, finding-2" in rest


def test_index_footer_says_how_many_superseded_are_hidden(graph, monkeypatch, capsys):
    graph.rules("name: t\nnode_types: [finding]\nstatuses: [alive, superseded]\nrules: []\n")
    graph.node("finding-old", "id: finding-old\ntype: finding\nstatus: superseded", "# old\n")
    graph.node("finding-new", "id: finding-new\ntype: finding\nstatus: alive", "# new\n")
    monkeypatch.chdir(graph.root)
    assert main(["index"]) == 0
    out = capsys.readouterr().out
    assert "finding-old" not in out and "1 superseded hidden; --all shows them" in out
    assert main(["index", "--all"]) == 0
    assert "finding-old" in capsys.readouterr().out


def test_show_prints_covers_before_the_body_of_a_general_node(graph, monkeypatch, capsys):
    graph.rules("name: t\nnode_types: [question, finding]\nstatuses: [alive, superseded, open]\nrules: []\n")
    graph.node("question-q", "id: question-q\ntype: question\nstatus: open", "# Q\n")
    for i in (1, 2):
        graph.node(f"finding-{i}", f"id: finding-{i}\ntype: finding\nstatus: superseded\nlinks:\n"
                                   "  - {rel: prov:wasDerivedFrom, to: question-q}", f"# {i}\n")
    graph.node("finding-g", "id: finding-g\ntype: finding\nstatus: alive\nlinks:\n"
                            "  - {rel: npx:supersedes, to: finding-1}\n"
                            "  - {rel: npx:supersedes, to: finding-2}",
               "# G\n\nThe general claim.\n\n## Covers\n- finding-1: small\n- finding-2: large\n")
    monkeypatch.chdir(graph.root)
    assert main(["show", "finding-g"]) == 0
    out = capsys.readouterr().out
    assert out.index("covers:") < out.index("finding-1: small")
    assert "finding-1: small" in out




def test_a_fresh_graph_accepts_an_idea_that_came_from_a_finding(tmp_path, monkeypatch, capsys):
    """`type: source, finding` in YAML flow syntax parsed as `type: source` plus a stray
    key, so the rule's own message promised something it refused. The value is quoted now."""
    root = _init(tmp_path, monkeypatch)
    (root / "nodes" / "finding-f.md").write_text(
        "---\nid: finding-f\ntype: finding\nstatus: alive\nlinks:\n"
        "  - {rel: prov:wasDerivedFrom, to: question-t}\n---\n\n# f\n", encoding="utf-8")
    (tmp_path / "fm.yaml").write_text(
        "type: idea\nstatus: open\nlinks:\n  - {rel: prov:wasDerivedFrom, to: question-t}\n"
        "  - {rel: prov:wasDerivedFrom, to: finding-f}\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("# an idea from a finding\n", encoding="utf-8")

    assert main(["commit", "idea-x", "--frontmatter", str(tmp_path / "fm.yaml"),
                 "--body", str(tmp_path / "b.md")]) == 0, capsys.readouterr().err


def test_the_rl_example_validates_and_tracks_its_return(monkeypatch, capsys):
    root = Path(__file__).resolve().parents[1] / "examples" / "rl-reward"
    monkeypatch.chdir(root)

    assert main(["validate"]) == 0
    assert main(["metric", "return"]) == 0
    out = capsys.readouterr().out
    assert "best 15.4" in out and "builds on exp-reward-scale" in out


# ---------------------------------------------------------------- a graph is its own repository

def _git(cwd, *args):
    import subprocess
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


def test_init_makes_the_graph_its_own_repository_with_the_gate_and_the_check(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t"); monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@t.t")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t"); monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@t.t")
    assert main(["init", "my-topic"]) == 0
    g = tmp_path / "my-topic"
    assert _git(g, "rev-parse", "--show-toplevel").stdout.strip() == str(g.resolve())
    assert _git(g, "config", "pull.rebase").stdout.strip() == "true"
    assert _git(g, "config", "rebase.autoStash").stdout.strip() == "true"
    assert _git(g, "log", "--oneline").stdout.count("\n") == 1          # the first commit, made
    assert _git(g, "status", "--porcelain").stdout == ""                 # and it took everything
    hooks = Path(_git(g, "rev-parse", "--git-path", "hooks").stdout.strip())
    assert "knoten validate" in (g / hooks / "pre-commit").read_text(encoding="utf-8")
    workflow = (g / ".github" / "workflows" / "knoten.yml").read_text(encoding="utf-8")
    assert "knoten validate" in workflow and "on: [push" in workflow
    out = capsys.readouterr().out
    assert "gh repo create" in out and "git pull" in out


def test_init_inside_a_project_keeps_the_graph_out_of_the_project(tmp_path, monkeypatch):
    """The project's repo ignores the graph folder: the knowledge is shared on its own."""
    project = tmp_path / "project"; project.mkdir()
    assert _git(project, "init", "-q").returncode == 0
    (project / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
    monkeypatch.chdir(project)
    assert main(["init", "research"]) == 0
    assert (project / ".gitignore").read_text(encoding="utf-8") == "*.pyc\nresearch/\n"
    assert _git(project / "research", "rev-parse", "--show-toplevel").stdout.strip() == str((project / "research").resolve())
    assert "research" not in _git(project, "status", "--porcelain").stdout   # ignored, not untracked

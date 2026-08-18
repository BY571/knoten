"""The CLI is the first thing a new user touches. It should never hand them a
traceback. `main()` returns an exit code; the console script exits with it."""
import pytest

from knoten.cli import main
from knoten.core import load
from knoten.validate import check


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


# ---------------------------------------------------------------- knoten new


def test_new_scaffolds_a_node_that_already_passes_the_rules(graph, monkeypatch, capsys):
    """The happy path was: hand-write frontmatter, get rejected, guess, retry. The failure
    mode this tool exists to prevent was caused by FRICTION, so friction on the write path
    is the thing to attack hardest."""
    monkeypatch.chdir(graph.root)
    graph.node("method-gate", "id: method-gate\ntype: method")

    assert main(["new", "hypothesis", "hyp-my-idea"]) == 0

    n = load(graph.root)["hyp-my-idea"]
    assert n.type == "hypothesis"
    assert n.status == "open"           # not yet alive: it has survived nothing
    assert check(load(graph.root), graph.root) == []


def test_a_dead_node_is_scaffolded_with_whatever_the_rules_demand(graph, monkeypatch):
    """Nothing here is knoten's opinion — it reads THIS graph's rules and pre-fills exactly
    what they require, so the author writes prose instead of rediscovering the rule."""
    graph.rules("""\
rules:
  - id: dead-claims-must-say-why
    when_status: dead
    require_sections: Why it died, What would reopen this
    require_result_min: {n_independent: 30}
    message: The post-mortem IS the asset.
""")
    monkeypatch.chdir(graph.root)

    main(["new", "hypothesis", "hyp-dead", "--status", "dead"])
    text = graph.read("hyp-dead")

    assert "## Why it died" in text
    assert "## What would reopen this" in text
    assert "n_independent: TODO" in text


def test_new_refuses_to_overwrite(graph, monkeypatch):
    monkeypatch.chdir(graph.root)
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead")

    assert main(["new", "hypothesis", "hyp-x"]) == 1


def test_new_refuses_an_id_that_is_not_kebab_case(graph, monkeypatch):
    monkeypatch.chdir(graph.root)

    assert main(["new", "hypothesis", "../evil"]) == 1
    assert not (graph.root.parent / "evil.md").exists()


# ---------------------------------------------------------------- query


def test_query_matches_across_the_separator(graph, monkeypatch, capsys):
    """`knoten query "self consistency"` found NOTHING, because the node is
    `self-consistency`. A naive substring match makes the tool's headline question fail on
    a space."""
    monkeypatch.chdir(graph.root)
    graph.node("hyp-self-consistency", "id: hyp-self-consistency\ntype: hypothesis\nstatus: dead")

    main(["query", "self consistency"])

    assert "hyp-self-consistency" in capsys.readouterr().out


def test_query_ranks_the_node_matching_both_tokens_first(graph, monkeypatch, capsys):
    """This replaces `test_query_requires_all_tokens`, which asserted that a partial
    match returns NOTHING. That contract was the headline bug: it answered "no prior
    work" for every question phrased in words the node happened not to use. Partial
    matches are the point — "anything related?" is the question — so the guarantee moved
    from exclusion to ORDER: the closest node comes first."""
    monkeypatch.chdir(graph.root)
    graph.node("hyp-a", "id: hyp-a\ntype: hypothesis\nstatus: dead", body="# about decoding\n")
    graph.node("hyp-b", "id: hyp-b\ntype: hypothesis\nstatus: dead", body="# about prompting\n")
    graph.node("hyp-c", "id: hyp-c\ntype: hypothesis\nstatus: dead",
               body="# about decoding and prompting\n")

    main(["query", "decoding prompting"])
    out = capsys.readouterr().out

    assert out.index("hyp-c") < out.index("hyp-a")
    assert out.index("hyp-c") < out.index("hyp-b")


def test_query_says_a_claim_was_retracted_by_another_node(graph, monkeypatch, capsys):
    """`_summarise` reported what a node RETRACTS but never that it WAS retracted. An
    agent asking "has this been tried?" about a withdrawn claim was told the verdict and
    not the withdrawal — and SPEC calls retraction the most valuable node type."""
    monkeypatch.chdir(graph.root)
    retracted_graph(graph)

    main(["query", "hyp-wrong"])
    out = capsys.readouterr().out

    assert "ret-oops" in out


def retracted_graph(graph):
    graph.node("hyp-wrong", "id: hyp-wrong\ntype: hypothesis\nstatus: retracted")
    graph.node("ret-oops", "id: ret-oops\ntype: hypothesis\nstatus: alive\nlinks:\n"
                           "  - {rel: npx:retracts, to: hyp-wrong}\n"
                           "  - {rel: kn:survivedGate, to: method-gate}")
    graph.node("method-gate", "id: method-gate\ntype: method")
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


def test_index_prints_one_line_per_node_with_the_claim(graph, monkeypatch, capsys):
    """The CLI and the MCP server must not drift: a fix that reaches one surface and not
    the other is this project's most repeated bug."""
    monkeypatch.chdir(indexed(graph).root)

    main(["index"])
    out = capsys.readouterr().out

    assert "hyp-a" in out and "Alpha beats greedy" in out
    assert "hyp-b" in out and "Beta improves accuracy" in out


def test_index_filters_by_tag(graph, monkeypatch, capsys):
    monkeypatch.chdir(indexed(graph).root)

    main(["index", "--tag", "prompting"])
    out = capsys.readouterr().out

    assert "hyp-b" in out
    assert "hyp-a" not in out


def test_index_filters_by_status(graph, monkeypatch, capsys):
    monkeypatch.chdir(indexed(graph).root)

    main(["index", "--status", "open"])
    out = capsys.readouterr().out

    assert "hyp-a" in out
    assert "hyp-b" not in out

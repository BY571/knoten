"""Regressions from an adversarial review of the fail-open fix. Each of these was
reproduced against the "fixed" code — including one that is a variant of the very bug
that fix claimed to close."""
import pytest
import yaml

from knoten.cli import _set_attachments, attach
from knoten.core import GraphError, load
from knoten.validate import check, load_rules


def frontmatter_of(text):
    return yaml.safe_load(text.split("---")[1])


# ---------------------------------------------------------------- the parser

@pytest.mark.parametrize("line,expected", [
    ("wallclock: 12:30", "12:30"),      # YAML 1.1 sexagesimal -> 750
    ("t: 1:30.5", "1:30.5"),            #                      -> 90.5
    ("seed: 042", "042"),               # YAML 1.1 octal       -> 34
    ("n: 1_000", "1_000"),              # underscore digits    -> 1000
    ("run: 2024-01-15", "2024-01-15"),  # implicit timestamp   -> datetime.date
])
def test_yaml_1_1_does_not_silently_rewrite_a_result(graph, line, expected):
    """`results:` holds experimental numbers. A runtime of 12:30 recorded as `750`, or a
    seed of 042 as `34`, is a research-integrity bug — and it validated clean."""
    graph.node("hyp-x", f"id: hyp-x\ntype: hypothesis\nstatus: dead\nresults:\n  {line}")
    n = load(graph.root)["hyp-x"]

    assert list(n.results.values()) == [expected]


def test_real_numbers_still_parse_as_numbers(graph):
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead\nresults:\n"
                        "  acc: 0.741\n  n: 1319\n  neg: -3\n  exp: 1.2e-3\n  big: 0")
    r = load(graph.root)["hyp-x"].results

    assert r == {"acc": 0.741, "n": 1319, "neg": -3, "exp": 1.2e-3, "big": 0}


def test_frontmatter_cannot_clobber_the_real_id_and_path(graph):
    """`to_dict` splatted frontmatter LAST, so a stale `id:` after a rename overwrote the
    filename-derived id — in graph.json, the artifact labelled "for agents"."""
    graph.node("hyp-x", "id: hyp-STALE\ntype: hypothesis\nstatus: dead\npath: /etc/passwd")
    d = load(graph.root)["hyp-x"].to_dict()

    assert d["id"] == "hyp-x"
    assert d["path"] == "nodes/hyp-x.md"


# ---------------------------------------------------------------- attach

def test_attach_survives_a_zero_indent_attachments_list(graph, tmp_path):
    """`yaml.dump` emits list items at ZERO indent by default. The old dropper required
    leading whitespace, so it orphaned them and left the node UNPARSEABLE — destroying
    the node, and with it the whole graph (load() raises)."""
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead\nattachments:\n- old.png")
    graph.attachment("hyp-x", "old.png")
    new = tmp_path / "new.txt"
    new.write_text("x", encoding="utf-8")

    attach(graph.root, "hyp-x", [str(new)])

    assert frontmatter_of(graph.read("hyp-x"))["attachments"] == ["new.txt", "old.png"]
    assert load(graph.root)["hyp-x"].attachments == ["new.txt", "old.png"]


@pytest.mark.parametrize("name", ["plot #1.png", "run: final.png", "123", "-lead.png", "a b.png"])
def test_a_filename_needing_quotes_gets_them(graph, tmp_path, name):
    """Filenames were written raw. `plot #1.png` became the YAML comment `plot`, and
    `run: final.png` became a dict — both legal Linux filenames."""
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead")
    f = tmp_path / name
    f.write_text("x", encoding="utf-8")

    attach(graph.root, "hyp-x", [str(f)])

    assert frontmatter_of(graph.read("hyp-x"))["attachments"] == [name]
    assert load(graph.root)["hyp-x"].attachments == [name]
    assert check(load(graph.root), graph.root) == []


def test_two_files_with_the_same_basename_are_refused(graph, tmp_path):
    """Both copied to attachments/<id>/plot.png — the second silently overwrote the
    first, while the response reported two files attached."""
    a, b = tmp_path / "a" / "plot.png", tmp_path / "b" / "plot.png"
    for p in (a, b):
        p.parent.mkdir()
        p.write_text(p.parent.name, encoding="utf-8")
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead")

    with pytest.raises(GraphError, match="plot.png"):
        attach(graph.root, "hyp-x", [str(a), str(b)])


def test_attaching_a_directory_is_refused_before_anything_is_copied(graph, tmp_path):
    """`exists()` is true for a directory, so the pre-check passed and copy2 blew up
    halfway through, leaving an orphaned file in attachments/."""
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead")
    good = tmp_path / "good.py"
    good.write_text("x", encoding="utf-8")
    (tmp_path / "adir").mkdir()

    with pytest.raises(GraphError, match="adir"):
        attach(graph.root, "hyp-x", [str(good), str(tmp_path / "adir")])

    assert not (graph.root / "attachments" / "hyp-x" / "good.py").exists()


def test_set_attachments_refuses_a_node_it_cannot_reparse(graph):
    """Belt and braces: never write a node back in a state we cannot read."""
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead")
    nf = graph.root / "nodes" / "hyp-x.md"

    _set_attachments(nf, ["a.png"])          # fine
    assert frontmatter_of(nf.read_text())["attachments"] == ["a.png"]


# ---------------------------------------------------------------- rule values

def test_a_rule_with_a_non_numeric_floor_is_a_clean_error(graph):
    """Crashed with a raw TypeError, contradicting "a rule this engine cannot understand
    is a hard error"."""
    (graph.root / "graph.yaml").write_text(
        "rules:\n  - id: r\n    require_result_min: {n: '30'}\n", encoding="utf-8")

    with pytest.raises(GraphError, match="require_result_min"):
        load_rules(graph.root)


def test_require_result_min_must_be_a_mapping(graph):
    (graph.root / "graph.yaml").write_text(
        "rules:\n  - id: r\n    require_result_min: n_independent\n", encoding="utf-8")

    with pytest.raises(GraphError, match="require_result_min"):
        load_rules(graph.root)


def test_require_edge_must_be_a_string(graph):
    """Natural mistake — `when_status` accepts a list, so why not this? Raw TypeError:
    unhashable type 'list'."""
    (graph.root / "graph.yaml").write_text(
        "rules:\n  - id: r\n    require_edge: [kn:survivedGate]\n", encoding="utf-8")

    with pytest.raises(GraphError, match="require_edge"):
        load_rules(graph.root)


def test_a_boolean_does_not_satisfy_a_numeric_floor(graph):
    """`bool` is a subclass of `int`, so `accuracy: true` passed `require_result_min:
    {accuracy: 0.8}` as 1. Floors below 1 (accuracy, F1, AUC) are the common case."""
    (graph.root / "graph.yaml").write_text(
        "rules:\n  - id: r\n    require_result_min: {accuracy: 0.8}\n    message: m\n",
        encoding="utf-8")
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead\nresults:\n  accuracy: true")

    assert [v.rule for v in check(load(graph.root), graph.root)] == ["r"]

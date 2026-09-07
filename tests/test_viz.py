"""One HTML file you can open from a plane."""
import json
import re

import pytest

from knoten import ops, viz
from knoten.cli import main
from knoten.core import load

GATE = "id: gate-costs\ntype: gate\nstatus: active"
HYP = ("id: hyp-a\ntype: hypothesis\nstatus: alive\ncreated: 2026-01-01\nlinks:\n"
       "  - {rel: kn:survivedGate, to: gate-costs}")


@pytest.fixture
def small(graph):
    return (graph.node("gate-costs", GATE, "# Gate: costs\n\n## The rule\nPrice it.\n")
                 .node("hyp-a", HYP, "# A claim that survived\n")
                 .node("find-b", "id: find-b\ntype: finding\nstatus: dead\n"
                                 "created: 2026-01-02", "# A dead finding\n"))


# ------------------------------------------------------------------ layout

def test_appending_a_node_moves_nothing(small, graph):
    """The property the whole design rests on."""
    before = viz.layout(load(small.root))
    graph.node("hyp-z", "id: hyp-z\ntype: hypothesis\nstatus: open\ncreated: 2026-06-01")
    after = viz.layout(load(small.root))
    for view in before:
        for nid, xy in before[view].items():
            assert after[view][nid] == xy, f"{view}: {nid} moved"


# ------------------------------------------------------------------ the file

def test_the_file_reaches_nothing(small):
    """"Opens on a plane" is the whole promise of a single file."""
    html = viz.render(small.root)
    fetchable = re.findall(r'(?:src|href)\s*=\s*["\'](?!#)([^"\']+)', html)
    fetchable += re.findall(r'url\(\s*["\']?(?!data:)([^)"\']+)', html)
    fetchable += re.findall(r"@import\s+[\"']([^\"']+)", html)
    assert fetchable == [], f"reaches out to {fetchable}"


def test_a_node_body_cannot_close_the_script_tag(graph):
    """An agent writes a node body containing the literal `</script>`."""
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead",
               "# Bad\n\n## Why it died\nWe wrote </script> in the notes.\n")
    html = viz.render(graph.root)
    payload = html.split("const DATA = ", 1)[1].split("\n", 1)[0]
    assert "</script>" not in payload
    assert "hyp-x" in payload


def test_the_payload_carries_what_the_panel_shows(small):
    data = viz.payload(small.root)
    node = next(n for n in data["nodes"] if n["id"] == "gate-costs")
    assert node["type"] == "gate"
    assert any(s["title"] == "The rule" for s in node["sections"])
    assert data["graph"]["rules"] is not None


# ------------------------------------------------------------------ the command

def test_viz_on_a_graph_with_no_nodes_directory_fails_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "graph.yaml").write_text("name: t\nrules: []\n", encoding="utf-8")
    assert main(["viz", "-o", str(tmp_path / "g.html")]) == 1
    assert "Traceback" not in capsys.readouterr().err


def test_appending_an_undated_node_does_not_push_everything_down(small, graph):
    before = viz.layout(load(small.root))
    graph.node("hyp-undated", "id: hyp-undated\ntype: hypothesis\nstatus: open")
    after = viz.layout(load(small.root))
    for nid, xy in before["columns"].items():
        assert after["columns"][nid] == xy, f"{nid} moved"


# ------------------------------------------------------------------ --watch

def test_watch_injects_a_reload(small):
    html = viz.render(small.root, reload_ms=2000)
    assert "location.reload" in html
    assert "2000" in html


def test_the_fingerprint_changes_when_a_node_is_written(small, graph):
    """What `--watch` polls."""
    before = viz.fingerprint(small.root)
    graph.node("hyp-new", "id: hyp-new\ntype: hypothesis\nstatus: open")
    assert viz.fingerprint(small.root) != before


def test_the_watch_block_is_cut_out_entirely_when_off(small):
    html = viz.render(small.root)
    assert "location.reload" not in html
    assert "beforeunload" not in html
    assert "__RELOAD_MS__" not in html


def test_a_section_keeps_its_shape_in_the_panel(graph):
    """`core.section` collapses whitespace because the CLI prints it inline."""
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: open",
               "# A claim\n\n## The result\n| tau | b |\n|---|---|\n| 0-60 | 1.05 |\n")
    text = next(s["text"] for s in viz.payload(graph.root)["nodes"][0]["sections"]
                if s["title"] == "The result")
    assert text.count("\n") >= 2
    assert "| 0-60 | 1.05 |" in text


def test_a_node_that_breaks_the_graphs_rules_says_so(graph):
    graph.rules("name: t\nrules:\n  - id: no-results-on-claims\n    when_type: hypothesis\n"
                "    forbid_fields: results\n    message: A claim is not a run.\n")
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: open\nresults:\n  auc: 0.9")
    data = viz.payload(graph.root)
    assert data["violations"]["hyp-x"][0]["rule"] == "no-results-on-claims"


# ------------------------------------------------------------------ record scaffold

def test_the_record_panel_orders_by_the_scaffold(small):
    html = (viz.HERE / "viz.html").read_text(encoding="utf-8")
    assert "for (const p of PANEL_SECTIONS){" in html
    assert 'el("h3", "field", p.label)' in html


Q = "id: question-q\ntype: question\nstatus: open\ncreated: 2026-01-01"
GATE_H = "id: gate-h\ntype: gate\nstatus: active\ncreated: 2026-01-01"


def _finding(i, status="alive", extra=""):
    return (f"id: finding-{i}\ntype: finding\nstatus: {status}\ncreated: 2026-01-0{i}\nlinks:\n"
            "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
            "  - {rel: kn:survivedGate, to: gate-h}" + extra)


@pytest.fixture
def compressed(graph):
    """Two specifics retired by one rule, one specific still alive, and one orphan."""
    graph.rules("name: t\nnode_types: [question, finding, gate]\n"
                "statuses: [open, alive, superseded, active]\nrules: []\n")
    graph.node("question-q", Q, "# Q\n").node("gate-h", GATE_H, "# H\n")
    for i in (1, 2):
        graph.node(f"finding-{i}", _finding(i, "superseded"), f"# {i}\n")
    graph.node("finding-3", _finding(3), "# 3\n")
    graph.node("finding-4", _finding(4, "superseded"), "# orphan\n")     # nothing alive covers it
    graph.node("finding-g", "id: finding-g\ntype: finding\nstatus: alive\ncreated: 2026-01-05\nlinks:\n"
                            "  - {rel: npx:supersedes, to: finding-1}\n"
                            "  - {rel: npx:supersedes, to: finding-2}\n"
                            "  - {rel: kn:survivedGate, to: gate-h}",
               "# G\n\n## Covers\n- finding-1: a\n- finding-2: b\n")
    return graph


def test_the_payload_says_who_covers_whom(compressed):
    p = viz.payload(compressed.root)
    by = {n["id"]: n for n in p["nodes"]}
    assert by["finding-g"]["rule"] is True and by["finding-g"]["covers"] == ["finding-1", "finding-2"]
    assert by["finding-1"]["under"] == "finding-g" and by["finding-3"]["under"] is None
    assert by["finding-4"]["under"] is None and by["finding-4"]["status"] == "superseded"
    assert by["finding-3"]["rule"] is False and by["finding-3"]["covers"] == []


def test_a_single_target_supersession_folds_and_can_be_opened(compressed):
    compressed.node("finding-3", _finding(3, "superseded"), "# 3\n")
    compressed.node("finding-p", "id: finding-p\ntype: finding\nstatus: alive\n"
                                 "created: 2026-01-07\nlinks:\n"
                                 "  - {rel: npx:supersedes, to: finding-3}\n"
                                 "  - {rel: kn:survivedGate, to: gate-h}",
                    "# P\n\n## Covers\n- finding-3: the one it replaces\n")
    p = viz.payload(compressed.root)
    by = {n["id"]: n for n in p["nodes"]}
    assert by["finding-p"]["rule"] is False and by["finding-p"]["covers"] == ["finding-3"]
    assert by["finding-3"]["under"] == "finding-p"
    assert "finding-p" in p["folded"] and "finding-3" not in p["folded"]
    # The rows the page measures come off what can be opened, not off the badge.
    assert "if (opens(n)) walk(n, new Set());" in viz.render(compressed.root)


def test_the_folded_view_still_holds_the_rule_when_every_specific_is_covered(compressed):
    """The fold must never leave the page with nothing to draw."""
    compressed.node("finding-3", _finding(3, "superseded"), "# 3\n")
    compressed.node("finding-4", _finding(4, "superseded"), "# 4\n")
    compressed.node("finding-g", "id: finding-g\ntype: finding\nstatus: alive\n"
                                 "created: 2026-01-05\nlinks:\n"
                                 + "".join(f"  - {{rel: npx:supersedes, to: finding-{i}}}\n"
                                           for i in (1, 2, 3, 4)) +
                                 "  - {rel: kn:survivedGate, to: gate-h}",
                    "# G\n\n## Covers\n- finding-1\n- finding-2\n- finding-3\n- finding-4\n")
    p = viz.payload(compressed.root)
    assert "finding-g" in p["folded"]
    assert not {"finding-1", "finding-2", "finding-3", "finding-4"} & set(p["folded"])


def test_a_rule_over_a_rule_hangs_the_whole_chain(compressed):
    compressed.node("finding-g", "id: finding-g\ntype: finding\nstatus: superseded\n"
                                 "created: 2026-01-05\nlinks:\n"
                                 "  - {rel: npx:supersedes, to: finding-1}\n"
                                 "  - {rel: npx:supersedes, to: finding-2}\n"
                                 "  - {rel: kn:survivedGate, to: gate-h}",
                    "# G\n\n## Covers\n- finding-1: a\n- finding-2: b\n")
    compressed.node("finding-o", "id: finding-o\ntype: finding\nstatus: alive\n"
                                 "created: 2026-01-06\nlinks:\n"
                                 "  - {rel: npx:supersedes, to: finding-g}\n"
                                 "  - {rel: kn:survivedGate, to: gate-h}",
                    "# O\n\n## Covers\n- finding-g: both of them\n")
    p = viz.payload(compressed.root)
    by = {n["id"]: n for n in p["nodes"]}
    assert by["finding-g"]["under"] == "finding-o"
    assert by["finding-1"]["under"] == "finding-g" and by["finding-2"]["under"] == "finding-g"
    assert not {"finding-1", "finding-2", "finding-g"} & set(p["folded"])


def test_the_folded_columns_share_the_full_views_headers(graph):
    graph.rules("name: t\nnode_types: [question, hypothesis, finding, gate]\n"
                "statuses: [open, alive, superseded, active]\nrules: []\n")
    graph.node("question-q", Q, "# Q\n").node("gate-h", GATE_H, "# H\n")
    for i in (1, 2):
        graph.node(f"hyp-{i}",
                   f"id: hyp-{i}\ntype: hypothesis\nstatus: superseded\ncreated: 2026-01-0{i}\nlinks:\n"
                   "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
                   "  - {rel: kn:survivedGate, to: gate-h}", f"# {i}\n")
    graph.node("finding-g", "id: finding-g\ntype: finding\nstatus: alive\ncreated: 2026-01-05\nlinks:\n"
                            "  - {rel: npx:supersedes, to: hyp-1}\n"
                            "  - {rel: npx:supersedes, to: hyp-2}\n"
                            "  - {rel: kn:survivedGate, to: gate-h}", "# G\n")
    p = viz.payload(graph.root)
    by = {n["id"]: n for n in p["nodes"]}
    assert p["columns"] == ["question", "hypothesis", "finding", "gate"]
    assert p["folded"]["finding-g"]["columns"][0] == by["finding-g"]["columns"][0]


def test_a_supersedes_cycle_cannot_hang_the_page(small):
    html = viz.render(small.root)
    assert "function visible(id, seen)" in html
    assert "function posOf(n, seen)" in html
    assert html.count("seen.has") >= 3          # visible, posOf, the walk up to the card



"""One HTML file you can open from a plane.

The properties here are the ones that make the map usable rather than pretty: it must
not move under your feet as an agent appends to the graph, it must not reach the network,
and it must not execute what an agent wrote into a node body.
"""
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
    """The property the whole design rests on. An agent appends to this graph while you
    are looking at it; if the map reshuffles, it stops being a map and becomes a slot
    machine. Byte-identical, not approximately."""
    before = viz.layout(load(small.root))

    graph.node("hyp-z", "id: hyp-z\ntype: hypothesis\nstatus: open\ncreated: 2026-06-01")
    after = viz.layout(load(small.root))

    for view in before:
        for nid, xy in before[view].items():
            assert after[view][nid] == xy, f"{view}: {nid} moved"


def test_the_layout_does_not_depend_on_dict_order(small):
    """No randomness, no force simulation, no dict-order dependence. Calling it twice on
    one dict in one process could not have caught the third of those: the insertion order
    was identical both times, so the claim in this docstring went untested."""
    nodes = load(small.root)

    assert viz.layout(nodes) == viz.layout(dict(reversed(list(nodes.items()))))


def test_a_graph_with_no_gates_still_lays_out(graph):
    """Clustering keys on the busiest nodes, not on `type: gate`. A graph that declares
    no gates at all must still produce a map rather than an empty one."""
    graph.node("a", "id: a\ntype: note\nstatus: open").node("b", "id: b\ntype: note\nstatus: open")

    pos = viz.layout(load(graph.root))

    assert set(pos["map"]) == {"a", "b"}


def test_every_node_gets_a_position_in_every_view(small):
    nodes = load(small.root)
    pos = viz.layout(nodes)

    for view in pos:
        assert set(pos[view]) == set(nodes)


# ------------------------------------------------------------------ the file

def test_the_file_reaches_nothing(small):
    """"Opens on a plane" is the whole promise of a single file. A stylesheet, a font or
    an analytics beacon would make the map depend on a network it will not have."""
    html = viz.render(small.root)

    fetchable = re.findall(r'(?:src|href)\s*=\s*["\'](?!#)([^"\']+)', html)
    fetchable += re.findall(r'url\(\s*["\']?(?!data:)([^)"\']+)', html)
    fetchable += re.findall(r"@import\s+[\"']([^\"']+)", html)

    assert fetchable == [], f"reaches out to {fetchable}"


def test_a_node_body_cannot_close_the_script_tag(graph):
    """An agent writes a node body containing the literal `</script>`. Inlined naively it
    terminates the payload and the file renders as a blank page — every node lost to one
    string in one post-mortem."""
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead",
               "# Bad\n\n## Why it died\nWe wrote </script> in the notes.\n")

    html = viz.render(graph.root)
    payload = html.split("const DATA = ", 1)[1].split("\n", 1)[0]

    assert "</script>" not in payload
    assert "hyp-x" in payload


@pytest.mark.parametrize("sink", ["innerHTML", "outerHTML", "insertAdjacentHTML",
                                  "document.write", "eval(", "new Function"])
def test_the_template_uses_no_html_sink(sink):
    """Node bodies are written by agents. Any of these on one is arbitrary script
    execution in the reader's browser, from a file they opened to read a post-mortem."""
    assert sink not in (viz.HERE / "viz.html").read_text()


def test_the_payload_carries_what_the_panel_shows(small):
    data = viz.payload(small.root)
    node = next(n for n in data["nodes"] if n["id"] == "gate-costs")

    assert node["type"] == "gate"
    assert any(s["title"] == "The rule" for s in node["sections"])
    assert data["graph"]["rules"] is not None


# ------------------------------------------------------------------ the command

def test_viz_writes_a_file(small, monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(small.root)
    out = tmp_path / "g.html"

    assert main(["viz", "-o", str(out)]) == 0
    assert "<!doctype html>" in out.read_text().lower()
    assert str(out) in capsys.readouterr().out


def test_viz_on_a_graph_with_no_nodes_directory_fails_cleanly(tmp_path, monkeypatch, capsys):
    """This used to `chdir` somewhere with no graph.yaml at all, so `find_root` raised
    before viz was ever reached — it passed for every subcommand and would have passed
    with viz's own guard deleted. `load()` returns {} for a missing nodes/ dir, so that
    guard is the only thing between the user and a valid-looking empty page."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "graph.yaml").write_text("name: t\nrules: []\n", encoding="utf-8")

    assert main(["viz", "-o", str(tmp_path / "g.html")]) == 1
    assert "Traceback" not in capsys.readouterr().err


def test_appending_an_undated_node_does_not_push_everything_down(small, graph):
    """`str(None or "")` is "", which sorts before every date — so a node written by hand
    or by another tool took slot 0 and moved every node already placed. `knoten new` and
    `knoten commit` both stamp `created`, which is why this hid."""
    before = viz.layout(load(small.root))

    graph.node("hyp-undated", "id: hyp-undated\ntype: hypothesis\nstatus: open")
    after = viz.layout(load(small.root))

    for nid, xy in before["columns"].items():
        assert after["columns"][nid] == xy, f"{nid} moved"


def test_an_agent_authored_body_cannot_become_markup(graph):
    """Node bodies are written by agents. Grepping the template for `innerHTML` misses
    outerHTML, insertAdjacentHTML, document.write and eval — and never executes anything.
    This pins the property that actually protects the reader: `<` never survives into the
    payload, so there is no tag to inject."""
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead",
               "# Bad\n\n## Why it died\n<img src=x onerror=alert(1)>\n")

    payload = viz.render(graph.root).split("const DATA = ", 1)[1].split("\n", 1)[0]

    assert "<img" not in payload
    assert "onerror" in payload          # the text is still there, just not as markup


def test_open_hands_the_file_to_the_browser(small, monkeypatch, tmp_path):
    """The one line of wiring nothing else covers."""
    import webbrowser
    opened = []
    monkeypatch.chdir(small.root)
    monkeypatch.setattr(webbrowser, "open", opened.append)

    main(["viz", "-o", str(tmp_path / "g.html"), "--open"])

    assert opened and opened[0].startswith("file://")


def test_declared_meanings_reach_the_legend(graph):
    """The legend explains each column in the graph's own words. Before this, `node_types`
    could only be a list, so the branch that reads meanings could never run: a graph that
    declared them failed to load at all."""
    graph.rules("name: t\nnode_types:\n  hypothesis: a falsifiable claim\nrules: []\n")
    graph.node("hyp-a", "id: hyp-a\ntype: hypothesis\nstatus: open")

    assert viz.payload(graph.root)["graph"]["vocab"] == {"hypothesis": "a falsifiable claim"}


# ------------------------------------------------------------------ --watch

def test_the_static_file_never_reloads_itself(small):
    """A file you emailed someone, or opened on a plane, must not sit there re-fetching
    itself. The reload only exists while `--watch` is holding the file open."""
    assert "location.reload" not in viz.render(small.root)


def test_watch_injects_a_reload(small):
    html = viz.render(small.root, reload_ms=2000)

    assert "location.reload" in html
    assert "2000" in html


def test_the_fingerprint_changes_when_a_node_is_written(small, graph):
    """What `--watch` polls. It has to notice a node being added AND a node being
    edited in place, which is what an agent loop does all day."""
    before = viz.fingerprint(small.root)

    graph.node("hyp-new", "id: hyp-new\ntype: hypothesis\nstatus: open")

    assert viz.fingerprint(small.root) != before


def test_the_fingerprint_is_stable_when_nothing_changes(small):
    assert viz.fingerprint(small.root) == viz.fingerprint(small.root)


def test_the_fingerprint_notices_a_rules_change(small):
    """The rules decide what renders — a legend line, a violation ring — so a graph.yaml
    edit has to redraw even though no node moved."""
    before = viz.fingerprint(small.root)

    (small.root / "graph.yaml").write_text("name: t\nrules: []\n", encoding="utf-8")

    assert viz.fingerprint(small.root) != before


def test_the_watch_block_is_cut_out_entirely_when_off(small):
    """Not merely guarded by a falsy constant: a file you emailed someone should contain
    no code that reloads it. It may still remember which legend you had open — that is
    the page being polite, not the page phoning home."""
    html = viz.render(small.root)

    assert "location.reload" not in html
    assert "beforeunload" not in html
    assert "__RELOAD_MS__" not in html


def test_watch_keeps_your_place_in_the_record(small):
    """The reload used to drop you back at the top of whatever you were reading, because
    `select()` resets the panel's scroll. Two seconds is not long enough to read a
    post-mortem."""
    html = viz.render(small.root, reload_ms=2000)

    assert "panel.scrollTop = seat.read" in html
    assert "read: panel.scrollTop" in html


def test_watch_does_not_reload_while_you_are_reading(small):
    """The pointer resting on the record is the clearest signal there is that a redraw
    should wait its turn."""
    html = viz.render(small.root, reload_ms=2000)

    assert "reading ? tick() : location.reload()" in html


def test_a_gate_column_leaves_room_for_the_tally_rail(graph):
    """Gate cards carry a rail showing what they killed and what they passed, so they are
    taller than every other card. Stepping every column by one fixed row height overlapped
    them by about the height of that rail."""
    graph.node("gate-a", "id: gate-a\ntype: gate\nstatus: active\ncreated: 2026-01-01")
    graph.node("gate-b", "id: gate-b\ntype: gate\nstatus: active\ncreated: 2026-01-02")
    graph.node("hyp-a", "id: hyp-a\ntype: hypothesis\nstatus: open\ncreated: 2026-01-01")
    graph.node("hyp-b", "id: hyp-b\ntype: hypothesis\nstatus: open\ncreated: 2026-01-02")

    pos = viz.layout(load(graph.root))["columns"]
    gate_step = pos["gate-b"][1] - pos["gate-a"][1]
    claim_step = pos["hyp-b"][1] - pos["hyp-a"][1]

    assert gate_step > claim_step
    assert gate_step >= viz.CARDH + viz.RAIL


def test_a_section_keeps_its_shape_in_the_panel(graph):
    """`core.section` collapses whitespace because the CLI prints it inline. The panel
    is a reading surface: collapsing turned every result table in a node body into one
    long row of pipes."""
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: open",
               "# A claim\n\n## The result\n| tau | b |\n|---|---|\n| 0-60 | 1.05 |\n")

    text = next(s["text"] for s in viz.payload(graph.root)["nodes"][0]["sections"]
                if s["title"] == "The result")

    assert text.count("\n") >= 2
    assert "| 0-60 | 1.05 |" in text


def test_a_node_that_breaks_the_graphs_rules_says_so(graph):
    """The page used to render a graph breaking its own rules exactly as it rendered a
    clean one, so adding a rule changed nothing you could see."""
    graph.rules("name: t\nrules:\n  - id: no-results-on-claims\n    when_type: hypothesis\n"
                "    forbid_fields: results\n    message: A claim is not a run.\n")
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: open\nresults:\n  auc: 0.9")

    data = viz.payload(graph.root)

    assert data["violations"]["hyp-x"][0]["rule"] == "no-results-on-claims"


def test_the_static_export_carries_no_timestamp(small):
    """Stamping every render would make a committed page differ from itself for the same
    graph, which is `git diff` noise on a file whose layout is deterministic on purpose."""
    a = viz.render(small.root)
    b = viz.render(small.root)

    assert a == b
    assert "__BUILT_AT__" not in a


def test_watch_stamps_when_it_was_built(small):
    """So the page can notice the watcher died instead of showing a green LIVE badge over
    a file that stopped updating hours ago."""
    import time as _t
    html = viz.render(small.root, reload_ms=2000)

    stamp = int(html.split("const BUILT_AT = ", 1)[1].split(" *", 1)[0])

    assert abs(stamp - _t.time()) < 5


def test_the_record_panel_can_be_widened_and_remembers_it(small):
    """Some post-mortems are a page of prose and a results table. The width is kept
    across reloads for the same reason the legend's state is: `--watch` must not undo
    what you just set."""
    html = viz.render(small.root)

    assert 'id=grip' in html
    assert "col-resize" in html
    assert 'sessionStorage.setItem(KEY, panel.getBoundingClientRect().width)' in html


# ------------------------------------------------------------------ record scaffold

def test_the_panel_scaffold_is_a_stable_well_formed_list():
    """The record panel rewrites a node's free-form body into this fixed scaffold. It is
    the one list both sides of the wire read, so it has to be stable: unique labels so the
    panel never renders two of the same field, and aliases so an agent's "kill criterion",
    "kill condition" and "when this is wrong" all land on one field."""
    scaffold = viz.PANEL_SECTIONS

    assert scaffold, "the scaffold is empty; the panel would fall back to body order"
    labels = [s["label"] for s in scaffold]
    assert len(labels) == len(set(labels)), f"panel labels must be unique: {labels}"
    for section in scaffold:
        assert section["label"], "each panel section needs a label"
        assert section["aliases"], "each panel section needs aliases to match agent titles"


def test_the_scaffold_reads_the_same_order_every_render(small):
    """Claim before the kill criterion, every time, for every graph. The order is the
    feature: two findings must read identically, so it is a fixed list, not body order."""
    labels = [s["label"] for s in viz.PANEL_SECTIONS]

    assert labels.index("Claim") < labels.index("Kill criterion")


def test_the_scaffold_is_injected_not_hardcoded(small):
    """One list, in the wire. Injected under `__PANEL_SECTIONS__` rather than duplicated,
    so the panel and the Python side cannot drift apart and disagree on the scaffold."""
    html = viz.render(small.root)

    assert '"Kill criterion"' in html


def test_the_record_panel_orders_by_the_scaffold(small):
    """The record renders in scaffold order, relabeled, not in whatever body order a
    non-compliant agent happened to use. This guards the block that would otherwise silently
    revert to body order, which is what made the panels read as unstructured. Verified at the
    source the way the render is client-side, not by matching runtime-built HTML."""
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


def test_a_rule_that_is_no_longer_alive_is_no_longer_badged(compressed):
    """`rule` is the badge on the card and the eyebrow on the record. A retracted general
    node stands for nothing -- validate reports the orphans it left behind -- and a page
    that still badges it says the compression holds when it does not."""
    compressed.node("finding-g", "id: finding-g\ntype: finding\nstatus: retracted\n"
                                 "created: 2026-01-05\nlinks:\n"
                                 "  - {rel: npx:supersedes, to: finding-1}\n"
                                 "  - {rel: npx:supersedes, to: finding-2}\n"
                                 "  - {rel: kn:survivedGate, to: gate-h}",
                    "# G\n\n## Covers\n- finding-1: a\n- finding-2: b\n")

    by = {n["id"]: n for n in viz.payload(compressed.root)["nodes"]}

    assert by["finding-g"]["rule"] is False
    # It still says what it claimed, and hangs none of it: the record marks the entries.
    assert by["finding-g"]["covers"] == ["finding-1", "finding-2"]
    assert by["finding-1"]["under"] is None and by["finding-2"]["under"] is None


def test_the_folded_view_still_holds_the_rule_when_every_specific_is_covered(compressed):
    """The fold must never leave the page with nothing to draw. A rule covers what it
    retired and nothing covers the rule, so it is there to be opened."""
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
    """Recursive compression: the outer rule retires the inner, the inner keeps its own
    findings. Reading only alive superseders drew the inner rule's two findings loose on
    the folded map, beside the rule that covers them."""
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


def test_the_folded_layout_holds_only_visible_nodes(compressed):
    p = viz.payload(compressed.root)

    assert set(p["folded"]) == {"question-q", "gate-h", "finding-3", "finding-4", "finding-g"}
    assert all({"columns", "map"} <= set(v) for v in p["folded"].values())
    assert set(p["folded_walls"]) <= set(p["folded"]) | {"unattached"}


def test_a_compression_moves_nothing_in_the_full_layout(compressed):
    before = {n["id"]: (n["columns"], n["map"]) for n in viz.payload(compressed.root)["nodes"]}
    # Compress finding-3 too, under a second rule, as the engine would leave the files.
    compressed.node("finding-3", _finding(3, "superseded"), "# 3\n")
    compressed.node("finding-r", "id: finding-r\ntype: finding\nstatus: alive\ncreated: 2026-01-06\nlinks:\n"
                                 "  - {rel: npx:supersedes, to: finding-3}\n"
                                 "  - {rel: kn:survivedGate, to: gate-h}",
                    "# R\n\n## Covers\n- finding-3: c\n")

    after = {n["id"]: (n["columns"], n["map"]) for n in viz.payload(compressed.root)["nodes"]}

    assert all(after[i] == before[i] for i in before)


def test_appending_moves_nothing_in_the_folded_layout(compressed):
    before = viz.payload(compressed.root)["folded"]
    compressed.node("finding-9", _finding(9), "# 9\n")

    after = viz.payload(compressed.root)["folded"]

    assert all(after[i] == before[i] for i in before)


def test_the_payload_carries_the_shape_and_the_clusters(compressed):
    compressed.node("finding-5", _finding(5), "# 5\n").node("finding-6", _finding(6), "# 6\n")

    p = viz.payload(compressed.root)

    assert p["shape"]["rules"] == 1 and p["shape"]["specifics"] == 3
    assert p["shape"]["clusters"] == 1
    assert p["clusters"][0]["shared"] == {"gate": "gate-h"}
    assert p["clusters"][0]["ids"] == ["finding-3", "finding-5", "finding-6", "finding-g"]


def test_the_folded_layout_equals_the_full_layout_when_nothing_is_compressed(small):
    """`_inherited` and the folded columns' basis are both no-ops on an uncompressed
    graph: nothing is `under` anything, so `visible` is every node, and the folded
    positions must be exactly the full ones."""
    p = viz.payload(small.root)

    full = {n["id"]: (n["columns"], n["map"]) for n in p["nodes"]}
    folded = {nid: (v["columns"], v["map"]) for nid, v in p["folded"].items()}

    assert folded == full


def test_the_folded_columns_share_the_full_views_headers(graph):
    """A type entirely covered still gets its column, and everything after it keeps its
    x, so a rule that just finished retiring the last hypothesis does not also shove
    every finding one column to the left."""
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


def test_a_max_alive_rule_reaches_the_page(compressed):
    (compressed.root / "graph.yaml").write_text(
        "name: t\nnode_types: [question, finding, gate]\nstatuses: [open, alive, superseded, active]\n"
        "rules:\n  - id: cap\n    max_alive: {type: finding, per: question, count: 4}\n    message: m\n",
        encoding="utf-8")

    p = viz.payload(compressed.root)

    assert p["graph"]["rules"][0]["max_alive"] == {"type": "finding", "per": "question", "count": 4}
    assert p["shape"]["budget"] == [{"question": "question-q", "type": "finding", "free": 2, "count": 4}]


def test_the_template_folds_covers_and_shows_the_shape(small):
    html = viz.render(small.root)

    for needle in ["DATA.folded", "DATA.shape", "DATA.clusters", 'id=f-folded', 'id=f-all',
                   'id=strip', 'id=clusters', 'id=cllegend', "function visible", "function posOf"]:
        assert needle in html, needle


def test_the_folded_switch_and_cluster_button_are_pressable_buttons(small):
    html = viz.render(small.root)

    assert re.search(r'<button id=f-folded aria-pressed=true>', html)
    assert re.search(r'<button id=f-all aria-pressed=false>', html)
    assert re.search(r'<button id=clusters aria-pressed=false>', html)


def test_the_watch_seat_remembers_the_fold(small):
    html = viz.render(small.root, reload_ms=2000)

    assert "fold: FOLD" in html and "open: [...open]" in html


def test_a_supersedes_cycle_cannot_hang_the_page(small):
    """Two alive nodes superseding each other pass `validate` today, and each is then
    `under` the other. Every walk that follows `under` carries a seen set, or opening
    both recurses until the stack gives out — a blank page from a legal graph."""
    html = viz.render(small.root)

    assert "function visible(id, seen)" in html
    assert "function posOf(n, seen)" in html
    assert html.count("seen.has") >= 3          # visible, posOf, the walk up to the card


def test_the_strip_prints_the_budget_rows_the_frontier_prints(graph):
    """The strip and `knoten frontier`'s header are built from the same `shape`, and the
    CLI prints up to three budget rows. A page that prints one says a graph with three
    caps is tighter than it is."""
    graph.rules("name: t\nnode_types: [question, finding, gate]\n"
                "statuses: [open, alive, superseded, active]\nrules:\n"
                "  - id: cap-q\n    max_alive: {type: finding, per: question, count: 4}\n"
                "    message: m\n"
                "  - id: cap-g\n    max_alive: {type: finding, per: graph, count: 9}\n"
                "    message: m\n")
    graph.node("question-q", Q, "# Q\n").node("gate-h", GATE_H, "# H\n")
    for i in (1, 2, 3):
        graph.node(f"finding-{i}", _finding(i), f"# {i}\n")

    p = viz.payload(graph.root)
    html = viz.render(graph.root)

    assert p["shape"] == ops.frontier(graph.root)["shape"]      # the CLI's own numbers
    assert len(p["shape"]["budget"]) == 2
    assert "rows.slice(0, 3)" in html                          # and its own three rows

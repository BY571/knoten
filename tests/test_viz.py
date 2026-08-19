"""One HTML file you can open from a plane.

The properties here are the ones that make the map usable rather than pretty: it must
not move under your feet as an agent appends to the graph, it must not reach the network,
and it must not execute what an agent wrote into a node body.
"""
import re

import pytest

from knoten import viz
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

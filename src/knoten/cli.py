"""knoten — a falsification-first research graph.

A graph is a FOLDER: graph.yaml (the rules) + nodes/ (the knowledge). One graph per
research topic. Each declares its own rules; the core knows nothing about any domain.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import webbrowser
from pathlib import Path

from . import attachments, ops, viz
from .commit import commit
from .core import GraphError, ID_RE, LOCK, find_root, load, node_path, today
from .hook import install as install_hook
from .validate import _csv, applies, load_config

# Keyed by the uppercase word `ops` puts in `verdict` — not by raw status, which is
# lowercase and includes values (open, active, …) this table has no symbol for.
MARK = {"ALIVE": "✓ ALIVE", "DEAD": "✗ DEAD", "RETRACTED": "⊘ RETRACTED"}


# ---------------------------------------------------------------- read commands

def _emit(payload: dict, as_json: bool, render) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        render(payload)


def _fail(payload: dict, reason, as_json: bool) -> int:
    """Every failure on this surface, one contract. --json keeps the structured payload on
    stdout even on failure, so a machine reader never has to check a second stream for the
    error; prose puts it on stderr, where every other command's GraphError goes."""
    if as_json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(f"knoten: {reason}", file=sys.stderr)
    return 1


def render_validate(payload: dict) -> None:
    print(f"{payload['nodes']} nodes\n")
    if payload["valid"]:
        print("  ✓ all rules pass")
        return
    for v in payload["violations"]:
        print(f"  ✗ {v['node']}\n      [{v['rule']}] {v['message']}")
    print(f"\n  {len(payload['violations'])} violation(s) — commit REJECTED")


def validate(root, as_json=False) -> int:
    payload = ops.validate(root)
    _emit(payload, as_json, render_validate)
    return 0 if payload["valid"] else 1


def render_query(payload: dict) -> None:
    print(f'"{payload["query"]}" → {payload["total"]} claim(s)\n')
    # Relevance order, NOT status order: the closest match must be first, because an
    # agent reads the top of this list and stops.
    for c in payload["claims"]:
        print(f"  [{MARK[c['verdict']]}] {c['id']}")
        for key, label in [("killed_by", "killed by"), ("survived_gates", "survived "),
                            ("retracted_by", "RETRACTED by"), ("superseded_by", "superseded by")]:
            if ts := c.get(key):
                print(f"      {label} : {', '.join(ts)}")
        if reopen := c.get("what_would_reopen_this"):
            print(f"      reopen if : {reopen[:140]}…")
        print()
    if payload["related"]:
        print("  also: " + ", ".join(payload["related"]))
    if note := payload.get("note"):
        # The guard against the one failure knoten exists to prevent (a false
        # "untested") lives in `note`. Dropping it in prose left an agent reading the
        # surface SKILL.md tells it to prefer with no caveat at all.
        print(f"\n  {note}")


def query(root, term, as_json=False) -> int:
    payload = ops.query(root, term)
    _emit(payload, as_json, render_query)
    return 0


def _pairs(pairs, msg):
    """Yield (key, raw value) for each `KEY=VALUE` string in `pairs` — only the key is
    stripped here, since `_kv` deliberately leaves its value alone while `_where` and
    `_links` strip theirs. `msg` is the caller's own error text, with `{}` for the
    offending item."""
    for p in pairs or []:
        if "=" not in p:
            raise GraphError(msg.format(p))
        k, v = p.split("=", 1)
        yield k.strip(), v


def _where(pairs) -> dict:
    """`--where cause=weak_baseline`, repeatable. Values for one field accumulate as
    alternatives, so `--where cause=a --where cause=b` reads as "a or b"."""
    out = {}
    for k, v in _pairs(pairs, "--where takes key=value, got '{}'"):
        out.setdefault(k, []).append(v.strip())
    return out


def render_index(payload: dict) -> None:
    shown = payload["nodes"]
    width = max((len(n["id"]) for n in shown), default=0)
    for n in shown:
        mark = MARK.get(n["verdict"], n["verdict"])
        tag = f"[{','.join(n['tags'])}]" if n["tags"] else ""
        print(f"  {n['id']:{width}}  {mark:12} {tag:24} {n['title']}")
    print(f"\n  {len(shown)} of {payload['total']} node(s)")
    if payload["truncated"]:
        # Never a silent cap: a truncated list reads as the whole graph.
        print("  (truncated — narrow with --tag/--status/--type, or raise --limit)")
    if note := payload.get("note"):
        print(f"\n  {note}")


def index(root, tags, status, ntype, where, since, limit, query=None, as_json=False) -> int:
    """The whole graph, one line per node. The answer to "have we done anything LIKE
    this?" that keyword search cannot give: a reader — human or agent — judges
    relatedness from the claims themselves."""
    payload = ops.index(root, query=query, tags=tags, status=status, type=ntype,
                        where=_where(where), since=since, limit=limit)
    _emit(payload, as_json, render_index)
    return 0


def render_gates(payload: dict) -> None:
    for g in payload["gates"]:
        killed, survived = g["killed"], g["survived"]
        record = f"killed {len(killed)}, survived by {len(survived)}" if killed or survived \
            else "never applied"
        print(f"  {g['id']}  ({record})")
        print(f"    {g['title']}")
        if rule := g.get("rule"):
            print(f"    the rule : {rule[:160]}")
        print()
    if note := payload.get("note"):
        print(f"  {note}")


def gates_cmd(root, as_json=False) -> int:
    """What every claim in this graph has to survive. Read it before you design the
    experiment, not after the commit is refused."""
    payload = ops.gates(root)
    _emit(payload, as_json, render_gates)
    return 0


def render_frontier(payload: dict) -> None:
    if payload["open"]:
        print("  OPEN — started, never settled")
        for n in payload["open"]:
            print(f"    {n['id']:24}  {n['title']}")
    if payload["reopenable"]:
        print("\n  REOPENABLE — died, but said what would bring them back")
        for n in payload["reopenable"]:
            print(f"    {n['id']:24}  {n['title']}")
            print(f"      reopen if : {n['reopen_if'][:120]}…")
    if payload["untested_gates"]:
        print("\n  UNTESTED GATES — no claim has been through them")
        for n in payload["untested_gates"]:
            print(f"    {n['id']:24}  {n['title']}")
    if not (payload["open"] or payload["reopenable"] or payload["untested_gates"]):
        print("  nothing open, nothing reopenable, every gate has fired.")
    if note := payload.get("note"):
        print(f"\n  {note}")


def frontier_cmd(root, as_json=False) -> int:
    """The one screen that answers "what now?". Kept short on purpose — a frontier you
    have to scroll is a frontier nobody reads."""
    payload = ops.frontier(root)
    _emit(payload, as_json, render_frontier)
    return 0


def render_path(payload: dict) -> None:
    p = payload["path"]
    if p is None:
        print(payload["note"])
        return
    print(f"research path {p[0]['node']} → {p[-1]['node']}:\n")
    for i, hop in enumerate(p):
        rel = hop.get("via")
        print("  " * i + (f"└─ {rel} → " if rel else "") + hop["node"])


def path(root, a, b, as_json=False) -> int:
    payload = ops.path(root, a, b)
    _emit(payload, as_json, render_path)
    return 0


def viz_cmd(root, out, show, watch) -> int:
    """One HTML file. Read-only, self-contained, no server.

    `--watch` rewrites it whenever the graph changes and tells the open page to reload,
    so you can leave it up beside an agent loop. Still no server: the page reloads
    itself from disk, and it remembers which view you were in, what you had selected and
    where you had panned to.
    """
    reload_ms = int(watch * 1000) if watch else 0
    dest = viz.write(root, Path(out), reload_ms)
    print(f"wrote {dest}  ({dest.stat().st_size // 1024} KB)")
    if show:
        webbrowser.open(dest.resolve().as_uri())
    if not watch:
        return 0

    # Flushed: a watch loop whose output only appears when it exits is a watch loop
    # you cannot tell is running.
    print(f"watching {root}/ — the page reloads itself every {watch:g}s. ctrl-c to stop.",
          flush=True)
    seen = viz.fingerprint(root)
    try:
        while True:
            time.sleep(watch)
            if (now := viz.fingerprint(root)) == seen:
                continue
            seen = now
            try:
                viz.write(root, Path(out), reload_ms)
                print(f"  {today()}  redrew {len(load(root))} nodes", flush=True)
            except GraphError as e:
                # A half-written node is normal while an agent is mid-commit: say so and
                # keep the last good page up rather than tearing the window down.
                print(f"  skipped: {e}", flush=True)
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


def hook(root, force) -> int:
    h = install_hook(root, force=force)
    print(f"  ✓ installed {h}")
    print("    `git commit` now runs `knoten validate` and refuses a broken graph.")
    return 0


def render_get(payload: dict) -> None:
    print(f"{payload['id']}  [{MARK.get(payload['verdict'], payload['verdict'])}]  "
          f"type={payload['type']}\n")
    for l in payload["links"]:
        print(f"  {l['rel']:22} -> {l['to']}")
    for b in payload["backlinks"]:
        print(f"  {b['rel']:22} <- {b['to']}")
    for label, d in [("repro", payload.get("repro")), ("results", payload.get("results"))]:
        if d:
            print(f"\n  {label}:")
            for k, v in d.items():
                print(f"    {k}: {v}")
    if atts := payload.get("attachment_files"):
        print("\n  attachments:")
        for a in atts:
            sz = f"{a['size_kb']:.1f} KB" if "size_kb" in a else "MISSING"
            print(f"    {a['path']}  ({sz})")


def show(root, nid, as_json=False) -> int:
    payload = ops.get(root, nid)
    if err := payload.get("error"):
        return _fail(payload, err, as_json)
    _emit(payload, as_json, render_get)
    return 0


# ---------------------------------------------------------------- write commands

def _read(arg: str) -> str:
    """A file path, or `-` for stdin. Frontmatter and bodies are multi-line YAML and
    markdown; passing them as shell arguments is how quoting bugs get into a research
    record."""
    return sys.stdin.read() if arg == "-" else Path(arg).read_text(encoding="utf-8")


def _kv(pairs) -> dict:
    """`--result acc=0.7`, repeatable. Typed rather than left as strings, because
    `require_result_min` compares numerically."""
    out = {}
    for k, v in _pairs(pairs, "--result takes key=value, got '{}'"):
        # Deliberately NOT stripped — alone among the four parsers. `--result "note= fine "`
        # writes ' fine ' to disk as-is. That asymmetry is existing behaviour, kept.
        try:
            v = float(v)
        except ValueError:
            pass
        out[k] = v
    return out


def _fields(pairs) -> dict:
    """`--field cause=weak_baseline`, repeatable. Left as STRINGS, unlike `_kv`.

    `_kv` coerces because `require_result_min` compares numerically. `require_field_one_of`
    and `--where` both compare with `str()`, so coercing `--field seed=2` to 2.0 made it
    match nothing the graph declared — and the refusal quoted `seed=2.0`, a value the user
    never typed. Stripped, because this is the write side of `--where`, which strips: the
    two must round-trip.
    """
    return {k: v.strip() for k, v in _pairs(pairs, "--field takes key=value, got '{}'")}


def _links(pairs) -> list[dict]:
    """`--link kn:killedByGate=gate-x`, repeatable."""
    out = []
    for rel, to in _pairs(pairs, "--link takes rel=to, got '{}'"):
        out.append({"rel": rel, "to": to.strip()})
    return out


def render_commit(payload: dict) -> None:
    print(f"  + {payload['path']}  ({payload['graph_size']} nodes)")
    if warning := payload.get("warning"):
        print(f"\n  ! {warning}")
        for s in payload["similar"]:
            print(f"    {s['id']}  [{MARK.get(s['verdict'], s['verdict'])}]  {s['title']}")


def commit_cmd(root, nid, frontmatter, body, as_json) -> int:
    res = commit(root, nid, _read(frontmatter), _read(body))
    if res["status"] == "REJECTED":
        return _fail(res, res.get("reason") or "; ".join(
            f"[{v['rule']}] {v['message']}" for v in res["violations"]), as_json)
    _emit(res, as_json, render_commit)
    return 0


def render_update(payload: dict) -> None:
    print(f"  {payload['node']} -> {payload['node_status']}")


def update_cmd(root, nid, status, append, results, links, fields, as_json) -> int:
    # ops.update() is the ONE shape for both outcomes — this used to build its own
    # dict here, and a different one on a second surface since removed, and they drifted.
    payload = ops.update(root, nid, status=status, append=_read(append) if append else None,
                         results=_kv(results), links=_links(links), fields=_fields(fields))
    if payload["status"] == "REJECTED":
        return _fail(payload, payload["reason"], as_json)
    _emit(payload, as_json, render_update)
    return 0


def attach(root, nid, files) -> int:
    res = attachments.attach(root, nid, files)
    for w in res.warnings:
        print(f"  ! {w}")
    for name in res.added:
        print(f"  + attachments/{nid}/{name}")
    if res.embedded:
        print(f"  embedded {len(res.embedded)} image(s) in the node body")
    return 0


def detach(root, nid, name) -> int:
    attachments.detach(root, nid, name)
    print(f"  - detached {name} from {nid}")
    return 0


TEMPLATE_GRAPH = """\
# {name} — a knoten research graph.
#
# The core knows NOTHING about this domain. Every rule below is declared HERE, as
# data. Write a rule only when you have a corpse: a rule without a body behind it is
# just friction.
name: {name}
description: TODO — what question is this graph about?

# Enforced. A node whose type or status is not declared here is a typo — and a claim with
# a typo'd status silently drops out of every query. Edit these for YOUR topic.
#
# The meanings are not decoration: knoten defines none of these words, so this is the only
# place they ARE defined, and `knoten viz` shows them beside each column.
node_types:
  question:   what this graph exists to answer — a question, a statement or a task
  source:     where the work came from — a paper, dataset, search, or your own intuition
  idea:       what you took from a source; a direction, not yet a testable claim
  hypothesis: a falsifiable claim derived from an idea
  experiment: the test built to verify or falsify a hypothesis
  finding:    what the experiment showed, expected or not — new ideas come from these
  retraction: a claim withdrawn after the fact
  gate:       a standing rule every claim must survive; a bar, not a stage
statuses:   [open, alive, dead, retracted, superseded, active]

# The axis `knoten index --tag` filters on. Declare them and a typo is a violation;
# declare none and tagging is free. Add tags as the topic tells you what they are.
# tags: [decoding, evaluation]

# Reused standards: mp:supports / mp:challenges (Micropublications),
#   npx:retracts / npx:supersedes (Nanopublications),
#   prov:wasDerivedFrom / prov:used (PROV-O)
# knoten adds:  kn:survivedGate  (claim -> the gate it PASSED)
#               kn:killedByGate  (claim -> the gate that KILLED it)
#               kn:blockedBy     (claim -> a structural wall, not a result)

rules:
  # --- the two that make a graph worth keeping -----------------------------------
  - id: live-claims-must-cite-their-gates
    when_status: alive
    when_type: hypothesis, finding
    require_edge: kn:survivedGate
    message: An unchallenged claim is not a finding, it is a hope.

  - id: dead-claims-must-say-why
    when_status: dead, retracted
    require_sections: Why it died, What would reopen this
    message: The post-mortem IS the asset. A dead end must become a standing offer.

  # --- the loop: every step records where it came from ---------------------------
  # Delete any of these that your topic does not want. They are this graph's opinion,
  # not knoten's: the core checks only what is declared here.
  - id: sources-must-be-findable-again
    when_type: source
    require_field: origin
    message: >
      A source you cannot go back to is a rumour. Record a url, a doi, a file path
      or "own intuition" if the work started in your head.

  - id: ideas-must-cite-what-prompted-them
    when_type: idea
    require_edge_target: {{rel: prov:wasDerivedFrom, type: question, min: 1}}
    message: >
      An idea that cites nothing cannot be traced back to the question it serves.
      Cite the question, or the source you read.

  - id: hypotheses-must-say-what-they-do-not-test
    when_type: hypothesis
    require_sections: The claim, What this does not test
    message: >
      A hypothesis is a pull request: one change, one thing tested. If you cannot say
      what this does NOT test, it is testing more than one thing. Open a second
      hypothesis instead of widening this one.

  - id: hypotheses-are-claims-not-runs
    when_type: hypothesis
    forbid_fields: results, repro
    message: >
      A hypothesis is a claim. The run that tested it is an experiment and the number it
      produced is a finding — put `results` and `repro` on those. One node holding all
      three is how a loop stops having stages.

  - id: alive-hypotheses-must-have-been-tested
    when_type: hypothesis
    when_status: alive
    require_backlink: {{rel: kn:testedBy, type: experiment, min: 1}}
    message: >
      A hypothesis is alive because something tested it. Record the run as an experiment
      that `kn:tests` this node.

  - id: experiments-must-be-rerunnable
    when_type: experiment
    require_sections: Setup, How to reproduce, Result
    message: An experiment I cannot rerun is an anecdote.
"""

TEMPLATE_QUESTION = """\
---
id: question-{name}
type: question
status: open
---
# TODO — the question, statement or task this graph exists to answer

## Why it matters
<what changes once this is answered — and for whom>

## What would count as an answer
<the shape of a result that would settle it, so you can tell when to stop>

Replace this with the real question. Everything else in this graph descends from it.
"""

TEMPLATE_GATE = """\
---
id: gate-example
type: gate
status: active
---
# Gate: <the test every claim in this graph must survive>

## The rule
<what to run>

## Why it exists
<what went wrong that made this necessary>

Replace this with a real gate. Delete it if you have none yet — but you will.
"""


def new(root, ntype, nid, status) -> int:
    """Scaffold a node carrying every section and field THIS graph's rules demand.

    Nothing here is knoten's opinion — it reads the graph's own declarations. The values
    are TODO on purpose: `knoten validate` then names the ones you still owe it, so `new`
    + `validate` is a checklist rather than a guessing game.
    """
    nf = node_path(root, nid)
    if nf.exists():
        raise GraphError(f"'{nid}' already exists. Supersede or retract it — corrections "
                         f"are nodes, not edits.")

    cfg = load_config(root)
    # `new` used to skip this while knoten commit enforced it: the same node was accepted
    # by one entry point and rejected by the other.
    for field, declared in [("type", cfg.get("node_types")), ("status", cfg.get("statuses"))]:
        value = ntype if field == "type" else status
        if declared and value not in declared:
            raise GraphError(f"{field} '{value}' is not declared in graph.yaml "
                             f"({field}s: {', '.join(map(str, declared))})")

    sections, results, fields, blanks = [], [], {}, []
    for r in cfg.get("rules", []):
        if not applies(status, ntype, r):
            continue
        sections += _csv(r.get("require_sections"))
        results += [k for k in [r.get("require_result")] if k]
        results += list(r.get("require_result_min") or {})
        fields.update(r.get("require_field_one_of") or {})
        blanks += [k for k in [r.get("require_field")] if k]

    fm = [f"id: {nid}", f"type: {ntype}", f"status: {status}", f"created: {today()}"]
    # The allowed values go in the scaffold as a comment: a closed vocabulary the author
    # has to go and look up is a closed vocabulary they will guess at.
    fm += [f"{k}: TODO   # one of: {', '.join(map(str, v))}" for k, v in fields.items()]
    # Left EMPTY, not TODO. `require_field` takes any non-empty value, so a placeholder
    # would satisfy it and `new` + `validate` would stop being a checklist. Blank is both
    # the prompt and the violation.
    fm += [f"{k}:" for k in dict.fromkeys(blanks) if k not in fields]
    if results:
        fm.append("results:")
        fm += [f"  {k}: TODO" for k in dict.fromkeys(results)]

    body = ["# TODO — state the claim in one line\n"]
    body += [f"## {s}\nTODO\n" for s in dict.fromkeys(sections)]

    nf.write_text("---\n" + "\n".join(fm) + "\n---\n\n" + "\n".join(body), encoding="utf-8")
    print(f"  + nodes/{nid}.md  ({ntype}, {status})")
    if wanted := ([f"## {s}" for s in dict.fromkeys(sections)]
                  + list(dict.fromkeys(results)) + list(fields)):
        print(f"    pre-filled what THIS graph's rules require: {', '.join(wanted)}")
    return 0


def init(name) -> int:
    if not ID_RE.match(name):
        raise GraphError(f"'{name}' is not a valid graph name (use kebab-case: my-topic)")
    root = Path.cwd() / name
    if root.exists():
        raise GraphError(f"{root} already exists")
    (root / "nodes").mkdir(parents=True)
    (root / "graph.yaml").write_text(TEMPLATE_GRAPH.format(name=name), encoding="utf-8")
    # knoten's own write lock. Nobody should have to see it in `git status`.
    (root / ".gitignore").write_text(f"{LOCK}\n", encoding="utf-8")
    (root / "nodes" / f"question-{name}.md").write_text(
        TEMPLATE_QUESTION.format(name=name), encoding="utf-8")
    (root / "nodes" / "gate-example.md").write_text(TEMPLATE_GATE, encoding="utf-8")
    print(f"created graph '{name}'\n")
    print(f"  {name}/nodes/question-{name}.md  <- start here: what this graph answers")
    print(f"  {name}/graph.yaml   <- edit the rules for THIS topic")
    print(f"  {name}/nodes/       <- one markdown file per question / source / idea / claim\n")
    print(f"  next:  cd {name} && git init && knoten hook")
    print("         (the hook makes `git commit` refuse a graph that breaks its own rules)")
    return 0


# ---------------------------------------------------------------- entry point

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="knoten", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("init", help="start a NEW graph for a topic")
    s.add_argument("name")

    s = sub.add_parser("validate", help="enforce THIS graph's declared rules")
    s.add_argument("--json", action="store_true", help="emit the raw payload")

    s = sub.add_parser("new", help="scaffold a node with whatever the rules demand")
    s.add_argument("type")
    s.add_argument("id")
    s.add_argument("--status", default="open")

    s = sub.add_parser("query", help='"has this been tried?" -> verdicts + causes of death')
    s.add_argument("term")
    s.add_argument("--json", action="store_true", help="emit the raw payload")

    s = sub.add_parser("index", help="the whole graph, one line per node")
    s.add_argument("--query", help="rank the rows by relevance to this, instead of by id")
    s.add_argument("--tag", action="append", help="keep nodes carrying this tag (repeatable)")
    s.add_argument("--status", action="append")
    s.add_argument("--type", action="append")
    s.add_argument("--where", action="append", metavar="KEY=VALUE",
                   help="keep nodes whose frontmatter KEY is VALUE (repeatable)")
    s.add_argument("--since", metavar="YYYY-MM-DD",
                   help="only nodes created or updated on/after this day")
    s.add_argument("--limit", type=int,
                   help=f"0 = the default cap ({ops.INDEX_LIMIT}); never uncapped, so a "
                        f"truncated list can't silently read as the whole graph")
    s.add_argument("--json", action="store_true", help="emit the raw payload")

    s = sub.add_parser("frontier", help="what should I work on next?")
    s.add_argument("--json", action="store_true", help="emit the raw payload")

    s = sub.add_parser("gates", help="what must a claim survive here?")
    s.add_argument("--json", action="store_true", help="emit the raw payload")

    s = sub.add_parser("path", help="how did we get from A to B?")
    s.add_argument("a")
    s.add_argument("b")
    s.add_argument("--json", action="store_true", help="emit the raw payload")

    s = sub.add_parser("viz", help="write the graph as one self-contained HTML file")
    s.add_argument("-o", "--out", default="knoten.html", help="where to write it")
    s.add_argument("--open", dest="show", action="store_true", help="open it when done")
    s.add_argument("--watch", nargs="?", type=float, const=2.0, default=None,
                   metavar="SECONDS",
                   help="redraw when the graph changes and reload the page (default 2s)")

    s = sub.add_parser("hook", help="install the git pre-commit gate")
    s.add_argument("--force", action="store_true",
                   help="overwrite a pre-commit hook knoten did not write")

    s = sub.add_parser("show", help="the node, its edges and its attachments")
    s.add_argument("node")
    s.add_argument("--json", action="store_true", help="emit the raw payload")

    s = sub.add_parser("commit", help="file a new claim — gate-checked before it touches disk")
    s.add_argument("id")
    s.add_argument("--frontmatter", required=True, metavar="FILE",
                   help="YAML frontmatter (no --- fences) — file path, or - for stdin")
    s.add_argument("--body", required=True, metavar="FILE",
                   help="markdown body — file path, or - for stdin")
    s.add_argument("--json", action="store_true", help="emit the raw payload")

    s = sub.add_parser("update", help="move a node through its lifecycle and append to it")
    s.add_argument("id")
    s.add_argument("--status", help="the new status, e.g. dead")
    s.add_argument("--append", metavar="FILE",
                   help="markdown to append — file path, or - for stdin")
    s.add_argument("--result", action="append", metavar="KEY=VALUE",
                   help="result to record (repeatable)")
    s.add_argument("--field", action="append", metavar="KEY=VALUE",
                   help="set any top-level frontmatter field, including one already "
                        "recorded (repeatable). The graph's rules decide what is "
                        "accepted.")
    s.add_argument("--link", action="append", metavar="REL=TO",
                   help="edge to add, e.g. kn:killedByGate=gate-x (repeatable)")
    s.add_argument("--json", action="store_true", help="emit the raw payload")

    s = sub.add_parser("attach", help="attach a script / plot / notebook to a node")
    s.add_argument("node")
    s.add_argument("files", nargs="+")

    s = sub.add_parser("detach", help="remove one")
    s.add_argument("node")
    s.add_argument("file")

    return p


def main(argv=None) -> int:
    args = _parser().parse_args(argv if argv is not None else sys.argv[1:])
    try:
        if args.cmd is None or args.cmd == "validate":
            # No subcommand never parsed a `validate` subparser, so it has no --json.
            return validate(find_root(), getattr(args, "json", False))
        if args.cmd == "init":
            return init(args.name)

        root = find_root()
        return {
            "query":  lambda: query(root, args.term, args.json),
            "path":   lambda: path(root, args.a, args.b, args.json),
            "frontier": lambda: frontier_cmd(root, args.json),
            "gates":  lambda: gates_cmd(root, args.json),
            "index":  lambda: index(root, tags=args.tag, status=args.status, ntype=args.type,
                                    where=args.where, since=args.since, limit=args.limit,
                                    query=args.query, as_json=args.json),
            "new":    lambda: new(root, args.type, args.id, args.status),
            "show":   lambda: show(root, args.node, args.json),
            "commit": lambda: commit_cmd(root, nid=args.id, frontmatter=args.frontmatter,
                                         body=args.body, as_json=args.json),
            "update": lambda: update_cmd(root, nid=args.id, status=args.status,
                                         append=args.append, results=args.result,
                                         links=args.link, fields=args.field,
                                         as_json=args.json),
            "viz":    lambda: viz_cmd(root, args.out, args.show, args.watch),
            "hook":   lambda: hook(root, args.force),
            "attach": lambda: attach(root, args.node, args.files),
            "detach": lambda: detach(root, args.node, args.file),
        }[args.cmd]()
    except (GraphError, OSError) as e:
        # OSError: a typo'd --frontmatter/--body/--append path is ordinary user error,
        # not a traceback. Every entry point owes the user one line, not a stack.
        return _fail({"error": str(e)}, e, getattr(args, "json", False))


def cli() -> None:
    sys.exit(main())


if __name__ == "__main__":
    cli()

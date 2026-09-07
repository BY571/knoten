"""knoten — a falsification-first research graph.

A graph is a FOLDER: graph.yaml (the rules) + nodes/ (the knowledge). One graph per
research topic. Each declares its own rules; the core knows nothing about any domain.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import webbrowser
from pathlib import Path

from . import attachments, ops, viz
from . import identity as C
from .commit import commit
from .core import GraphError, ID_RE, LOCK, _STOP, find_root, load, node_path, today
from .hook import install as install_hook, install_server
from .identity import ensure_key, public_line
from .registry import ROLES, Registry
from .serve import make_server
from . import gate
from . import remote
from .validate import _csv, applies, load_config

# Keyed by the uppercase word `ops` puts in `verdict` — not by raw status, which is
# lowercase and includes values (open, active, …) this table has no symbol for.
MARK = {"ALIVE": "✓ ALIVE", "DEAD": "✗ DEAD", "RETRACTED": "⊘ RETRACTED", "rule": "◆ rule"}


# ---------------------------------------------------------------- read commands

def _emit(payload: dict, as_json: bool, render) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        render(payload)


def _fail(payload: dict, reason, as_json: bool) -> int:
    """Every failure on this surface, one contract. --json keeps the payload on stdout
    even on failure, so a machine reader never checks a second stream for the error."""
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
        # `note` carries the caveat against a false "untested", the one failure knoten
        # exists to prevent. Prose must print it too.
        print(f"\n  {note}")


def _pairs(pairs, msg):
    """(key, raw value) for each `KEY=VALUE` in `pairs`. Only the key is stripped: `_kv`
    leaves its value alone where `_where` and `_links` strip theirs. `msg` is the caller's
    error text, with `{}` for the offending item."""
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


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


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
    if payload.get("hidden"):
        print(f"  {payload['hidden']} superseded hidden; --all shows them")
    if note := payload.get("note"):
        print(f"\n  {note}")


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


BANDS = [("open", "OPEN — started, never settled"),
         ("unchecked", "UNCHECKED — alive, but no gate has ruled on them"),
         ("reopenable", "REOPENABLE — died, but said what would bring them back"),
         ("untested_gates", "UNTESTED GATES — no claim has been through them")]


def render_frontier(payload: dict) -> None:
    s = payload["shape"]
    head = [f"{_plural(s['rules'], 'rule')} over {_plural(s['specifics'], 'specific')}"]
    if s["clusters"]:
        head.append(_plural(s["clusters"], "compressible cluster"))
    print("  " + " · ".join(head))
    if payload["compressible"]:
        print("\n  COMPRESSIBLE — do these before the next experiment")
        for c in payload["compressible"]:
            key = next(iter(c["shared"].values()))
            print(f"    {c['question']}  ·  {key}  ·  "
                  f"{_plural(len(c['ids']), 'alive ' + c['type'])}")
            more = f", +{len(c['ids']) - 8} more" if len(c["ids"]) > 8 else ""
            print(f"      {', '.join(c['ids'][:8])}{more}")
    for key, heading in BANDS:
        if not payload[key]:
            continue
        print(f"\n  {heading}")
        for n in payload[key]:
            print(f"    {n['id']:24}  {n['title']}")
            if offer := n.get("reopen_if"):
                print(f"      reopen if : {offer[:120]}…")
    if not any(payload[key] for key, _ in BANDS):
        print("  nothing open, nothing reopenable, every gate has fired.")
    if note := payload.get("note"):
        print(f"\n  {note}")


def render_path(payload: dict) -> None:
    p = payload["path"]
    if p is None:
        print(payload["note"])
        return
    print(f"research path {p[0]['node']} → {p[-1]['node']}:\n")
    for i, hop in enumerate(p):
        rel = hop.get("via")
        print("  " * i + (f"└─ {rel} → " if rel else "") + hop["node"])


def viz_cmd(root, out, show, watch) -> int:
    """One HTML file. Read-only, self-contained, no server. `--watch` rewrites it when
    the graph changes and the page reloads itself from disk, remembering which view you
    were in, what you had selected and where you had panned to."""
    reload_ms = int(watch * 1000) if watch else 0
    dest = viz.write(root, Path(out), reload_ms)
    print(f"wrote {dest}  ({dest.stat().st_size // 1024} KB)")
    if show:
        webbrowser.open(dest.resolve().as_uri())
    if not watch:
        return 0

    # Flushed: output that only appears on exit cannot show the loop is running.
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
                # A half-written node is normal mid-commit: keep the last good page up.
                print(f"  skipped: {e}", flush=True)
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


# ---------------------------------------------------------------- remote and server

def hook(root, force) -> int:
    h = install_hook(root, force=force)
    print(f"  ✓ installed {h}")
    print("    `git commit` now runs `knoten validate` and refuses a broken graph.")
    return 0


def server_hook(repo, force) -> int:
    """The gate for a graph several people push to. Run it ON the server, in the repo
    they push to: there is no graph there to `find_root`."""
    h = install_server(Path(repo), force=force)
    print(f"  ✓ installed {h}")
    print("    `git push` now runs `knoten validate` on the pushed tree and refuses a")
    print("    broken graph — for every contributor, including the ones who never ran")
    print("    `knoten hook` and the ones who used `git commit --no-verify`.")
    return 0


def remote_cmd(root, args) -> int:
    if args.remote_cmd == "create":
        url = remote.remote_create(root, args.name, args.on, admin=args.admin,
                                   owner_secret=args.owner_secret)
        print(f"  ✓ {url}")
        print("    invite someone:  knoten invite <name> --role write")
        return 0
    signs_as = remote.remote_add(root, args.url, me=args.me)
    print("  ✓ origin set. `knoten pull` and `knoten push` now use it.")
    if signs_as:
        print(f"    this clone signs as {signs_as}")
    return 0


def invite_cmd(root, name, role, days) -> int:
    code = remote.invite(root, name, role, days)
    print(f"  ✓ {name} may join as {role} for {days} day(s). Send them this code, once:")
    print(f"    {code}")
    return 0


def render_invites(payload: dict) -> None:
    rows = payload["invites"]
    if not rows:
        print("  no open invites")
        return
    width = max(len(r["name"]) for r in rows)
    for r in rows:
        by = f"  (invited by {r['by']})" if r["by"] else ""
        print(f"  {r['name']:{width}}  {r['role']:6}  expires {r['expires'][:10]}{by}")


def invites_cmd(root, as_json=False) -> int:
    """Who was invited and has not arrived. An invite is a bearer secret sitting on the
    server, and without this an admin cannot tell a forgotten one from a revoked one."""
    _emit({"invites": remote.invites(root)}, as_json, render_invites)
    return 0


def revoke_cmd(root, name) -> int:
    remote.revoke(root, name)
    print(f"  ✓ {name} is revoked and can no longer connect. What they already pushed stays.")
    return 0


def serve_cmd(data, bind) -> int:
    """Run on the server. Prints the owner secret the first time a data directory is
    used: that is the one moment the owner is certainly at the keyboard."""
    host, _, port_str = bind.rpartition(":")
    host = host or "127.0.0.1"
    # Parse and validate the port before any file is written; a crash after the owner
    # secret exists orphans it, shown to nobody, forever.
    try:
        port = int(port_str)
    except ValueError:
        raise GraphError(f"--bind wants HOST:PORT with a numeric port, got '{bind}'") from None
    reg = Registry(Path(data))
    srv = make_server(reg, host, port)
    # Only now, after the server socket is open, check and display the owner secret.
    # The registry says whether it made one, rather than this guessing from the file:
    # an `owner` file that existed but was empty read as "already shown" and the server
    # came up with a secret nobody had ever seen.
    secret, minted = reg.ensure_owner_secret()
    if minted:
        print(f"  owner secret (shown once, keep it somewhere safe): {secret}")
    if host not in ("127.0.0.1", "localhost"):
        print("  warning: plain HTTP on a non-local address. Put TLS in front (a reverse "
              "proxy or a tunnel) before anyone outside this machine connects.",
              file=sys.stderr)
    print(f"  serving {reg.data} on http://{host}:{srv.server_address[1]}  (ctrl-c to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


def render_get(payload: dict) -> None:
    print(f"{payload['id']}  [{MARK.get(payload['verdict'], payload['verdict'])}]  "
          f"type={payload['type']}")
    if warning := payload.get("warning"):
        print(f"  ! {warning}")
    print()
    for l in payload["links"]:
        print(f"  {l['rel']:22} -> {l['to']}")
    for b in payload["backlinks"]:
        print(f"  {b['rel']:22} <- {b['to']}")
    if covers := payload.get("covers"):
        print("\n  covers:")
        for line in covers.splitlines():
            print(f"    {line}")
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


# Every read command: build the payload from `ops`, then print it as JSON or as prose.
# One table, so a command cannot grow a second renderer or forget the --json contract.
READ = {
    "validate": (lambda a, root: ops.validate(root), render_validate),
    "query":    (lambda a, root: ops.query(root, a.term), render_query),
    "index":    (lambda a, root: ops.index(root, query=a.query, tags=a.tag, status=a.status,
                                           type=a.type, where=_where(a.where), since=a.since,
                                           limit=a.limit, all=a.all), render_index),
    "frontier": (lambda a, root: ops.frontier(root), render_frontier),
    "gates":    (lambda a, root: ops.gates(root), render_gates),
    "path":     (lambda a, root: ops.path(root, a.a, a.b), render_path),
    "show":     (lambda a, root: ops.get(root, a.node), render_get),
}


def read_cmd(cmd: str, args, root) -> int:
    build, render = READ[cmd]
    payload = build(args, root)
    if err := payload.get("error"):
        return _fail(payload, err, args.json)
    _emit(payload, args.json, render)
    # `validate` is the one read that decides an exit code: the hook runs it.
    return 1 if payload.get("valid") is False else 0


# ---------------------------------------------------------------- write commands

def _read(arg: str) -> str:
    """A file path, or `-` for stdin. Frontmatter and bodies are multi-line YAML and
    markdown; passing them as shell arguments is how quoting bugs get into a research
    record."""
    return sys.stdin.read() if arg == "-" else Path(arg).read_text(encoding="utf-8")


def _kv(pairs) -> dict:
    """`--result acc=0.7`, repeatable. Typed, not left as strings, because
    `require_result_min` compares numerically. Values are NOT stripped, alone among the
    four parsers: `--result "note= fine "` writes ' fine ' as-is."""
    out = {}
    for k, v in _pairs(pairs, "--result takes key=value, got '{}'"):
        try:
            v = float(v)
        except ValueError:
            pass
        out[k] = v
    return out


def _fields(pairs) -> dict:
    """`--field cause=weak_baseline`, repeatable. Left as STRINGS unlike `_kv`, because
    `require_field_one_of` and `--where` both compare with `str()` and `seed=2.0` matches
    nothing the graph declared. Stripped, so it round-trips with `--where`."""
    return {k: v.strip() for k, v in _pairs(pairs, "--field takes key=value, got '{}'")}


def _links(pairs) -> list[dict]:
    """`--link kn:killedByGate=gate-x`, repeatable."""
    out = []
    for rel, to in _pairs(pairs, "--link takes rel=to, got '{}'"):
        out.append({"rel": rel, "to": to.strip()})
    return out


def render_reward(c: dict) -> None:
    """What a compression freed. Printed by both commit and update, so a general node
    built either way is told the same thing."""
    if len(c["targets"]) == 1:
        t = c["targets"][0]
        print(f"    replaces {t}; {t} is now superseded")
        return
    print(f"    compressed {_plural(len(c['targets']), c['type'])} into 1 under "
          f"{c['question'] or '(no question)'}")
    print(f"    survived {_plural(c['gates'], 'gate')}, one more than any of them faced alone"
          if c["gates_bonus"] else
          f"    survived the {_plural(c['gates'], 'gate')} they faced")
    print(f"    this graph now stands on {_plural(c['rules'], 'rule')} and "
          f"{_plural(c['specifics'], 'specific')}")


def render_commit(payload: dict) -> None:
    print(f"  + {payload['path']}  ({payload['graph_size']} nodes)")
    if warning := payload.get("warning"):
        print(f"\n  ! {warning}")
        for s in payload["similar"]:
            print(f"    {s['id']}  [{MARK.get(s['verdict'], s['verdict'])}]  {s['title']}")
    if compressed := payload.get("compressed"):
        render_reward(compressed)


def _rejected(res: dict, as_json: bool = False) -> int:
    """A REJECTED payload from `commit`, printed the one way: `reason` when the candidate
    never parsed, the violations otherwise."""
    return _fail(res, res.get("reason") or "; ".join(
        f"[{v['rule']}] {v['message']}" for v in res.get("violations", [])), as_json)


def commit_cmd(root, nid, frontmatter, body, as_json) -> int:
    res = commit(root, nid, _read(frontmatter), _read(body))
    if res["status"] == "REJECTED":
        return _rejected(res, as_json)
    _emit(res, as_json, render_commit)
    return 0


def render_update(payload: dict) -> None:
    print(f"  {payload['node']} -> {payload['node_status']}")
    if compressed := payload.get("compressed"):
        render_reward(compressed)


def update_cmd(root, nid, status, append, results, links, fields, as_json) -> int:
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






OWN_INTUITION = "source-own-intuition"

# The starter graph, as the three files it becomes. Data, not string literals: a rule set
# is easier to read and to edit as YAML than as an escaped Python triple-quote.
TEMPLATE = Path(__file__).parent / "template"


def _template(file: str, name: str = "") -> str:
    """A starter file with `{name}` filled in. `str.replace`, not `str.format`: the rule
    set is full of `{rel: ..., to: ...}` flow mappings, which format() reads as fields."""
    return (TEMPLATE / file).read_text(encoding="utf-8").replace("{name}", name)


def _slug(text: str) -> str:
    """A readable id from a sentence. Kebab-case because the id becomes a filename."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    out = []
    for w in words:
        if w in _STOP and out:            # keep a leading "the" rather than emit nothing
            continue
        out.append(w)
        if len("-".join(out)) > 48:
            break
    return "-".join(out) or "untitled"


def idea(root, text, source) -> int:
    """Drop your own idea into the graph in one line, for the agent to pick up.

    An idea has to cite what prompted it, so this wires the edges: the graph's single
    question and, unless `--from` names a source or finding, `source-own-intuition`, made
    once on first use. The node lands `open`, which puts it at the top of `frontier`."""
    nodes = load(root)
    if source is not None and source not in nodes:
        raise GraphError(f"no node '{source}' to derive this from")
    questions = [n.id for n in nodes.values() if n.type == "question"]
    origins = [q for q in questions[:1]] if len(questions) == 1 else []
    if source is None:
        if OWN_INTUITION not in nodes:
            made = commit(root, OWN_INTUITION, "type: source\nstatus: alive\norigin: own intuition",
                          "# Own intuition\n\nIdeas that started in a person's head, not in "
                          "something they read. Cited so an idea never comes from nowhere.\n")
            if made["status"] != "COMMITTED":
                return _rejected(made)
        origins.append(OWN_INTUITION)
    else:
        origins.append(source)

    cfg = load_config(root)
    sections = []
    for r in cfg.get("rules", []):
        if applies("open", "idea", r):
            sections += _csv(r.get("require_sections"))

    nid = "idea-" + _slug(text)
    fm = "type: idea\nstatus: open"
    if origins:
        fm += "\nlinks:\n" + "".join(f"  - {{rel: prov:wasDerivedFrom, to: {o}}}\n" for o in origins)
    body = f"# {text.strip()}\n\n"
    body += "".join(f"## {sec}\nTODO\n\n" for sec in dict.fromkeys(sections))

    res = commit(root, nid, fm, body)
    if res["status"] != "COMMITTED":
        return _rejected(res)
    print(f"  + nodes/{nid}.md  (from {', '.join(origins)})" if origins else f"  + nodes/{nid}.md")
    print("  it is `open`, so `knoten frontier` will offer it to whoever looks next.")
    return 0


def new(root, ntype, nid, status) -> int:
    """Scaffold a node carrying every section and field THIS graph's rules demand.
    Nothing here is knoten's opinion. The values are TODO on purpose: `validate` then
    names the ones you still owe, so `new` + `validate` is a checklist."""
    nf = node_path(root, nid)
    if nf.exists():
        raise GraphError(f"'{nid}' already exists. Supersede or retract it — corrections "
                         f"are nodes, not edits.")

    cfg = load_config(root)
    # `commit` enforces this; without it the same node is accepted by one entry point
    # and rejected by the other.
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
    # The allowed values go in as a comment: a closed vocabulary the author has to look up
    # is one they will guess at.
    fm += [f"{k}: TODO   # one of: {', '.join(map(str, v))}" for k, v in fields.items()]
    # Left EMPTY, not TODO: `require_field` takes any non-empty value, so a placeholder
    # would satisfy it and stop `new` + `validate` being a checklist.
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
    (root / "graph.yaml").write_text(_template("graph.yaml", name),
                                     encoding="utf-8")
    # knoten's own write lock. Nobody should have to see it in `git status`.
    (root / ".gitignore").write_text(f"{LOCK}\n", encoding="utf-8")
    (root / "nodes" / f"question-{name}.md").write_text(
        _template("question.md", name), encoding="utf-8")
    (root / "nodes" / "gate-example.md").write_text(_template("gate.md"), encoding="utf-8")
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
    s.add_argument("--all", action="store_true",
                   help="include superseded nodes (hidden by default)")
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

    s = sub.add_parser("hook", help="install the git gate that refuses a broken graph")
    s.add_argument("--server", nargs="?", const=".", metavar="REPO",
                   help="install the pre-receive gate in the repo everyone pushes to "
                        "(run this ON the server, inside the bare repo) instead of the "
                        "pre-commit gate in this clone")
    s.add_argument("--force", action="store_true",
                   help="overwrite a hook knoten did not write")

    s = sub.add_parser("serve", help="host remote graphs (run this on the server)")
    s.add_argument("--data", required=True, metavar="DIR", help="where graphs and tokens live")
    s.add_argument("--bind", default="127.0.0.1:8899", metavar="HOST:PORT")

    # git runs this; nobody types it. `!knoten credential` is set in every clone's config.
    s = sub.add_parser("credential", help=argparse.SUPPRESS)
    s.add_argument("action", nargs="?")

    # git runs this from the pre-receive hook; nobody types it.
    sub.add_parser("gate", help=argparse.SUPPRESS)

    s = sub.add_parser("key", help="your signing key (made on first use)")
    s.add_argument("name", nargs="?", help="who you sign as (default: git user.name)")

    s = sub.add_parser("remote", help="connect this graph to a knoten server")
    rs = s.add_subparsers(dest="remote_cmd", required=True)
    c = rs.add_parser("create", help="create this graph on a server and push it")
    c.add_argument("name")
    c.add_argument("--on", required=True, metavar="URL", help="the server, e.g. https://graphs.example")
    c.add_argument("--as", dest="admin", metavar="NAME", help="your contributor name (default: git user.name)")
    c.add_argument("--owner-secret",
                   help="the server's owner secret, as a last resort: argv is visible to "
                        "every other process on the machine. Prefer KNOTEN_OWNER_SECRET in "
                        "the environment, or let knoten prompt for it.")
    a = rs.add_parser("add", help="point this clone at an existing remote graph")
    a.add_argument("url", help="the graph's URL, e.g. https://graphs.example/trading")
    a.add_argument("--as", dest="me", metavar="NAME",
                   help="sign as this contributor (default: the stored credential's name)")

    sub.add_parser("push", help="push this graph's commits to its remote, through the gate")
    sub.add_parser("pull", help="bring down what collaborators pushed; your own commits go on top")

    s = sub.add_parser("invite", help="admin: let someone in (prints a one-time code)")
    s.add_argument("name", help="their contributor name, kebab-case")
    s.add_argument("--role", default="write", choices=ROLES)
    s.add_argument("--expires", type=int, default=7, metavar="DAYS")

    s = sub.add_parser("invites", help="admin: list the invites nobody has redeemed yet")
    s.add_argument("--json", action="store_true", help="emit the raw payload")

    s = sub.add_parser("join", help="redeem an invite and clone the graph")
    s.add_argument("url", help="the graph's URL, e.g. https://graphs.example/trading")
    s.add_argument("--invite", required=True, metavar="CODE")
    s.add_argument("--dest", metavar="DIR", help="where to clone (default: the graph's name)")

    s = sub.add_parser("revoke", help="admin: remove a contributor's access")
    s.add_argument("name")

    s = sub.add_parser("show", help="the node, its edges and its attachments")
    s.add_argument("node")
    s.add_argument("--json", action="store_true", help="emit the raw payload")

    s = sub.add_parser("idea", help="drop your own idea in for the agent to pick up")
    s.add_argument("text", help="the idea, in a sentence")
    s.add_argument("--from", dest="source", metavar="NODE",
                   help="the source or finding that prompted it (default: your own intuition)")

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
        if args.cmd is None:
            # No subcommand parsed no subparser, so `args` has no --json to read.
            args = _parser().parse_args(["validate"])
        if args.cmd == "init":
            return init(args.name)

        # Both bypass find_root(): `init` has no graph yet, and a bare repo never has one.
        if args.cmd == "hook" and args.server is not None:
            return server_hook(args.server, args.force)

        if args.cmd == "serve":
            return serve_cmd(args.data, args.bind)

        if args.cmd == "credential":
            if args.action == "get":
                sys.stdout.write(remote.credential_helper(sys.stdin.read()))
            return 0           # store/erase: git manages nothing here; knoten does

        if args.cmd == "gate":
            return gate.main()

        if args.cmd == "key":
            name = args.name or remote.default_signing_name()
            priv = ensure_key(name)
            print(f"  {public_line(priv)}")
            print(f"    signs as {name}; private half at {priv}. Never share that file.")
            return 0

        if args.cmd == "join":
            clone, name, role, signed = remote.join(args.url, args.invite, args.dest)
            print(f"  ✓ joined as {name} ({role}), cloned to {clone}/")
            if signed:
                print(f"    a signing key was made for {name}; this clone signs its own commits")
            elif role == "read":
                print("    read access: this clone can pull. Readers are not listed in "
                      f"{C.FILE} and hold no signing key.")
            print(f"    cd {clone} && knoten frontier")
            return 0

        root = find_root()
        if args.cmd in READ:
            return read_cmd(args.cmd, args, root)
        return {
            "new":    lambda: new(root, args.type, args.id, args.status),
            "idea":   lambda: idea(root, args.text, args.source),
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
            "remote": lambda: remote_cmd(root, args),
            "push":   lambda: remote.push(root),
            "pull":   lambda: remote.pull(root),
            "invite": lambda: invite_cmd(root, args.name, args.role, args.expires),
            "invites": lambda: invites_cmd(root, args.json),
            "revoke": lambda: revoke_cmd(root, args.name),
        }[args.cmd]()
    except (GraphError, OSError) as e:
        # OSError: a typo'd --frontmatter/--body/--append path is ordinary user error.
        return _fail({"error": str(e)}, e, getattr(args, "json", False))


def cli() -> None:
    sys.exit(main())


if __name__ == "__main__":
    cli()

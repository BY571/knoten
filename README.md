<p align="center">
  <img src="assets/knoten.png" alt="knoten" width="360">
</p>

<p align="center">
  <b>A research graph that remembers what didn't work.</b>
</p>

Each idea is a markdown file in git, marked **alive**, **dead**, or **retracted**. If it
died, the node carries the reason, what would bring it back, and the code to reproduce it.

## How it works

A graph is a folder. A node is a markdown file: **frontmatter for machines, prose for
humans, code to reproduce it.**

````markdown
---
id: hyp-self-consistency
type: hypothesis
status: dead
cause: weak_baseline
links:
  - {rel: kn:killedByGate, to: gate-compute-matched-baseline}
repro:
  script: experiments/self_consistency.py
  model: Qwen3-8B-Instruct
  data: GSM8K test, 1319 questions
  cmd: python experiments/self_consistency.py --n 5 --temp 0.7
results:
  acc_greedy: 0.741
  acc_self_consistency: 0.792
  acc_compute_matched_baseline: 0.788
  tokens_per_question: 1420
  n_independent: 1319
---

# Self-consistency (sample 5, majority vote) beats greedy decoding

## Verdict: DEAD
Sampling 5 chains and taking the majority scored 79.2% vs 74.1% greedy. +5.1 points.
It looked like a free win.

## Why it died
It is not free. It costs **5x the tokens**, and given the same budget a longer-CoT
baseline reaches **78.8%**. The entire gain was compute, not method.

```python
# reproduce the kill:
python experiments/self_consistency.py --n 5 --compare compute_matched
```

## What would reopen this
A task where the majority-vote *aggregation* does real work, i.e. where the gain
survives a compute-matched baseline. Plausible for code execution or theorem proving.
GSM8K is not that task.
````

Three months later, when someone proposes self-consistency again:

```
$ knoten query "self-consistency"

  [✗ DEAD] hyp-self-consistency
      killed by : gate-compute-matched-baseline
      reopen if : A task where the majority-vote aggregation does real work, i.e.
                  where the gain survives a compute-matched baseline…
```

## Use it

```bash
pip install -e .
knoten init my-topic              # a new graph (it's a folder)
```

```bash
# what should I do next?
knoten frontier                   # open work, dead ends worth reopening, gates nothing has been through
knoten index [--tag t] [--since d]   # the whole graph, one line per node
knoten query <term>               # has this been tried, by keyword?
knoten show <node>                # edges, results, attachments
knoten gates                      # what must a claim survive here?

# recording what happened
knoten new hypothesis hyp-idea    # scaffold a node with whatever the rules demand
knoten commit <node> --frontmatter <f> --body <f>   # file a claim, gate-checked before it touches disk
knoten update <node> --status dead --append <f>     # move it through its lifecycle
knoten attach <node> <file>...    # the script that ran it, the plot that shows it

# keeping it honest
knoten validate                   # enforce this graph's own rules
knoten hook                       # make `git commit` refuse a broken graph
knoten viz --open                 # the whole graph as one HTML file
knoten path A B                   # how did we get from A to B?
```

Every read command takes `--json`. Prose is the default because it is cheaper to read —
the same 55-node graph is ~1,185 tokens as columnar prose against ~2,551 as JSON. Exit
code is the signal: `0` succeeded, `1` refused. **A refusal is the feature.**

## How a graph is shaped

knoten defines no research vocabulary. `hypothesis`, `finding` and the rest mean whatever
your `graph.yaml` says they mean. A shape that recurs:

```
question ─▶ source ─▶ idea ─▶ hypothesis ─▶ experiment ─▶ finding
                       ▲                                     │
                       └────── findings open new ideas ──────┘

gate    stands outside the loop: the bar every claim must survive
```

A graph begins with **one question**, and `knoten init` scaffolds it as a node rather than
prose, so a finding can be traced back to the question it serves. From there the usual move
is to investigate **sources** — papers, datasets, searches. Work that starts from your own
head is not an exception: write `source-own-intuition` and cite it the same way. One rule
every time, an idea names where it came from — which also answers how much of a graph rests
on hunches rather than on having read anything.

Nothing is one-to-one: one hypothesis carries several experiments and several findings.

**Every edge points from the new node to the one it depends on** — a claim at the gate it
faced, an experiment at the hypothesis it tests. Back-links are generated and never
authored: writing the generated name (`kn:testedBy` where you meant `kn:tests`) is refused.
Writing the *right* relation on the *wrong* node is **not detectable at all** — the
back-link lands, the graph reports itself healthy, and the claim now says the opposite of
what you meant. A correction is a new node that supersedes or retracts the old one, never
an edit.

Your graph declares its vocabulary, and `node_types` takes a mapping when you want to write
down what the words mean — the only place they *can* be defined, since the core refuses to:

```yaml
node_types:
  question:   what this graph exists to answer — a question, a statement or a task
  source:     where the work came from — a paper, dataset, search, or your own intuition
  hypothesis: a falsifiable claim derived from an idea
  gate:       a standing rule every claim must survive; a bar, not a stage
statuses:   [open, alive, dead, retracted, superseded, active]
tags:       [decoding, reasoning, prompting, evaluation]
```

`type: hypthesis` is a typo, not a new type; `status: ded` would silently drop the claim
out of every query. Both are violations. Declare no `tags:` and tagging stays free — the
core enforces only the vocabulary you declared, and a config or rule key it does not
recognise is a **hard error**, because config that enforces nothing is decoration.

## Rules are data

Each graph declares its own in `graph.yaml`. knoten knows nothing about your field; it
enforces whatever *you* said matters.

```yaml
rules:
  - id: underpowered
    when_type: hypothesis
    require_result_min: {n_independent: 30}
    message: A result on fewer than 30 independent questions is noise, not evidence.

  - id: deaths-must-name-a-cause
    when_status: dead
    require_field_one_of:
      cause: [no_signal, cost_hurdle, weak_baseline, underpowered, crowding_decay]
    message: A cause of death you cannot filter on is a story, not an index.
```

That second rule is what makes a dead end *reusable*: once the cause is a field rather than
a sentence, the question you ask six months later is a query —
`knoten index --where cause=weak_baseline`.

Two keys look past the node being checked. `require_edge_target` asks what an edge points
**at**, so the day a finding dies everything derived from it fails validation — killing one
result indicts what was built on it. `require_backlink` asks what points **at** a node,
the only way to police work that was *abandoned* rather than written badly, since a
hypothesis nobody tested declares nothing wrong:

```yaml
rules:
  - id: methods-rest-on-live-claims
    when_type: method
    require_edge_target: {rel: prov:wasDerivedFrom, type: finding, status: alive, min: 1}
    message: A method built on a dead finding is a method built on sand.
```

`knoten new` reads these rules and pre-fills exactly what they demand — the values are
`TODO` on purpose, so `new` + `validate` is a checklist rather than a guessing game.

## What it answers

**What next?** `frontier` is the one screen for it — open work, plus dead ends that stated
what would bring them back. A dead end with a standing offer is a cheaper experiment than a
new idea, because the design is already written down. knoten does not decide whether a
condition is *met*; that judgement is the research.

**Has this been tried?** `query` answers by keyword. But keyword search cannot answer
*"anything like it?"* — an idea worded differently from the node that already killed it
will not match, and a confident "no prior work" is the one failure that costs real work. So
`index` prints the whole graph, one line per node, and lets the reader judge. On a 500-node
graph a broad query returned ~83k tokens; the index is ~9k, and one tag narrows it to ~2.5k.

**What must it survive?** `gates` puts the specification in front of the work — a claim can
only be marked `alive` if it cites a gate it survived, and meeting that at commit time means
the compute is already spent. Each gate also reports what it killed and what it passed: one
that has done neither has never been applied, which is either a useless check or a check
nobody runs.

**And a claim someone later withdrew says so** — `query` surfaces the retraction from both
sides, so asking about a retracted claim tells you it was retracted, not just what it said.

## Looking at it

```bash
knoten viz --open
```

One self-contained HTML file, no server and no build step. **Columns** is the inventory —
what exists, in what role, with what verdict, along the loop your graph declares.
**Map** is the traversal — click a node and walk its neighbours, with a trail of where you
have been. The map is monochrome on purpose: shade and size say how connected a node is,
which is a property of the map, while what happened to a claim lives in the record panel
beside it.

Appending a claim of a type already on screen moves nothing. Two things do reorder the
view, both real changes in what the graph is: a node introducing a new type, and the hub
count stepping up as the graph grows.

## The gate is a git hook

```bash
knoten hook     # after `git init`
```

`git commit` now runs `knoten validate` and refuses a graph that breaks its own rules:

```
  ✗ hyp-self-consistency
      [live-claims-must-cite-their-gates] An unchallenged claim is not a finding, it is a hope.

  1 violation(s) — commit REJECTED
```

A rule that only fires when you remember to ask is the rule that let the last attempt rot.
(`git commit --no-verify` bypasses it — you should have a reason.)

## Two readers, one file

**Humans** skim the prose: what was tried, what killed it, what is still open. No database,
no UI, just markdown you can read on GitHub.

**Agents** traverse the frontmatter: typed edges, structured `results`, a `repro` block with
the exact script/model/data/command, and the paths of attached scripts and plots they can
re-run. The same file serves both. That is the whole design.

## For coding agents

[`SKILL.md`](SKILL.md) is how an agent learns knoten — point Claude Code, or anything with a
shell, at it. The loop is the CLI itself, and it accumulates knowledge about a topic across
sessions instead of starting cold.

It also tells the agent when it is about to file the same question twice: a loop running for
weeks *will* re-propose an idea it already settled under a new id, so `commit` reports
settled claims the new node resembles, and refuses one recording a shiny result that cites
no test it survived.

An experiment that takes a week does not finish in the session that started it, so a
hypothesis can be opened now and closed later. `update` appends and moves status; it cannot
rewrite a result already recorded — that is what retraction is for.

`ops.py` holds the one implementation behind every read — index, query, frontier, gates,
show, validate, path — as a function returning a dict, which the CLI renders as prose or
dumps with `--json`. There was briefly a second surface for shell-less clients; it cost
~2,340 tokens of schema in every session against ~304 for `knoten --help`, and it was a
second thing to keep correct. The shell is the interface.

## Why bother

**You stop redoing experiments you already ran and forgot.** Dead ends come back with their
cause of death and a command to re-run them.

**Work that outlives the session still gets closed**, so *what is still open?* stays a real
answer instead of filling with questions that were settled and never filed.

**You cannot fool yourself as easily:** a claim is only `alive` if it cites a test it
survived, so a good-looking result that was never checked cannot quietly become a finding.

**A broken node is a loud failure, not a quiet one.** Unreadable frontmatter, an unknown
relation (`kn:killdByGate` — one letter dropped), a rule key that does not exist: all
errors. A graph that silently drops what it cannot parse reports itself healthy while it
rots.

See `examples/llm-research/` for a worked graph and [SPEC.md](SPEC.md) for the design.

MIT. One runtime dependency: PyYAML. No framework, no database, no build step — a handful
of small modules you can read in one sitting.

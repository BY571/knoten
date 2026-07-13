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
links:
  - {rel: kn:killedByGate, to: method-compute-matched-baseline}
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
      killed by : method-compute-matched-baseline
      reopen if : A task where the majority-vote aggregation does real work, i.e.
                  where the gain survives a compute-matched baseline…
```

## Use it

```bash
pip install -e .                  # add ".[mcp]" for the agent server

knoten init my-topic              # a new graph (it's a folder)
knoten new hypothesis hyp-idea    # scaffold a node that already satisfies the rules
knoten query <term>               # has this been tried?
knoten show <node>                # edges, results, attachments
knoten attach <node> <file>...    # attach a script, plot or notebook
knoten detach <node> <file>
knoten validate                   # enforce this graph's own rules
knoten path A B                   # how did we get from A to B?
```

Each graph declares its own rules in `graph.yaml`. `knoten` knows nothing about your
field. It enforces whatever *you* said matters. The example graph requires every claim to
report `tokens_per_question` and to rest on at least 30 independent questions; a different
topic would require something else entirely.

```yaml
rules:
  - id: underpowered
    when_type: hypothesis
    require_result_min: {n_independent: 30}
    message: A result on fewer than 30 independent questions is noise, not evidence.
```

Your graph also declares its own vocabulary, and that is enforced too:

```yaml
node_types: [hypothesis, experiment, finding, method, source]
statuses:   [open, alive, dead, retracted, superseded, active]
```

`type: hypthesis` is a typo, not a new type. `status: ded` is worse than wrong — it would
silently drop the claim out of every query, which is exactly the sort of quiet rot this
tool exists to prevent. Both are now violations.

A rule key — or a config key — `knoten` doesn't recognise is a **hard error**, not a
shrug. Config that enforces nothing is decoration, and a rule that enforces nothing is
worse than no rule, because you think you're covered.

`knoten new` reads those rules and pre-fills exactly what they demand — nothing in the
scaffold is knoten's opinion, it's your graph's:

```
$ knoten new hypothesis hyp-my-idea --status dead

  + nodes/hyp-my-idea.md  (hypothesis, dead)
    pre-filled what THIS graph's rules require:
      ## Why it died, ## What would reopen this, tokens_per_question, n_independent
```

The failure this tool exists to prevent was caused by **friction**, so the write path is
where friction hurts most. Write prose, not boilerplate you had to be rejected to discover.

## The gate is a git hook

```bash
knoten hook     # after `git init`
```

`git commit` now runs `knoten validate` and **refuses a graph that breaks its own rules**:

```
$ git commit -m "self-consistency is a win"

  ✗ hyp-self-consistency
      [live-claims-must-cite-their-gates] An unchallenged claim is not a finding, it is a hope.

  1 violation(s) — commit REJECTED
```

A rule that only fires when you remember to ask is the rule that let the last attempt rot.
Put it somewhere you can't walk past. (`git commit --no-verify` bypasses it — you should
have a reason.)

## Attach the code and the plots

A node isn't just a claim. It carries what you need to re-run it.

```bash
knoten attach hyp-self-consistency experiments/self_consistency.py accuracy_vs_budget.png
```

The files are copied into `attachments/<node-id>/`, listed in the frontmatter, and
images are **embedded in the node body** so they render on GitHub:

```
attachments/hyp-self-consistency/
  self_consistency.py        the script that KILLED it
  accuracy_vs_budget.png     the plot that shows why
```

`knoten validate` then fails if a node lists an attachment that isn't there. A broken
repro is a broken node.

```bash
knoten show hyp-self-consistency     # edges, results, attachments
knoten detach hyp-self-consistency accuracy_vs_budget.png
```

## Two readers, one file

**Humans** skim the prose and get the story: what was tried, what killed it, what's
still open. No database, no UI, just markdown you can read in any editor or on GitHub.

**Agents** traverse the frontmatter: typed edges (`kn:killedByGate`, `kn:survivedGate`),
structured `results`, a `repro` block with the exact script/model/data/command, and the
paths of any attached scripts and plots, which they can read and re-run directly. An agent
answers *"has this been tried?"* and *"how do I reproduce it?"* without reading a word of
prose.

The same file serves both. That's the whole design.

## For coding agents (MCP)

Point Claude Code, or any MCP client, at a graph. It then **accumulates knowledge about a
topic across sessions** instead of starting cold every time:

```bash
pip install -e ".[mcp]"
```

```jsonc
{"mcpServers": {"knoten": {
  "command": "knoten-mcp",
  "env": {"KNOTEN_GRAPH": "/path/to/llm-research"}
}}}
```

```
knoten_query("has anyone tried self-consistency?")   ← BEFORE it starts work
knoten_commit(node)                                  ← AFTER it finishes, pass or fail
knoten_attach(node, [script, plot])                  ← and the code that proves it
```

The agent reads the graph before running an experiment and writes back when it's done,
**including when the experiment fails.** A dead hypothesis with a documented cause of death
is the most valuable node in the graph, and the one that would otherwise be lost.

It writes back the *evidence* too, not just the prose: `knoten_attach` puts the script it
ran and the plot it made into the node. A claim you can't re-run is a claim nobody trusts
in six months — so the agent that killed a hypothesis leaves behind the thing that killed
it, ready for the next agent to run.

`knoten_commit` **validates before writing and refuses on violation** — the candidate node
is parsed and checked in memory, so an invalid node never reaches your filesystem. An agent
cannot record a shiny result that cites no test it survived:

```json
{"status": "REJECTED",
 "violations": [{"rule": "live-claims-must-cite-their-gates",
                 "message": "An unchallenged claim is not a finding, it is a hope."}]}
```

## Why bother

**You stop redoing experiments you already ran and forgot.** Dead ends come back with
their cause of death and a command to re-run them.

**And you can't fool yourself as easily:** a claim can only be marked *alive* if it cites
a test it survived, so a good-looking result that was never checked can't quietly become
a finding.

**And a broken node is a loud failure, not a quiet one.** Unreadable frontmatter, an
unknown edge relation (`kn:killdByGate` — one letter dropped), a rule key that doesn't
exist: all are errors. A graph that silently drops what it can't parse reports itself
healthy while it rots.

**And a claim someone later withdrew says so.** `query` surfaces the retraction from both
sides, so an agent asking *"has this been tried?"* about a claim that was later retracted
is told it was retracted — not just what the claim said:

```
  [✓ ALIVE] hyp-few-shot-format
      survived     : method-compute-matched-baseline
      RETRACTED by : ret-oops
```

See `examples/llm-research/` for a worked graph and [SPEC.md](SPEC.md) for the design.

MIT. One dependency (PyYAML). The whole thing is a few files you can read in one sitting.

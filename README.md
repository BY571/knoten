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
pip install -e .

knoten init my-topic              # a new graph (it's a folder)
knoten query <term>               # has this been tried?
knoten show <node>                # edges, results, attachments
knoten attach <node> <file>...    # attach a script, plot or notebook
knoten detach <node> <file>
knoten validate                   # enforce this graph's own rules
knoten path A B                   # how did we get from A to B?
```

Each graph declares its own rules in `graph.yaml`. `knoten` knows nothing about your
field. It enforces whatever *you* said matters. The example graph requires every claim to
report `tokens_per_question`; a different topic would require something else entirely.

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

```jsonc
{"mcpServers": {"knoten": {
  "command": "knoten-mcp",
  "env": {"KNOTEN_GRAPH": "/path/to/llm-research"}
}}}
```

```
knoten_query("has anyone tried self-consistency?")   ← BEFORE it starts work
knoten_commit(node)                                  ← AFTER it finishes, pass or fail
```

The agent reads the graph before running an experiment and writes back when it's done,
**including when the experiment fails.** A dead hypothesis with a documented cause of death
is the most valuable node in the graph, and the one that would otherwise be lost.

`knoten_commit` **validates before writing and refuses on violation**. An agent cannot
record a shiny result that cites no test it survived:

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

See `examples/llm-research/` for a worked graph and [SPEC.md](SPEC.md) for the design.

MIT. No required dependencies. The whole thing is a few files you can read in one sitting.

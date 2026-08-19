<p align="center">
  <img src="assets/knoten.png" alt="knoten" width="360">
</p>

<p align="center">
  <b>A research graph that remembers what didn't work.</b>
</p>

Every idea you test is a markdown file in git, marked **alive**, **dead** or
**retracted**. A dead one carries the reason it died, the condition that would bring it
back, and the script that killed it. Nothing is deleted and nothing is overwritten: a
correction is a new node that supersedes the old one, so six months later the graph can
tell you not just what you believe but what you already ruled out and why.

It exists because research loops — human or agent — forget. They re-propose an idea that
was settled last month under a different name, and they file the wins while the failures
evaporate. knoten makes the failure the artifact: a claim cannot be marked alive unless it
cites a test it survived, and a dead end has to say what would reopen it. Your graph
declares its own rules in `graph.yaml`; the tool enforces them and knows nothing else
about your field.

## A node

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

## The loop

```bash
pip install -e .
knoten init my-topic          # a graph is a folder
```

```bash
knoten frontier               # what should I work on next?
knoten index --tag decoding   # anything LIKE this been tried?
knoten query self-consistency # ...or by keyword, if it has a name
knoten show hyp-self-consistency
knoten gates                  # what must a claim survive here?

knoten new hypothesis hyp-idea                   # scaffolded from this graph's rules
knoten commit hyp-idea --frontmatter fm --body b # gate-checked before it touches disk
knoten update hyp-idea --status dead --append post-mortem.md --field cause=weak_baseline
knoten attach hyp-idea run.py accuracy.png       # the code and the plot

knoten validate               # enforce this graph's rules
knoten hook                   # make `git commit` refuse a broken graph
knoten viz --open             # the whole graph as one HTML file
```

Every read command takes `--json`. Exit `0` succeeded, `1` refused — and a refusal is the
feature: read it, fix the node, run it again.

## Rules are data

```yaml
rules:
  - id: live-claims-must-cite-their-gates
    when_status: alive
    when_type: hypothesis, finding
    require_edge: kn:survivedGate
    message: An unchallenged claim is not a finding, it is a hope.

  - id: deaths-must-name-a-cause
    when_status: dead
    require_field_one_of:
      cause: [no_signal, cost_hurdle, weak_baseline, underpowered, crowding_decay]
    message: A cause of death you cannot filter on is a story, not an index.
```

The first is the safety mechanism: a good-looking result that was never checked cannot
quietly become a finding. The second is what makes a dead end *reusable* — once the cause
is a field rather than a sentence, the question you ask six months later is a query:

```bash
knoten index --where cause=weak_baseline    # we have a stronger baseline now. what reopens?
```

Your graph declares its vocabulary the same way, and typos in it are violations rather
than new types:

```yaml
node_types:
  question:   what this graph exists to answer — a question, a statement or a task
  source:     where the work came from — a paper, dataset, search, or your own intuition
  hypothesis: a falsifiable claim derived from an idea
  gate:       a standing rule every claim must survive; a bar, not a stage
statuses:   [open, alive, dead, retracted, superseded, active]
tags:       [decoding, reasoning, prompting, evaluation]
```

## For agents

[`SKILL.md`](SKILL.md) is how a coding agent learns knoten — point Claude Code, or
anything with a shell, at it. It teaches the loop above, which types of node a graph holds
and which way an edge points.

---

See [`examples/llm-research/`](examples/llm-research) for a worked graph and
[SPEC.md](SPEC.md) for the design and the evidence behind it.

MIT. One dependency: PyYAML. No framework, no database, no build step.

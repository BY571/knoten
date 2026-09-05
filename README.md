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

It exists because research loops forget, whether they are run by a human or an agent.
They re-propose an idea that was settled last month under a different name, and they file
the wins while the failures evaporate. knoten makes the failure the artifact: a claim
cannot be marked alive unless it cites a test it survived, and a dead end has to say what
would reopen it. One graph can be shared by a team, humans and agents alike, with those
rules enforced on the server rather than on trust. Your graph declares its own rules in `graph.yaml`; the tool enforces them
and knows nothing else about your field.

The point is to run it **before** the work, not after. `knoten frontier` says what is worth
doing next, `knoten index` says whether it has been tried in different words, and
`knoten gates` says what the result will have to survive. Used the other way round, as a
place to file results once they exist, it is a tidy record that changes no decision.

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
knoten remote create g --on https://graphs.example   # share it
knoten invite maria --role write                     # let someone in
knoten viz --open             # the whole graph as one HTML file
```

Every read command takes `--json`. Exit `0` succeeded, `1` refused, and a refusal is the
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
quietly become a finding. The second is what makes a dead end *reusable*. Once the cause
is a field rather than a sentence, the question you ask six months later is a query:

```bash
knoten index --where cause=weak_baseline    # we have a stronger baseline now. what reopens?
```

Your graph declares its vocabulary the same way, and typos in it are violations rather
than new types:

```yaml
node_types:
  question:   what this graph exists to answer, be it a question, statement or task
  source:     where the work came from, such as a paper, dataset or your own intuition
  hypothesis: a falsifiable claim derived from an idea
  gate:       a standing rule every claim must survive; a bar, not a stage
statuses:   [open, alive, dead, retracted, superseded, active]
tags:       [decoding, reasoning, prompting, evaluation]
```

## A shared graph

One graph, several people, one set of rules enforced for all of them. A remote is a
`knoten serve` process on any machine you can reach over HTTPS: a box you own behind a
reverse proxy or a tunnel, a small VPS, or a hosted knoten.

```bash
# you, once, in your graph
knoten remote create trading --on https://graphs.example
knoten invite maria --role write        # prints a one-time code

# maria, anywhere
knoten join https://graphs.example/trading --invite 7f3a9c...
knoten frontier                         # her clone; the loop is unchanged from here
knoten push                             # over HTTPS, through the gate
```

Every push runs `knoten validate` on the server before the ref moves, so a node that
breaks the graph's rules is refused for everyone, including whoever wrote the rules, and
including anyone who never installed `knoten hook`. That matters more here than it
looks: the parser refuses rather than skips, on purpose, so on one machine a malformed
node is your problem and on a shared one it would be everybody's.

`read` can clone and pull. `write` can push. `admin` can invite and revoke. Tokens say
who is connecting and nothing else; what a token can do is the role the admin gave it.

Reading needs nothing installed. Nodes are markdown, and `knoten viz` writes the graph as
one self-contained HTML file you can hand to someone who has never heard of knoten.

To run the server:

```bash
knoten serve --data ~/knoten-remotes       # prints the owner secret once; keep it
```

It binds localhost and speaks plain HTTP. Put TLS in front before anyone outside the
machine connects. For a graph that lives in a bare repo you administer yourself, without
a server, `knoten hook --server <repo.git>` installs the same gate as a `pre-receive`
hook.

## For agents

[`SKILL.md`](SKILL.md) is how a coding agent learns knoten. Point Claude Code, or
anything with a shell, at it. It teaches the loop above, which types of node a graph holds
and which way an edge points.

---

See [`examples/llm-research/`](examples/llm-research) for a worked graph and
[SPEC.md](SPEC.md) for the design and the evidence behind it.

MIT. One dependency: PyYAML. No framework, no database, no build step.

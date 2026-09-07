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
tell you not just what you believe but what you already ruled out and why. A graph that
only grows is a notebook; a general finding that supersedes several specific ones is how
it gets wiser, and the graph rewards that.

It exists because research loops forget, whether they are run by a human or an agent.
They re-propose an idea that was settled last month under a different name, and they file
the wins while the failures evaporate. knoten makes the failure the artifact: a claim
marked alive with no test cited is not refused, but `knoten frontier` lists it as
unchecked so it cannot quietly pass as settled, and a dead end has to say what
would reopen it. Your graph declares its own rules in `graph.yaml`; the tool enforces them
and knows nothing else about your field.

The point is to run it **before** the work, not after. `knoten frontier` says what is worth
doing next, `knoten index` says whether it has been tried in different words, and
`knoten gates` says what the result will have to survive. Used the other way round, as a
place to file results once they exist, it is a tidy record that changes no decision.

One graph can be shared by a team, humans and agents alike. The rules are enforced on the
server, for everyone, rather than on trust.

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
pip install git+https://github.com/BY571/knoten   # or `pip install -e .` from a clone
knoten init my-topic                                # a graph is a folder
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

knoten remote create my-topic --on https://graphs.example   # share it
knoten invite maria --role write                            # let someone in
knoten pull                   # what they added
knoten push                   # what you added, through the gate
```

Every read command takes `--json`. Exit `0` succeeded, `1` refused, and a refusal is the
feature: read it, fix the node, run it again.

## Rules are data

```yaml
rules:
  # - id: live-claims-must-cite-their-gates
  #   when_status: alive
  #   when_type: hypothesis, finding
  #   require_edge: kn:survivedGate
  #   message: An unchallenged claim is not a finding, it is a hope.

  - id: deaths-must-name-a-cause
    when_status: dead
    require_field_one_of:
      cause: [no_signal, cost_hurdle, weak_baseline, underpowered, crowding_decay]
    message: A cause of death you cannot filter on is a story, not an index.
```

The first is advisory by default: a scaffolded graph ships it commented out, so a
good-looking result that was never checked against a gate is not refused, only listed by
`knoten frontier` under `UNCHECKED`. Uncomment it to make citing `kn:survivedGate` or
`kn:killedByGate` mandatory for an alive hypothesis or finding. The second is what makes a
dead end *reusable*. Once the cause is a field rather than a sentence, the question you
ask six months later is a query:

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

## Compress

Sooner or later several findings under one question are saying the same thing in
different numbers. Write the statement that makes them unnecessary, and point it at each
of them:

````markdown
---
id: finding-sc-needs-scale
type: finding
status: alive
tags: [decoding]
created: 2026-09-06
links:
  - {rel: npx:supersedes, to: finding-sc-small-models}
  - {rel: npx:supersedes, to: finding-sc-large-models}
  - {rel: kn:survivedGate, to: gate-compute-matched-baseline}
---

# Self-consistency pays only above a size the budget can afford

Compute-matched, sampling more chains buys nothing below ~7B and about two points above it.

## Covers
- finding-sc-small-models: the within-noise result; what it drops is the per-size table
- finding-sc-large-models: the two-point gain; what it drops is the exact token count
````

The engine checks the claim before anything reaches disk. Every target has to be alive,
of a type the graph lets you supersede, and standing under the same question as the
general node; the general node has to carry `kn:survivedGate` to every gate any target
survived, so a general claim faces the union of the bars its specifics faced; and
`## Covers` has to name each target by id, because a compression that cannot say what it
drops is a summary. The targets then flip to `superseded` in the same operation, keeping
their numbers, and `knoten index` stops listing them.

`knoten frontier` opens with the shape of the graph (`0 rules over 2 specifics` here).
Three or more alive findings under one question that share a gate or a tag are a
`COMPRESSIBLE` cluster, printed above the open work: the graph pointing at the rule it is
ready for. This example is one finding short of one. There is no ceiling and no quota;
the band is the whole of the nudge, and the commit says what a compression bought:

```
  + nodes/finding-sc-needs-scale.md  (8 nodes)

  ! This resembles 2 settled claim(s). If it is the same question, supersede or retract that node (npx:supersedes / npx:retracts) rather than leaving two answers in the graph.
    exp-self-consistency-budget  [✓ ALIVE]  Self-consistency against a compute-matched baseline, at three model sizes
    hyp-self-consistency  [✗ DEAD]  Self-consistency (sample 5, majority vote) beats greedy decoding
    compressed 2 findings into 1 under question-what-improves-reasoning
    survived the 1 gate they faced
    this graph now stands on 1 rule and 0 specifics
```

That is the whole of what the command prints. The resemblance warning never names what
the node just superseded, since resembling them is the point; it names the experiment and
the dead hypothesis still standing behind them, and leaves the judgement to you.

Retracting a general node stops it covering anything, and the specifics it retired stay
`superseded` until a person revives them: only a person can say whether an old result is
worth having back.

## A shared graph

One graph, several people, one set of rules enforced for all of them. A remote is a
`knoten serve` process on any machine you can reach over HTTPS. Seb runs the graph and
the server; Maria contributes from another city; an agent works in Maria's clone. Nothing
below needs a shared machine, a VPN, or an account anywhere.

```bash
# seb, once, on a small VPS with a reverse proxy terminating TLS in front of it
knoten serve --data ~/knoten-remotes           # localhost:8899; put caddy or nginx in front

# seb, once, in the graph on his laptop
knoten remote create trading --on https://graphs.example --as seb
knoten invite maria --role write               # a one-time code, good for 7 days; send it

# maria, once
knoten join https://graphs.example/trading --invite 7f3a9c...
cd trading                                     # a clone that signs as maria

# maria, or her agent, every session
knoten pull                                    # what arrived; her own commits go on top
knoten frontier                                # the loop is unchanged from here
knoten commit hyp-14 --frontmatter fm.yaml --body body.md
git add -A && git commit -m "hyp-14: batch size does not close the gap"
knoten push                                    # signed as maria, checked by the gate

# seb, whenever
knoten pull                                    # maria's nodes, into his
knoten invites                                 # who has a code and has not arrived
knoten revoke maria                            # a signed mark in the graph, then her token
```

`knoten commit` files a node; git commits it; `knoten push` sends the commits and refuses
while anything is still uncommitted, so it never reports a push that sent nothing. When
two people push the same afternoon, the second sees `the graph moved on since your last
pull`, pulls, and pushes: `knoten pull` replays your commits on top of theirs, and the
server refuses a merge commit if you make one by hand.

Every push runs `knoten validate` on the server before the ref moves, so a node that
breaks the graph's rules is refused for everyone, including whoever wrote them and anyone
who never installed `knoten hook`. `read` can clone and pull, `write` can push, `admin`
can invite and revoke. The server holds only hashed tokens and open invites; everything
that means anything lives in the graph, so losing the server loses availability and not
the answer to who said what. A shared graph is one line of history: the first push
creates the only branch it will ever have, and after that a new branch, a tag, a deletion
and a force push are each refused with the reason.

Who may write is written down in the graph, not on the server. `knoten remote create`
makes you a signing key and lists you as admin in `contributors.yaml`; from then on every
commit is signed and the server refuses one signed by nobody that file lists. Only the
admin's own token may lay down that first `contributors.yaml`, and that commit may touch
nothing else. An invite is signed on the admin's machine, so a stolen admin token mints
nothing. Revoking is a signed commit marking the entry, never a line deleted, so a clone
a year later still says who could write and who let them in. Readers are not listed: the
file is the list of people who may write.

`knoten key <name>` prints the public line the graph lists you under; the private half
lives at `~/.config/knoten/keys/<name>` (`KNOTEN_KEYS` moves that directory). One name,
one key: copy that file to a second machine to sign there, lose it and an admin adds you
back under a new name. `knoten serve --data <dir>` prints the owner secret once, on the
run that creates it; it creates graphs and nothing else, binds localhost and speaks plain
HTTP, so put TLS in front. For a bare repo you administer yourself,
`knoten hook --server <repo.git>` installs the same gate.

Reading needs nothing installed. Nodes are markdown, and `knoten viz` writes the graph as
one self-contained HTML file you can hand to someone who has never heard of knoten.
Superseded nodes fold under the rule that covers them: click `covers N` on a rule's card,
press `o` with the rule selected, or switch to `all` to see everything at once. The strip
at the top gives you the same `N rules over M specifics` that `knoten frontier` opens
with.

## For agents

[`SKILL.md`](SKILL.md) is how a coding agent learns knoten: point Claude Code, or
anything with a shell, at it. It teaches the loop above, which types of node a graph
holds and which way an edge points.

---

See [`examples/llm-research/`](examples/llm-research) for a worked graph and
[SPEC.md](SPEC.md) for the design and the evidence behind it.

MIT. One dependency: PyYAML. No framework, no database, no build step, and no server until
you share a graph.

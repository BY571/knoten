---
name: knoten
description: Use BEFORE designing or running anything in a knoten research graph, to
  decide what to work on and what the result must survive; and again the moment work
  concludes, including when it fails. Not a place to file results afterwards.
---

# knoten

A research graph that remembers what did NOT work. Run `knoten` in the graph directory.

## knoten is not a filing cabinet

The common way to misuse this is to do the research first and then write nodes describing
what you did. That produces a tidy record and changes nothing, because every decision the
graph was supposed to inform has already been made.

**The graph directs the work.** Before you design an experiment, choose what to look at
next, or write any code, run steps 1-4 below. They take seconds and they answer three
things your own context cannot: what is already dead, what is already open, and what a
result has to survive here.

Skipping them has a price you pay in compute: an experiment designed blind can fail a
check you never saw, and you run it again having already spent the budget once. Read
`knoten gates` first, and file `kn:survivedGate` or `kn:killedByGate` on the claim once
you have checked it against one.

If you realise you have already done the work without doing this, run 1-4 now, before
writing anything. The question may already be settled, and the gates still apply even
though citing none is not refused: `knoten frontier` lists an alive claim with no gate as
unchecked, and a graph may turn that into a refusal by enabling the rule.

## How the graph is shaped

`graph.yaml` declares the node kinds and, if the author wrote them, what each word means.
**Read it first**: knoten defines none of these words, so `hypothesis` means whatever
that graph says it means. A common shape:

    question ─▶ source ─▶ idea ─▶ hypothesis ─▶ experiment ─▶ finding
                           ▲                                     │
                           └────── findings open new ideas ──────┘

    gate    stands outside the loop: the bar every claim must survive

A round goes once around the loop. Read sources and file each one; take ideas from them;
sharpen an idea into a hypothesis that says what would kill it; build the experiment;
file the finding, alive or dead. Findings are where the next ideas come from, and where
compression starts.

A graph starts from ONE question, statement or task. `knoten init` scaffolds it, and
everything else descends from it. Investigate sources first (papers, posts, datasets,
searches); if the work starts from your own head instead, record that as a source too, so
an idea always names where it came from.

Those names are the order `knoten viz` lays columns out in; a type it does not know lands
after the ones it does. Links are a list, so one hypothesis can carry several experiments
and several findings.

**Every stage names the one before it.** One idea can produce several hypotheses and one
hypothesis several experiments, but the requirement runs the other way and is not optional:
every hypothesis cites an idea, every experiment cites a hypothesis it tests, every idea
cites the question, a source or the finding that prompted it. A node with nothing behind it
cannot be traced back to why anyone did the work, and a graph made with `knoten init`
refuses it: an idea with no source, a hypothesis with no idea, an experiment that tests
nothing, a finding that came from no experiment.

**One node is one stage.** The commonest way to get this wrong is to write the claim, the
run and the number into a single hypothesis: `results:` and a `## The result` section on a
node whose type says it is a claim. Split them: the hypothesis states what you believe
and how it could be wrong, the experiment states the setup and how to rerun it, the
finding states what came out. A graph with four hypotheses and one experiment is a graph
where three claims have no recorded test, whatever their bodies say.

## Which way an edge points

An edge always points from the NEW node to the one it depends on. Getting this backwards
makes the node invisible from the other side, because back-links are generated from the
forward edge and never authored.

    kn:survivedGate / kn:killedByGate   claim ──▶ the gate it faced
    kn:tests                            experiment ──▶ the hypothesis it tests
    prov:wasDerivedFrom                 claim ──▶ what it came from

`knoten validate` lists every relation it knows when you name one it does not, so ask it
rather than guessing; `kn:explains`, `kn:generalises` and `kn:followsFrom` name the KIND
of a derivation when that matters. Writing the generated name (`kn:testedBy` where you
meant `kn:tests`) is refused. Writing the right relation on the wrong node is NOT
detectable: the back-link lands and the graph reports itself healthy, with the claim
reversed. A correction is a NEW node, never an edit.

## Before you work

If the graph has a remote, `knoten pull` FIRST. Every read below answers from the files
on disk, so a stale clone reports work a collaborator settled days ago as still open:
the exact failure this graph exists to prevent, arriving through the back door.

1. `knoten frontier` says what is worth doing next: open work, dead ends whose stated
   reopen condition may now hold, and gates nothing has been through.
2. `knoten index`: the whole graph, one line per node. Read it and judge relatedness
   yourself; this is the only way to find work already done in DIFFERENT WORDS.
   `knoten query <term>` is keyword search: faster when the idea has a distinctive name,
   and blind to paraphrase. An empty result means "no keyword match", NOT "never tried".
3. `knoten show <id>` gives the full node: edges, results, and the path of the script that
   produced them, for anything that looks close.
4. `knoten gates`: what a result must survive here. Read this BEFORE designing the
   experiment; file `kn:survivedGate` or `kn:killedByGate` once you have checked one. An
   alive claim that cites no gate is listed by `knoten frontier` as unchecked, not
   refused, unless this graph has enabled the rule that refuses it.

`knoten metric` reads out the numbers this graph declared it is trying to move, oldest
first, each against the best result before it and naming what it built on. Read the best
point before you choose what to try: it already says which run beat which, and by how
much, so a direction that has been walked twice is visible without opening a node. A
graph that declares no `metrics:` prints one line saying so and costs you nothing.
## When the work concludes

5. `knoten commit <id> --frontmatter <file> --body <file>`: file the claim, INCLUDING
   when it failed. A dead hypothesis with a stated cause is the most valuable node in the
   graph and the one that would otherwise be lost. Use `knoten update <id> --status dead
   --append <file> --field cause=<value>` instead if you opened the node earlier.
6. `knoten attach <id> <files...>`: the script that ran it and the plot that shows it. A
   claim nobody can re-run is a claim nobody trusts in six months. With a remote, git
   commit and `knoten push` now, not at the end of the session.

## In a shared graph

A graph with a remote is one line of history that several people and their agents push
to. Three things change, and nothing else does:

- `knoten pull` before step 1, every session. Your own unpushed commits are replayed on
  top of what arrived; there is never a merge commit, because the server refuses one.
- `knoten commit` writes a node to disk and git has not seen it. Filing is finished when
  you run `git add -A && git commit -m "<id>: what was found"`; the clone signs the commit
  for you, and `knoten push` refuses while anything is uncommitted.
- `knoten push` after each filed node. Three refusals to know: `moved on since your last
  pull` means somebody was faster, so pull and push again; a rule violation is step 5's
  refusal applied on the server for everyone, so fix the node and push again; `not
  signed` means this clone was never set up by `knoten join`, `knoten remote create` or
  `knoten remote add`, so stop and say so rather than working around it.

Never edit a node somebody else filed, not even to fix it: supersede or retract it with a
new node. Two people editing one file is the only way a pull ends in a conflict here.

## Compress before you accumulate

A graph that only grows is a notebook. Every few findings, and always when
`knoten frontier` lists a `COMPRESSIBLE` cluster, stop and ask what single statement
would make several of them unnecessary. Write that statement as a new node that
`npx:supersedes` each of them, cite every gate any of them survived, and give it a
`## Covers` section that names each superseded node by its id, one per line, with what
that specific result contributed and what the general one drops. The specifics stay,
superseded, with their numbers; `knoten index` stops listing them. A general node is held
to the union of the bars its specifics faced, so a compression is not a summary, it is a
stronger claim.

Nothing refuses a finding for being one too many: there is no quota to satisfy and no
number to game. The `COMPRESSIBLE` band is the whole of the nudge, and the reward is that
the frontier gets shorter. A refused compression costs nothing but the attempt; a lazy
one cannot land, because the bar is checked.

Retracting a general node stops it covering anything. The specifics it retired stay
`superseded` until you revive them yourself (`knoten update <id> --status alive`): only a
person can say whether they are worth having back.

## Stage prompts

`src/knoten/prompts/` holds one prompt per stage: how to write a `source`, an `idea`, a
`hypothesis`, an `experiment`, a `finding`, a `gate`, and the root `question`. Read them
at the start of a turn, then follow the one the next action asks for, and do not write a
node from a prompt whose type it is not. Each prompt names the stage before it, lists the
scaffold sections to fill, and points at the kill or reopen field that stage carries. They
are the per-stage "write it like this"; the loop itself is above.

## Ideas a human dropped in

`knoten idea "<one sentence>"` files an idea as `status: open`, so anything a person
wants looked at shows up at the top of `knoten frontier` alongside your own open work. It
cites the graph's question and `source-own-intuition` for them (or `--from <node>`, a
source or a finding), so the idea already satisfies the lineage rules. Treat those the
same way: read the sentence, derive hypotheses from it, and if you decide against it,
close it with a reason rather than leaving it open forever.

## When you run out of ideas

`knoten frontier` tells you the well is dry: nothing `open` you can act on, no reopen
condition that now holds, no untested gate. Do not invent a hypothesis from your own
context. Compress first (a general node often opens ideas the specifics hid), then go
and read, in this order, filing each thing you actually read as a `source` node with its
`origin`:

- papers (arXiv, Semantic Scholar, the venue of the last good one you cited)
- the docs and changelogs of what you depend on
- blog posts and write-ups from people who ship this
- books and surveys for the settled parts
- forums and issue trackers for what breaks in practice
- a general web search last

Then derive `idea` nodes from those sources, and hypotheses from the ideas. On the way
back through, check two things a source round changes: dead nodes whose reopen
condition the new reading now satisfies (a cheaper experiment than a new idea), and gates
nothing has been through. Work from your own head is allowed if you cite
`source-own-intuition`; an idea from nowhere is not.

## Writing details

`--frontmatter`, `--body` and `--append` take a file path or `-` for stdin. `--result
key=value` records a number, `--link rel=to` adds an edge, `--field key=value` sets any
top-level field including one already recorded. Record every metric `graph.yaml` declares
in the `results:` of the node that produced it, under exactly the declared name: a run
that does not report the number is a run that is not on the chart, and nothing else
records it. `--result` refuses to change a number the
node already carries: correct a published result by superseding or retracting the node,
never by editing it. A node that fails the graph's rules never reaches disk.

An experiment that takes a week does not finish in the session that started it. Open the
node now with `status: open`, come back, and close it. `knoten index --status open` is
what shows you the ones you left hanging.

## Reading the output

Default output is compact prose; prefer it. `--json` exists for nested data and for
scripts; it costs about 2.2x the tokens for the same information (measured: 46 vs 21
tokens per node).

Exit code is the signal: `0` succeeded, `1` rejected or violated a rule. A refusal is the
feature: read it, fix the node, run it again.

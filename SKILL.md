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

Skipping them has a price you pay in compute. A claim cannot be filed `alive` without
citing a gate it survived, so an experiment designed without reading `knoten gates`
produces a result you cannot file. You run it again, against the gate, having already
spent the budget once.

If you realise you have already done the work without doing this: run 1-4 now, before
writing anything. The question may already be settled, and the gates still apply.

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
file the finding, alive or dead. The engine refuses a step that skips the one before it:
an idea with no source, a hypothesis with no idea, an experiment that tests nothing, a
finding that came from no experiment. Findings are where the next ideas come from, and
where compression starts.

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
cannot be traced back to why anyone did the work.

**One node is one stage.** The commonest way to get this wrong is to write the claim, the
run and the number into a single hypothesis: `results:` and a `## The result` section on a
node whose type says it is a claim. Split them — the hypothesis states what you believe
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
rather than guessing. `kn:explains`, `kn:generalises` and `kn:followsFrom` name the KIND
of a derivation when that matters.

Writing the generated name (`kn:testedBy` where you meant `kn:tests`) is refused. Writing
the right relation on the wrong node is NOT detectable: the back-link lands and the graph
reports itself healthy, with the claim reversed. A correction is a NEW node that supersedes
or retracts the old one, never an edit to it.

## Before you work

If the graph has a remote, `knoten pull` FIRST. Every read below answers from the files
on disk, so a stale clone reports work a collaborator settled days ago as still open.
That is the exact failure this graph exists to prevent, arriving through the back door.
See "In a shared graph" below for the rest of what a remote changes.

1. `knoten frontier` says what is worth doing next: open work, dead ends whose stated
   reopen condition may now hold, and gates nothing has been through.
2. `knoten index`: the whole graph, one line per node. Read it and judge relatedness
   yourself; this is the only way to find work already done in DIFFERENT WORDS.
   `knoten query <term>` is keyword search: faster when the idea has a distinctive name,
   and blind to paraphrase. An empty result means "no keyword match", NOT "never tried".
3. `knoten show <id>` gives the full node: edges, results, and the path of the script that
   produced them, for anything that looks close.
4. `knoten gates`: what a result must survive here. Read this BEFORE designing the
   experiment; a claim cannot be filed as alive without citing a gate it survived.
## When the work concludes

5. `knoten commit <id> --frontmatter <file> --body <file>`: file the claim, INCLUDING
   when it failed. A dead hypothesis with a stated cause is the most valuable node in the
   graph and the one that would otherwise be lost. Use `knoten update <id> --status dead
   --append <file> --field cause=<value>` instead if you opened the node earlier.
6. `knoten attach <id> <files...>`: the script that ran it and the plot that shows it.
   A claim nobody can re-run is a claim nobody trusts in six months. If the graph has a
   remote, git commit the node and `knoten push` now, not at the end of the session; the
   server runs the same rules and refuses what this clone would have.

## In a shared graph

A graph with a remote is one line of history that several people and their agents push
to. Three things change, and nothing else does:

- `knoten pull` before step 1, every session. Your own unpushed commits are replayed on
  top of what arrived; there is never a merge commit, because the server refuses one.
- `knoten commit` writes a node to disk and git has not seen it. Filing is finished when
  you run `git add -A && git commit -m "<id>: what was found"`; the clone signs the
  commit for you. `knoten push` refuses while anything is uncommitted, so it cannot
  claim to have sent what it did not.
- `knoten push` after each filed node. Three refusals to know:
  `moved on since your last pull` means somebody was faster: `knoten pull`, then push
  again. A rule violation is the refusal step 5 would have given you, applied on the
  server for everyone: fix the node, push again. `not signed` means this clone was not
  set up by `knoten join`, `knoten remote create` or `knoten remote add`; stop and say
  so rather than working around it.

Never edit a node somebody else filed, not even to fix it: supersede or retract it with a
new node, as above. Two people editing one file is the only way a pull ends in a
conflict here, and `knoten pull` then names the file and what to run next.

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

The graph will refuse a new finding once a question holds more live findings than its
budget. That refusal is not an error; it is the graph telling you it has learned enough
specifics to deserve a rule.

The graph rewards compression in the only currency it has: budget under the question
comes back, the frontier gets shorter, and the commit tells you exactly what you freed. A
refused compression costs nothing but the attempt; a lazy one cannot land, because the
bar is checked, so every general node that exists is one the graph considers stronger
than what it replaced.

## Stage prompts

`src/knoten/prompts/` holds one prompt per stage: how to write a `source`, an `idea`, a
`hypothesis`, an `experiment`, a `finding`, a `gate`, and the root `question`. Read them
at the start of a turn, then follow the one the next action asks for, and do not write a
node from a prompt whose type it is not. Each prompt names the stage before it, lists the
scaffold sections to fill, and points at the kill or reopen field that stage carries. They
are the per-stage "write it like this"; the loop itself is above.

## Ideas a human dropped in

`knoten idea "<one sentence>"` files an idea as `status: open`, so anything a person
wants looked at shows up at the top of `knoten frontier` alongside your own open work.
Treat those the same way: read the sentence, derive hypotheses from it, and if you decide
against it, close it with a reason rather than leaving it open forever.

## When you run out of ideas

`knoten frontier` is also how you learn the well is dry: nothing `open` you can act on, no
reopen condition that now holds, no untested gate left. That is not a signal to invent a
hypothesis out of your own context and file it. It is a signal to go and read.

Do a compression pass first: a general node often opens ideas the specifics hid.

Then start a new source round, and put what you find in the graph before you reason from
it:

- search the web, arXiv, the venue's or vendor's own docs, blog posts, forum and Reddit
  threads, and the issue trackers of anything you depend on
- file each thing you actually read as a `source` node, with `origin` set to the url, doi
  or path so somebody can go back to it
- derive `idea` nodes from those sources, then hypotheses from the ideas

Two things to check on the way back through, because a source round is exactly the event
that changes them:

- the reopen conditions on dead nodes (`knoten frontier`). A dead end whose stated
  condition the new reading satisfies is a cheaper experiment than a new idea, because the
  design is already written down.
- the untested gates. A gate nothing has been through is a check nobody is running.

If the work really did start in your own head rather than in something you read, that is
allowed: write `source-own-intuition` and cite it like any paper. What is not allowed is
an idea that came from nowhere, because six months from now nobody can tell whether it
came from evidence or from a mood.

## Writing details

`--frontmatter`, `--body` and `--append` take a file path or `-` for stdin. `--result
key=value` records a number, `--link rel=to` adds an edge, `--field key=value` sets any
top-level field including one already recorded. `--result` refuses to change a number the
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

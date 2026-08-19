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
**Read it first** — knoten defines none of these words, so `hypothesis` means whatever
that graph says it means. A common shape:

    question ─▶ source ─▶ idea ─▶ hypothesis ─▶ experiment ─▶ finding
                           ▲                                     │
                           └────── findings open new ideas ──────┘

    gate    stands outside the loop: the bar every claim must survive

A graph starts from ONE question, statement or task — `knoten init` scaffolds it, and
everything else descends from it. Investigate sources first (papers, posts, datasets,
searches); if the work starts from your own head instead, record that as a source too, so
an idea always names where it came from.

Those names are the order `knoten viz` lays columns out in; a type it does not know lands
after the ones it does. Links are a list, so one hypothesis can carry several experiments
and several findings.

## Which way an edge points

An edge always points from the NEW node to the one it depends on. Getting this backwards
makes the node invisible from the other side, because back-links are generated from the
forward edge and never authored.

    kn:survivedGate / kn:killedByGate   claim ──▶ the gate it faced
    kn:tests                            experiment ──▶ the hypothesis it tests
    prov:wasDerivedFrom                 claim ──▶ what it came from

`knoten validate` lists every relation it knows when you name one it does not, so ask it
rather than guessing — `kn:explains`, `kn:generalises` and `kn:followsFrom` name the KIND
of a derivation when that matters.

Writing the generated name (`kn:testedBy` where you meant `kn:tests`) is refused. Writing
the right relation on the wrong node is NOT detectable — the back-link lands and the graph
reports itself healthy, with the claim reversed. A correction is a NEW node that supersedes
or retracts the old one, never an edit to it.

## Before you work

1. `knoten frontier` — what is worth doing next: open work, dead ends whose stated
   reopen condition may now hold, and gates nothing has been through.
2. `knoten index` — the whole graph, one line per node. Read it and judge relatedness
   yourself; this is the only way to find work already done in DIFFERENT WORDS.
   `knoten query <term>` is keyword search: faster when the idea has a distinctive name,
   and blind to paraphrase. An empty result means "no keyword match", NOT "never tried".
3. `knoten show <id>` — the full node: edges, results, and the path of the script that
   produced them, for anything that looks close.
4. `knoten gates` — what a result must survive here. Read this BEFORE designing the
   experiment; a claim cannot be filed as alive without citing a gate it survived.
## When the work concludes

5. `knoten commit <id> --frontmatter <file> --body <file>` — file the claim, INCLUDING
   when it failed. A dead hypothesis with a stated cause is the most valuable node in the
   graph and the one that would otherwise be lost. Use `knoten update <id> --status dead
   --append <file> --field cause=<value>` instead if you opened the node earlier.
6. `knoten attach <id> <files...>` — the script that ran it and the plot that shows it.
   A claim nobody can re-run is a claim nobody trusts in six months.

## When you run out of ideas

`knoten frontier` is also how you learn the well is dry: nothing `open` you can act on, no
reopen condition that now holds, no untested gate left. That is not a signal to invent a
hypothesis out of your own context and file it. It is a signal to go and read.

Start a new source round, and put what you find in the graph before you reason from it:

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
node now with `status: open`, come back, and close it — `knoten index --status open` is
what shows you the ones you left hanging.

## Reading the output

Default output is compact prose — prefer it. `--json` exists for nested data and for
scripts; it costs about 2.2x the tokens for the same information (measured: 46 vs 21
tokens per node).

Exit code is the signal: `0` succeeded, `1` rejected or violated a rule. A refusal is the
feature — read it, fix the node, run it again.

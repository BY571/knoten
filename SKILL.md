---
name: knoten
description: Use when working in a knoten research graph — before starting an
  investigation (has this been tried?), when choosing what to work on, and when an
  experiment concludes, including when it fails.
---

# knoten

A research graph that remembers what did NOT work. Run `knoten` in the graph directory.

## How the graph is shaped

`graph.yaml` declares the node kinds and, if the author wrote them, what each word means.
**Read it first** — knoten defines none of these words, so `hypothesis` means whatever
that graph says it means. A common shape:

    source ──▶ idea ──▶ hypothesis ──▶ experiment ──▶ finding
                 ▲                                       │
                 └────── findings open new ideas ────────┘

    gate    stands outside the loop: the bar every claim must survive

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

## The loop

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
5. `knoten commit <id> --frontmatter <file> --body <file>` — file the claim when the
   work concludes, INCLUDING when it fails. `knoten update <id> --status dead --append
   <file> --field cause=<value>` if you opened the node earlier and are now closing it.
   `--field` sets any top-level field, including one already recorded — the graph's own
   rules decide what is acceptable, and a node that fails them never reaches disk.
   `--result` still refuses to change a number already recorded: correct a published
   result by retracting or superseding the node, not by editing it.
6. `knoten attach <id> <files...>` — the script that ran it and the plot that shows it.
   A claim nobody can re-run is a claim nobody trusts in six months.

## Reading the output

Default output is compact prose — prefer it. `--json` exists for nested data and for
scripts; it costs about 2.2x the tokens for the same information (measured: 46 vs 21
tokens per node).

Exit code is the signal: `0` succeeded, `1` rejected or violated a rule. A refusal is the
feature — read it, fix the node, run it again.

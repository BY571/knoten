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
    method  the answer derived from findings that are still alive

Nothing is one-to-one: one idea yields several hypotheses, one hypothesis several
experiments, one experiment several findings, and a finding may rest on several
experiments. Write the node you have; the edges carry the structure.

## Which way an edge points

An edge always points from the NEW node to the one it depends on. Getting this backwards
makes the node invisible from the other side, because back-links are generated from the
forward edge and never authored.

    kn:survivedGate / kn:killedByGate   claim ──▶ the gate it faced
    kn:tests                            experiment ──▶ the hypothesis it tests
    kn:explains                         claim ──▶ the observation it accounts for
    kn:generalises                      claim ──▶ the instances behind it
    kn:followsFrom                      claim ──▶ the premise it argues from
    prov:wasDerivedFrom / prov:used     claim ──▶ what it came from / used
    mp:supports / mp:challenges         claim ──▶ the claim it bears on
    npx:supersedes / npx:retracts       the newer node ──▶ the one it replaces

A correction is a NEW node that supersedes or retracts the old one, never an edit to it.

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

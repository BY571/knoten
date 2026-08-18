---
name: knoten
description: Use when working in a knoten research graph — before starting an
  investigation (has this been tried?), when choosing what to work on, and when an
  experiment concludes, including when it fails.
---

# knoten

A research graph that remembers what did NOT work. Run `knoten` in the graph directory.

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
   <file>` if you opened the node earlier and are now closing it — but `update` only
   appends and moves status, it cannot set a NEW top-level field, so if this graph
   requires one on death (`require_field_one_of`) set it at `commit` time instead.
6. `knoten attach <id> <files...>` — the script that ran it and the plot that shows it.
   A claim nobody can re-run is a claim nobody trusts in six months.

## Reading the output

Default output is compact prose — prefer it. `--json` exists for nested data and for
scripts; it costs about 2.2x the tokens for the same information (measured: 46 vs 21
tokens per node).

Exit code is the signal: `0` succeeded, `1` rejected or violated a rule. A refusal is the
feature — read it, fix the node, run it again.

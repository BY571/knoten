# knoten stage: hypothesis

A hypothesis is one falsifiable claim, derived from an `idea`, with the number that would
kill it written down first. It carries no run and no result: those are the experiment's
and the finding's. Status `open` until an experiment tests it.

Frontmatter it needs:

```yaml
type: hypothesis
status: open
links:
  - {rel: prov:wasDerivedFrom, to: <the idea>}
```

No `results:` and no `repro:` here (the graph refuses them on a hypothesis).

Body, in this order:

# <the claim, one sentence>

## The claim
The yes/no statement, with the measure it is about.

## What this does not test
The boundary: which data, model, asset, period or setting is out. One claim per node;
if you cannot say what is out, split it.

## Why it might be true

## Why it might be false

## Kill criterion
The threshold at which this is dead, as a number on the measure. "No edge" is not a
criterion; "below 0.5 points at equal compute" is.

Marking it `alive` needs an experiment that tests it (`kn:tests` from the experiment)
and, by convention, a gate it survived (`kn:survivedGate`); a `dead` one needs
`## Why it died` and `## What would reopen this`.

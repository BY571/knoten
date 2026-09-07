# knoten stage: finding

A finding says what an `experiment`'s numbers mean for the claim it tested, and whether
the claim is `alive` or `dead`. File it when it failed too: a dead end with a stated cause
is the node that saves the next person a week. New ideas come from findings.

Frontmatter it needs:

```yaml
type: finding
status: alive            # or dead
links:
  - {rel: prov:wasDerivedFrom, to: <the experiment>}
  - {rel: kn:survivedGate, to: <the gate it passed>}      # or kn:killedByGate
cause: <one of the graph's causes>     # dead findings only, if the graph declares them
```

No `repro:` here (it lives on the experiment; a copy would drift).

Body, in this order:

# <what it shows, one sentence>

## What it shows
The verdict in words, with the number that decides it.

## Evidence
Which experiment, which figure, how it compares to the baseline and to the kill criterion.

## Why it died
Only when dead: the cause, as a relationship between measured things.

## What would reopen this
Only when dead: the condition under which this verdict would reverse. Required.

A general finding that supersedes several findings (`npx:supersedes` to each) names each
of them in a `## Covers` section instead of citing an experiment.

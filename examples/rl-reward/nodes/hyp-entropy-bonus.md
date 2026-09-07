---
id: hyp-entropy-bonus
type: hypothesis
status: dead
cause: no_signal
tags: [exploration]
created: 2026-06-19
links:
  - {rel: prov:wasDerivedFrom, to: idea-more-exploration}
  - {rel: kn:killedByGate, to: gate-three-seeds}
---

# Raising the entropy coefficient from 0 to 0.01 lifts return above 12

## The claim
Raising the entropy coefficient from 0 to 0.01 lifts return above 12.

## What this does not test
Other exploration methods; changes to the reward.

## Why it might be true
More diverse gaits early may include hopping.

## Why it might be false
The plateau is variance, not exploration; entropy adds noise to a solved problem.

## Kill criterion
Mean return below 10.3 (the reward-scaled run).

## Why it died
Return fell to 9.8 over three seeds: the extra entropy disturbed a gait that had already converged.

## What would reopen this
A task where runs plateau at clearly different gaits across seeds, which would say exploration and not variance is the bottleneck.

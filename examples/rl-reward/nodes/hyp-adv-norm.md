---
id: hyp-adv-norm
type: hypothesis
status: alive
tags: [variance]
created: 2026-06-04
links:
  - {rel: prov:wasDerivedFrom, to: idea-normalise-advantages}
  - {rel: kn:survivedGate, to: gate-three-seeds}
---

# Advantage normalisation lifts mean return from 2 to above 4 at 1M steps

## The claim
Advantage normalisation lifts mean return from 2 to above 4 at 1M steps.

## What this does not test
Other tasks, other algorithms, and any change to the value function.

## Why it might be true
The clip range becomes meaningful once advantages are unit scale.

## Why it might be false
The baseline's plateau may be a reward-scale problem instead.

## Kill criterion
Mean return below 4 over three seeds.

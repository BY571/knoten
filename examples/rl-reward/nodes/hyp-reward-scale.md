---
id: hyp-reward-scale
type: hypothesis
status: alive
tags: [variance]
created: 2026-06-11
links:
  - {rel: prov:wasDerivedFrom, to: idea-scale-rewards}
  - {rel: kn:survivedGate, to: gate-three-seeds}
---

# Reward scaling on top of advantage normalisation lifts return above 8

## The claim
Reward scaling on top of advantage normalisation lifts return above 8.

## What this does not test
Value clipping, learning-rate schedules, network size.

## Why it might be true
The value loss no longer grows with episode length.

## Why it might be false
Scaling may slow early learning when rewards are small.

## Kill criterion
Mean return below 8 over three seeds.

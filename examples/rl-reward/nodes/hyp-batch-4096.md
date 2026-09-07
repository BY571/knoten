---
id: hyp-batch-4096
type: hypothesis
status: alive
tags: [batch]
created: 2026-06-25
links:
  - {rel: prov:wasDerivedFrom, to: idea-bigger-batches}
  - {rel: kn:survivedGate, to: gate-three-seeds}
---

# Batch 4096 with learning rate 6e-4 lifts return above 15

## The claim
Batch 4096 with learning rate 6e-4 lifts return above 15.

## What this does not test
Learning-rate schedules beyond a constant; other optimisers.

## Why it might be true
With variance under control the bigger step is affordable.

## Why it might be false
Fewer updates per million steps may not compensate.

## Kill criterion
Mean return below 15 over three seeds.

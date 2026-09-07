---
id: idea-bigger-batches
type: idea
status: alive
tags: [batch]
created: 2026-06-24
links:
  - {rel: prov:wasDerivedFrom, to: question-hopper-return}
  - {rel: prov:wasDerivedFrom, to: finding-reward-scaling}
---

# With the variance fixed, larger batches should let the learning rate go up

## Where it came from
The reward-scaling finding: the value loss stopped dominating, but gradient
noise across minibatches was still high.

## Why it might hold
Two variance fixes in a row helped; batch size is the third variance knob.

## Why it might not hold
Fewer updates per million steps.

## What would make it false
Return below 10.3 at 1M steps.

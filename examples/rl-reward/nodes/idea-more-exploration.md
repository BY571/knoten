---
id: idea-more-exploration
type: idea
status: alive
tags: [exploration]
created: 2026-06-18
links:
  - {rel: prov:wasDerivedFrom, to: question-hopper-return}
  - {rel: prov:wasDerivedFrom, to: source-own-intuition}
---

# A larger entropy bonus keeps the policy from collapsing early

## Where it came from
A hunch after watching runs plateau at the same gait.

## Why it might hold
Policies converge to a shuffling gait; more entropy might find hopping.

## Why it might not hold
Hopper is not an exploration problem; the plateau may be a variance problem.

## What would make it false
Return below the reward-scaled run at 1M steps.

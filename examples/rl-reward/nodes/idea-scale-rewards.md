---
id: idea-scale-rewards
type: idea
status: alive
tags: [variance]
created: 2026-06-10
links:
  - {rel: prov:wasDerivedFrom, to: question-hopper-return}
  - {rel: prov:wasDerivedFrom, to: source-impl-details-blog}
---

# Scale rewards by a running standard deviation

## Where it came from
Detail 9 in the implementation-details post.

## Why it might hold
Hopper rewards grow with episode length, so value targets drift upward during
training and the value loss dominates.

## Why it might not hold
Scaling changes the effective discount of early rewards.

## What would make it false
Return no better than the advantage-normalised run.

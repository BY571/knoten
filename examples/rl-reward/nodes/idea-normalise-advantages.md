---
id: idea-normalise-advantages
type: idea
status: alive
tags: [variance]
created: 2026-06-03
links:
  - {rel: prov:wasDerivedFrom, to: question-hopper-return}
  - {rel: prov:wasDerivedFrom, to: source-ppo-paper}
---

# Normalise advantages per batch before the policy update

## Where it came from
The PPO paper lists it as standard; our baseline skips it.

## Why it might hold
Unnormalised advantages on hopper span two orders of magnitude, so the clip is
either never or always active.

## Why it might not hold
Normalisation can wash out the sign of rare large advantages.

## What would make it false
No change in mean return over three seeds.

---
id: gate-three-seeds
type: gate
status: active
tags: [seeds]
created: 2026-06-01
---

# Gate: three seeds, std reported

## The rule
A return counts only as a mean over at least three seeds at the same step budget,
with the standard deviation next to it. One seed is an anecdote.

## Why it exists
The first "improvement" on this task was 2.1 to 6.0 on one seed; over five seeds
it was 2.1 to 2.4. PPO variance across seeds on hopper is larger than most of the
effects we chase.

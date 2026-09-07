---
id: source-impl-details-blog
type: source
status: alive
origin: https://iclr-blog-track.github.io/2022/03/25/ppo-implementation-details/
created: 2026-06-02
---

# The 37 implementation details of PPO

## What it says
A catalogue of details that matter more than the algorithm: reward scaling by a
running std, value clipping, orthogonal init, and larger batches for continuous
control.

## Why it is here
Two of the details (reward scaling, batch size) are absent from our baseline.

## What it is not
Measured on Atari and MuJoCo defaults, not on our hopper variant.

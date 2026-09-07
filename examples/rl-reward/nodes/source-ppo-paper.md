---
id: source-ppo-paper
type: source
status: alive
origin: https://arxiv.org/abs/1707.06347
created: 2026-06-02
---

# Schulman et al. 2017, Proximal Policy Optimization Algorithms

## What it says
Clipped surrogate objective, several epochs per batch, and advantage normalisation
as an implementation detail that stabilises updates.

## Why it is here
Our baseline PPO omits advantage normalisation; the paper treats it as standard.

## What it is not
It says nothing about reward scaling or batch size on locomotion tasks.

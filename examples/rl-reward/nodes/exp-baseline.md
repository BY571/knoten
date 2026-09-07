---
id: exp-baseline
type: experiment
status: alive
tags: [ppo]
created: 2026-06-03
links:
  - {rel: kn:tests, to: hyp-adv-norm}
results:
  return: 2.1
  return_std: 0.4
  seeds: 3
  steps: 1000000
repro:
  script: experiments/ppo_hopper.py
  cmd: python experiments/ppo_hopper.py --seeds 0 1 2
---

# Baseline PPO with no advantage normalisation

## Setup
PPO, hopper task, 1M steps, three seeds (0, 1, 2), evaluation over 20 episodes at the
end. Baseline PPO with no advantage normalisation.

## How to reproduce
`python experiments/ppo_hopper.py --seeds 0 1 2`

## Result
Mean return 2.1, std 0.4, over three seeds.

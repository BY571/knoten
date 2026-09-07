---
id: exp-adv-norm
type: experiment
status: alive
tags: [ppo, variance]
created: 2026-06-06
links:
  - {rel: kn:tests, to: hyp-adv-norm}
results:
  return: 5.1
  return_std: 0.6
  seeds: 3
  steps: 1000000
repro:
  script: experiments/ppo_hopper.py
  cmd: python experiments/ppo_hopper.py --norm-adv --seeds 0 1 2
---

# Advantage normalisation on, everything else baseline

## Setup
PPO, hopper task, 1M steps, three seeds (0, 1, 2), evaluation over 20 episodes at the
end. Advantage normalisation on, everything else baseline.

## How to reproduce
`python experiments/ppo_hopper.py --norm-adv --seeds 0 1 2`

## Result
Mean return 5.1, std 0.6, over three seeds.

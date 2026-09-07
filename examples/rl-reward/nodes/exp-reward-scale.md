---
id: exp-reward-scale
type: experiment
status: alive
tags: [ppo, variance]
created: 2026-06-14
links:
  - {rel: kn:tests, to: hyp-reward-scale}
results:
  return: 10.3
  return_std: 0.9
  seeds: 3
  steps: 1000000
repro:
  script: experiments/ppo_hopper.py
  cmd: python experiments/ppo_hopper.py --norm-adv --scale-rewards --seeds 0 1 2
---

# Reward scaling by running std, advantages normalised

## Setup
PPO, hopper task, 1M steps, three seeds (0, 1, 2), evaluation over 20 episodes at the
end. Reward scaling by running std, advantages normalised.

## How to reproduce
`python experiments/ppo_hopper.py --norm-adv --scale-rewards --seeds 0 1 2`

## Result
Mean return 10.3, std 0.9, over three seeds.

---
id: exp-entropy-bonus
type: experiment
status: alive
tags: [ppo, exploration]
created: 2026-06-21
links:
  - {rel: kn:tests, to: hyp-entropy-bonus}
results:
  return: 9.8
  return_std: 1.4
  seeds: 3
  steps: 1000000
repro:
  script: experiments/ppo_hopper.py
  cmd: python experiments/ppo_hopper.py --norm-adv --scale-rewards --ent-coef 0.01 --seeds 0 1 2
---

# Entropy coefficient 0.01 on top of the reward-scaled run

## Setup
PPO, hopper task, 1M steps, three seeds (0, 1, 2), evaluation over 20 episodes at the
end. Entropy coefficient 0.01 on top of the reward-scaled run.

## How to reproduce
`python experiments/ppo_hopper.py --norm-adv --scale-rewards --ent-coef 0.01 --seeds 0 1 2`

## Result
Mean return 9.8, std 1.4, over three seeds.

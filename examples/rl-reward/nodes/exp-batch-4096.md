---
id: exp-batch-4096
type: experiment
status: alive
tags: [ppo, batch]
created: 2026-06-28
links:
  - {rel: kn:tests, to: hyp-batch-4096}
results:
  return: 15.4
  return_std: 0.7
  seeds: 3
  steps: 1000000
repro:
  script: experiments/ppo_hopper.py
  cmd: python experiments/ppo_hopper.py --norm-adv --scale-rewards --batch 4096 --lr 6e-4 --seeds 0 1 2
---

# Batch 4096, learning rate 6e-4, both variance fixes on

## Setup
PPO, hopper task, 1M steps, three seeds (0, 1, 2), evaluation over 20 episodes at the
end. Batch 4096, learning rate 6e-4, both variance fixes on.

## How to reproduce
`python experiments/ppo_hopper.py --norm-adv --scale-rewards --batch 4096 --lr 6e-4 --seeds 0 1 2`

## Result
Mean return 15.4, std 0.7, over three seeds.

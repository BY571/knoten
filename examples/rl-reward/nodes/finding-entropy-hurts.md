---
id: finding-entropy-hurts
type: finding
status: dead
tags: [exploration]
created: 2026-06-22
cause: no_signal
links:
  - {rel: prov:wasDerivedFrom, to: exp-entropy-bonus}
  - {rel: kn:killedByGate, to: gate-three-seeds}
---

# An entropy bonus of 0.01 lowers return to 9.8 and widens the spread

    ## What it shows
    An entropy bonus of 0.01 lowers return to 9.8 and widens the spread.

    ## Evidence
    exp-entropy-bonus: 9.8 (std 1.4) against 10.3 (std 0.9); the larger spread is the entropy itself.

## Why it died
Hopper at this budget is a variance problem, not an exploration problem; the bonus perturbs a gait that had already converged.

## What would reopen this
A task where seeds plateau at visibly different gaits.

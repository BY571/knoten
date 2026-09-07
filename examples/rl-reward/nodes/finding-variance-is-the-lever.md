---
id: finding-variance-is-the-lever
type: finding
status: alive
tags: [variance, batch]
created: 2026-07-02
links:
  - {rel: npx:supersedes, to: finding-adv-norm}
  - {rel: npx:supersedes, to: finding-reward-scaling}
  - {rel: kn:survivedGate, to: gate-three-seeds}
---

# On hopper, every gain came from reducing update variance, none from exploration

Advantage normalisation, reward scaling and a larger batch each cut the variance of
the policy update and each raised return; the one exploration change lowered it.

## Covers
- finding-adv-norm: the first variance fix, 2.1 to 5.1; what it drops is the per-seed table
- finding-reward-scaling: the second, 5.1 to 10.3; what it drops is the value-loss curve

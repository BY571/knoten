---
id: finding-sc-large-models
type: finding
status: alive
created: 2026-08-21
tags: [decoding]
links:
  - {rel: prov:wasDerivedFrom, to: exp-self-consistency-budget}
  - {rel: kn:survivedGate, to: gate-compute-matched-baseline}
results:
  tokens_per_question: 1260
  n_independent: 40
---

# At 8B and above, about two points of the self-consistency gain is real

77.1% with a majority vote over 5 chains, 74.9% with the same tokens spent on one
longer chain, at 1260 tokens per question in both arms. 32B moves the same way, 88.4%
against 86.2%.

Survives [[gate-compute-matched-baseline]]: the gain is what is left after the baseline
was handed the same budget, which is what [[hyp-self-consistency]] never had.

Two points is small enough that it is worth knowing what it costs. It is not free
accuracy; it is accuracy you can now buy at a stated price.

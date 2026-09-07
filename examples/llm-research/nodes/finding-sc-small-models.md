---
id: finding-sc-small-models
type: finding
status: alive
created: 2026-08-21
tags: [decoding]
links:
  - {rel: prov:wasDerivedFrom, to: exp-self-consistency-budget}
  - {rel: kn:survivedGate, to: gate-compute-matched-baseline}
results:
  tokens_per_question: 1180
  n_independent: 40
---

# Below 7B, self-consistency buys nothing a matched budget does not

41.2% with a majority vote over 5 chains, 40.8% with the same tokens spent on one
longer chain. The 0.4 point difference sits inside a seed-to-seed spread of 1.5 points
on the same 40 questions, so it is noise.

Survives [[gate-compute-matched-baseline]]: both arms spent 1180 tokens per question.

The honest reading is that a small model does not produce enough disagreement between
chains for a majority to have anything to aggregate. Most chains that are wrong are
wrong the same way.

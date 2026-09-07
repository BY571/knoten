---
id: exp-self-consistency-budget
type: experiment
status: alive
created: 2026-08-19
tags: [decoding, evaluation]
links:
  - {rel: kn:tests, to: hyp-self-consistency}
  - {rel: prov:wasDerivedFrom, to: question-what-improves-reasoning}
repro:
  script: experiments/budget_sweep.py
  model: Qwen3-1.7B, Qwen3-8B, Qwen3-32B
  data: GSM8K test, 40 held-out questions per arm
  cmd: python experiments/budget_sweep.py --arms matched --n 40
---

# Self-consistency against a compute-matched baseline, at three model sizes

The numbers here are an example. This graph ships to show what a knoten graph looks
like, not as a result anybody should cite.

## Setup

Two arms, one token budget, three model sizes.

- **Arm A**: sample 5 chains at temp 0.7, take the majority answer.
- **Arm B**: spend the same tokens on one longer chain plus a reranking pass.

40 GSM8K questions per arm per model, drawn once and held out from the prompt tuning.
[[gate-compute-matched-baseline]] is the bar: both arms get the same budget, so the
comparison measures the aggregation and not the spend.

## How to reproduce

```bash
python experiments/budget_sweep.py --arms matched --n 40
```

The script prints one row per model with both arms and the token count it actually
spent, so a run that drifts off the budget is visible rather than silent.

## Result

| model      | arm A (majority of 5) | arm B (matched budget) | tok/question |
|------------|-----------------------|------------------------|--------------|
| Qwen3-1.7B | 0.412                 | 0.408                  | 1180         |
| Qwen3-8B   | 0.771                 | 0.749                  | 1260         |
| Qwen3-32B  | 0.884                 | 0.862                  | 1310         |

At 1.7B the two arms are inside the seed-to-seed spread of 1.5 points. At 8B and above
the gap is about two points and holds across seeds. The two readings are filed
separately as [[finding-sc-small-models]] and [[finding-sc-large-models]], because they
disagree about what the method is worth.

---
id: method-compute-matched-baseline
type: method
status: active
tags: [gate, evaluation]
---
# Gate: compute-matched baseline

## The rule

Any method that spends **more inference compute** must be compared against a baseline
given **the same budget** — not against greedy decoding at 1x.

```python
# WRONG: 5 samples vs 1 sample. You are measuring compute, not the method.
acc_new  = evaluate(self_consistency, n_samples=5)
acc_base = evaluate(greedy,           n_samples=1)

# RIGHT: equal token budget on both sides.
budget = 5 * tokens_per_greedy_answer
acc_new  = evaluate(self_consistency, budget=budget)
acc_base = evaluate(best_of_n_reranked, budget=budget)   # or a longer CoT, or a bigger model
```

## Why it exists

Most "technique X improves accuracy" results are really "X spends more tokens." The
gain is real, but it is not *free*, and it usually does not survive a baseline handed
the same budget.

Report `tokens_per_question` for both arms. If you can't, you don't have a result.

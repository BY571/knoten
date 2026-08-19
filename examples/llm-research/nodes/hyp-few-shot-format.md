---
id: hyp-few-shot-format
type: hypothesis
status: alive
created: 2026-03-14
tags: [prompting]
links:
  - {rel: kn:survivedGate, to: gate-compute-matched-baseline, note: "same token budget both arms"}
repro:
  script: experiments/fewshot_format.py
  model: Qwen3-8B-Instruct
  data: GSM8K test, 1319 questions
  cmd: python experiments/fewshot_format.py --format xml --shots 4
results:
  acc_plain: 0.741
  acc_xml_delimited: 0.771
  tokens_per_question: 290
  n_independent: 1319
---

# Delimiting few-shot examples with XML tags improves accuracy

## Verdict — ALIVE

**74.1% → 77.1%** (+3.0), at an *identical* token budget (290 tok/question both arms).
Survives [[gate-compute-matched-baseline]]: the gain is not bought with compute.

```python
# the whole change:
prompt = "".join(f"<example>\n{q}\n{a}\n</example>\n" for q, a in shots)
# vs the plain "Q: ... A: ..." concatenation
```

## Caveat

Tested on one model and one benchmark. Cheap to check on others; nobody has.

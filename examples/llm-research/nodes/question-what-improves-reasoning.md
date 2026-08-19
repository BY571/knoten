---
id: question-what-improves-reasoning
type: question
status: open
tags: [reasoning, evaluation]
---
# What actually improves LLM accuracy on reasoning tasks?

## Why it matters
Every published gain is reported against a baseline the authors chose. The question is
which of them survive a baseline chosen by someone with no stake in the answer.

## What would count as an answer
A method that beats a compute-matched baseline on a held-out reasoning benchmark, by a
margin larger than the seed-to-seed spread, and that still holds when the baseline is
given the same token budget.

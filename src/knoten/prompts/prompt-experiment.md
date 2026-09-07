# knoten stage: experiment

An experiment is the run that tests one `hypothesis`, written so anyone can rerun it and
get the same number. It records what was measured, not what it means: the meaning is the
finding's job. Status `open` while it runs, `alive` when it has run.

Frontmatter it needs:

```yaml
type: experiment
status: alive
links:
  - {rel: kn:tests, to: <the hypothesis>}
results:
  <measure>: <number>          # every declared metric goes here, as a number
repro:
  cmd: python experiments/<script>.py --seed 0     # optional, but this is what makes it rerunnable
  data: <dataset, split, dates>
```

Body, in this order:

# <the test, one sentence>

## Setup
Model, data, parameters, baseline, seeds. Everything a stranger needs to reproduce the
number, stated, not implied.

## How to reproduce
The exact command(s). If it needs a file, `knoten attach <id> <file>` it.

## Result
The numbers, and only the numbers: the measure for each arm, the sample size, the noise
if you have it. No verdict here.

Then file a `finding` that cites this experiment and says what the numbers mean.

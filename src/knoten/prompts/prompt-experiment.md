# knoten stage - experiment

You are building the test that carries a hypothesis to its verdict. The experiment states
the setup and how to rerun it, not the result - the result belongs to the finding that
follows. An experiment that takes time opens `open` now and is closed later;
`knoten index --status open` is the list of the ones left hanging.

Cite the `hypothesis` it tests via kn:tests. This is the node the finding will cite back,
so the link is the spine of the whole record: an experiment with no hypothesis is a test
nobody designed.

Scaffold:

# <the test, one sentence>

## The setup
How to rerun it, exactly. Data, dates, parameters, the command. A result nobody can run is
a result nobody trusts in six months.

## What it measures
The number that decides, in one line: the return measure, the risk measure, a Brier score.

## Which gate(s) it survives
The `gate` node(s) the result will clear. State them before the run, so the run cannot
invent a bar it already meets.

## Kill criterion
The verdict threshold, carried down from the hypothesis.

## The hypothesis it tests
{rel: kn:tests, to: <hypothesis id>}

Rules that matter here:

- The experiment is not the finding. Do not write "it worked" here; that is the next node's
  job, and putting it here hides what came out.

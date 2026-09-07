# knoten stage prompts

One prompt per stage of the loop. Read them at the start of a turn, then follow whichever
the next action asks for.

- `prompt-question.md`  the root the graph descends from
- `prompt-source.md`    what you read before you reason from it
- `prompt-idea.md`      a direction derived from a source
- `prompt-hypothesis.md` a falsifiable claim, with the answer that would kill it
- `prompt-experiment.md` the test that carries a hypothesis to its verdict
- `prompt-finding.md`   what came out, even if it failed
- `prompt-gate.md`      the bar a claim must survive, not a stage it passes through

How to use them:

- At the start of a turn read all of them once, alongside `graph.yaml`. The graph's own
  `node_types` is the only authority on what each word means here, and its `rules` are
  the bar your nodes must clear.
- When it is time to create a node, follow the one for that stage. Do not write a node
  from a prompt whose type it is not: a hypothesis is not a finding, and filing a claim as
  one changes nothing the graph can act on.
- Each stage names the stage before it. Follow that rule. The node the next stage cites is
  what makes this a graph rather than a pile of notes.

They are short on purpose. The loop is in `SKILL.md`; these are the per-stage "write it
like this" that the loop points you to when it is time.

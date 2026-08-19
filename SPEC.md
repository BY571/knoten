# knoten — design spec

**Falsification-first research graphs in git.**

**Status:** draft · **Date:** 2026-07-13 · **Validated against:** a real 23-node investigation

---

## 1. The problem, and why nobody has solved it

A prior-art search (108 agents, 25+ primary sources) established that **no existing
tool combines git-native markdown nodes + falsification-aware typed edges +
agent-native read/write**. The gap is *structural*: every candidate buys one axis by
foreclosing another.

| system | markdown + git | falsification edges | agent / MCP |
|---|---|---|---|
| Basic Memory | markdown ✓ · **git ✗** | **✗** free-form wikilinks | ✓ |
| Graphiti | **✗** (Neo4j) | partial (bi-temporal) | ✓ |
| Micropublications | **✗** (OWL) | ✓✓ | ✗ |
| Nanopublications | **✗** (RDF) | ✓✓ | ✗ |
| DISK | **✗** (OWL) | **✗** revision-typed only | ✗ |

**The load-bearing finding.** A 2019 survey (Wanyana & Moodley, CEUR Vol-2540)
compared the three existing hypothesis ontologies — LABORS, DISK, HELO — and *all
three* score **"No"** on *"hypothesis appraisal mechanism and unsuccessful
hypotheses."* Grepping DISK's released ontology for
`falsif|refut|retract|reject|invalid|supersed|negativ` returns **zero matches**.

> **The entire research-ontology field, across 20 years, does not model hypothesis
> death.**

This is not an oversight — it is an incentive. Publication rewards recording what
worked. **A private research graph has no such incentive, which is exactly why it can
hold what the literature cannot.** Negative results are the asset *because* nobody
stores theirs.

### The evidence that this matters

The investigation that motivated this produced **seven hypotheses; six died.**
The surviving value was almost entirely in the deaths:

- Four of seven died on **economics, not statistics** (a fee schedule; an effect too small to cover costs). No model would have saved them.
- Two "findings" were **unintentional cherry-picks** caught only by specific gates.
- The reusable asset turned out to be the **seven method nodes** — the tests
  themselves — not any strategy.

A system that stores only conclusions would have preserved ~15% of that.

---

## 2. Non-goals

- **We do not build a Git host.** Git already provides versioning, branching, blame,
  diffs, PRs (peer review!) and hosting. Reimplementing any of it is madness.
- **We do not build a UI first.** A static site generator over the graph is a
  phase-3 nicety, and it can emit its own JSON when it exists.
- **We do not invent a vocabulary.** Micropublications and nanopublications already
  standardised most of the predicates we need. We add only what is genuinely missing.
- **The core knows nothing about any domain.** Not trading, not biology. Domain rules
  live in the graph's own config, as data.

---

## 3. Core model

Two primitives, and only two.

**Node** = one markdown file. YAML frontmatter (machine) + prose body (human).
**Edge** = a typed link declared in that frontmatter.

The prose is not decoration. `## Why it died` and `## What would reopen this` are the
fields that make a graph worth revisiting, and they are exactly what the previous
attempt (`knowledge_graph.jsonl`, still empty) could not hold. **A schema that cannot
store an argument will be abandoned, because writing to it feels like paperwork
rather than thinking.**

### Node types (conventions, not hardcoded)

`hypothesis` · `experiment` · `finding` · `method` · `source` · `retraction`

### Status lifecycle

```
open ──► alive ──────► superseded
   └───► dead          (a better claim replaced it)
   └───► retracted     (WE WERE WRONG — the most valuable node type)
```

Every arrow above is walkable from the agent surface via `knoten_update`, which appends
and moves the status but cannot rewrite a claim. Immutability protects **what was
claimed**, never the status field — the status field *is* the lifecycle, and git already
holds the before and after (§7). Without this an agent could open a hypothesis and never
close it, leaving a settled question `open` on every future frontier.

`retracted` is first-class. Three claims in the source session were withdrawn. **A
graph that records only conclusions and never corrections lies to you in six months.**
The retracted node *stays*, with its post-mortem attached.

---

## 4. Edge vocabulary — reuse the standards

Adopt existing predicates. Coin only what the field genuinely lacks.

### Reused (do not reinvent)

| predicate | source | meaning |
|---|---|---|
| `mp:supports` | Micropublications (purl.org/mp) | evidence supports claim (transitive) |
| `mp:challenges` | Micropublications | **evidence contradicts claim** |
| `npx:retracts` | Nanopublications | this node withdraws that one |
| `npx:supersedes` | Nanopublications | this node replaces that one |
| `prov:wasDerivedFrom` | PROV-O | this question arose from that one |
| `prov:used` | PROV-O | used this dataset / method |

### Novel — this is the actual contribution

The field has no way to say *"this claim survived / was killed by a named
methodological gate."* DISK's `LineOfInquiry` is the closest blueprint and it
**cannot express failure** (zero falsification terms in its ontology).

| predicate | domain → range | meaning |
|---|---|---|
| `kn:survivedGate` | claim → method | **claim passed this gate.** A `status: alive` claim MUST have ≥1. |
| `kn:killedByGate` | claim → method | **the gate that killed it.** The predicate the entire field is missing. |
| `kn:blockedBy` | claim → finding | a *structural* blocker (a fee schedule, a venue, a data licence) — not a result, a wall. |

`kn:survivedGate` + the rule engine is the whole safety mechanism: **an unchallenged
claim cannot be marked alive.**

---

## 5. Cause of death — a closed vocabulary

Adapted from arXiv:2606.21024 (*Negative Knowledge as Failure-aware Shared Memory*,
Jun 2026), grounded in the seven real deaths of the source session:

| `death.cause` | meaning | real example |
|---|---|---|
| `no_signal` | the effect is not there | a signal that wasn't there |
| `cost_hurdle` | real effect, too small to trade | a real effect too small to pay its costs |
| `structural_blocker` | a wall you cannot climb | a venue the participant cannot access |
| `selection_bias` | it was a cherry-pick | an unintentional cherry-pick |
| `weak_baseline` | it only beat a strawman | a model that only beat a strawman |
| `underpowered` | too few independent bets | any t-stat on n<30 |
| `crowding_decay` | it was real; it got arbitraged | a real edge that got arbitraged away |

Every dead/retracted node should carry a `cause`, a `## Why it died` section, and a
**`## What would reopen this`** section. The last is non-negotiable: it converts a
dead end into a **standing offer**, and it is what stops the next agent re-running it.

**The core does not enforce this vocabulary, and must not** — a cause of death is
domain knowledge, and §2 says the core knows no domain. The list above is a *convention*
for research graphs. Your graph enforces the parts it cares about, as data:

```yaml
  - id: dead-claims-must-say-why
    when_status: dead, retracted
    require_sections: Why it died, What would reopen this
    message: The post-mortem IS the asset — a dead end must become a standing offer.

  - id: deaths-must-name-a-cause
    when_status: dead, retracted
    require_field_one_of:
      cause: [no_signal, cost_hurdle, structural_blocker, selection_bias,
              weak_baseline, underpowered, crowding_decay]
    message: A cause of death you cannot filter on is a story, not an index.
```

The second rule is what makes a death **queryable** rather than merely recorded:

```bash
knoten index --where cause=weak_baseline    # we have a stronger baseline now. what reopens?
```

That is a query when the cause is a field and a re-read of every post-mortem when it is
prose — and it is the moment a research graph pays for itself. `require_field_one_of`
constrains any frontmatter field to a declared set; the *values* live in `graph.yaml`, so
a biology graph declares different ones and the core still knows no domain.

---

## 6. Rules as configuration

The core enforces whatever the graph declares. **Domain knowledge lives in data,
never in code.**

LinkML was the original plan and was dropped: it validates *shape*, and every rule that
matters here is a *predicate over a node* ("does this claim cite a gate?"). A LinkML
schema plus a bespoke predicate layer is strictly more machinery than the predicate
layer alone. The rule engine is ~50 lines in `validate.py`.

```yaml
# graph.yaml — each rule is a SCAR. Write one only when you have a corpse.
rules:
  - id: live-claims-must-cite-their-gates
    when_status: alive
    when_type: hypothesis, finding
    require_edge: kn:survivedGate
    message: An unchallenged claim is not a finding, it is a hope.

  - id: dead-claims-must-say-why
    when_status: dead, retracted
    require_sections: Why it died, What would reopen this
    message: The post-mortem IS the asset.

  - id: underpowered
    when_type: hypothesis
    require_result_min: {n_independent: 30}
    message: An effect size without a sample size is not evidence.
```

| key | effect |
|---|---|
| `id` | **required.** Names the rule in the violation. |
| `message` | what the human reads when it fires. |
| `when_status` / `when_type` | only apply to these statuses / node types (comma-separated). |
| `require_edge` | node must declare this relation. |
| `require_sections` | body must contain these `## ` headings. |
| `require_result` | `results:` must carry this key. |
| `require_result_min` | `{key: floor}` — numeric floor on a result. |
| `require_field_one_of` | `{field: [allowed]}` — a frontmatter field constrained to a closed set. |
| `require_edge_target` | an edge of this relation must point at a node of this type/status; `min` counts distinct targets |

**An unknown rule key is a hard error.** A rule the engine cannot understand would
enforce nothing while reporting `✓ all rules pass` — a validator that silently accepts is
worse than no validator, because you believe you are covered. The same goes for an
unknown edge relation (`kn:killdByGate`, one letter dropped) and for frontmatter that
does not parse.

The graph also declares its own vocabulary, and it is enforced:

```yaml
node_types: [hypothesis, experiment, finding, method, source]
statuses:   [open, alive, dead, retracted, superseded, active]
tags:       [decoding, reasoning, prompting, evaluation, gate]
```

The core invents neither list — declare none and none is checked (§2: the core knows no
domain). But a graph that *does* declare one has said those are the only legal words, so
`type: hypthesis` is a typo and `status: ded` is a claim that would silently vanish from
every query.

Always-on structural checks, which no graph has to declare: `dangling-edge`,
`missing-attachment`, `unknown-relation`, `authored-backlink`, `missing-type`,
`unknown-type`, `unknown-status`, `unknown-tag`, `malformed-tags`.

A biology graph declares entirely different rules. The core never changes.

> Rule 1 alone would have rejected `a claim` the moment it was
> marked `alive` without citing `the gate that would have caught it` — **before** an RL env was built
> for it.

---

## 7. Layout

```
my-graph/
  graph.yaml               # identity + rules
  nodes/*.md               # one node per file — the source of truth
  attachments/<id>/*       # the script that killed it, the plot that shows why
```

`knoten new` and `knoten_commit` stamp `created:`; `knoten_update` stamps `updated:`.
Both are plain ISO date strings, which the YAML 1.2 loader keeps as strings rather than
coercing to `datetime.date`. Git knows when the *file* changed, which is not when the
*claim* did — a typo fix and a status flip are the same event to git — and reading it
would cost one subprocess per node to order a frontier.

Git provides: history, blame, diff-a-claim-as-it-changed, branches-as-research-
directions, **PRs-as-peer-review**, and hosting. We write none of it.

---

## 8. The agent surface — CLI first

This section used to be titled "The agent surface (MCP) — this is what makes it
compound," and meant it. That claim is now wrong, and this is the reversal, stated
plainly rather than slid past.

The previous attempt failed because the graph was a **byproduct of automation**: when
the orchestrator did not run, nothing was written. Meanwhile a plain chat session
produced 15 experiments whose knowledge would have evaporated without hand-written
memory files. The fix was still right — make the graph primary, and make writing to it
the path of least resistance — but MCP turned out to be the expensive way to deliver it
to an agent that already has a shell.

**The measurement.** knoten's MCP surface loads ~2,340 tokens into every session whether
the agent touches the graph or not (1,928 of tool schema + 412 of instructions).
`knoten --help` costs ~304 tokens, and only when the agent asks for it — most sessions
never pay even that. And format compounds the gap: the same 55 nodes cost ~2,551 tokens
as MCP's JSON (46/node) against ~1,185 as the CLI's columnar prose (21/node) — 2.2x. That
is why prose is the CLI's default and `--json` is opt-in, not the other way round.

**So: the CLI is now the primary agent surface.** `ops.py` holds the one implementation
of every read — index, query, frontier, gates, show, validate, path — as a plain
function returning a dict; the CLI renders it as prose or dumps it with `--json`, and MCP
serialises the same dict as a tool result. `commit` and `update` are likewise shared
functions, not surface-specific code paths. `SKILL.md`, at the repo root, is how an agent
now learns the loop: `frontier` → `index`/`query` → `show` → `gates` → `commit`/`update`
→ `attach`.

**MCP is retained, not deleted.** It is the right surface for a client that has no
shell — a chat UI wired to MCP servers rather than a coding agent with Bash. Those tools
still exist, still delegate to the same `ops`/`commit`/`update` functions, and still carry
the loop as the server's `instructions`:

| tool | purpose |
|---|---|
| `knoten_frontier()` | *"what should I work on next?"* → open claims, standing offers, unused gates |
| `knoten_index(...)` | *"anything LIKE this?"* / *"what is still open?"* → the graph, one line per node |
| `knoten_query(q)` | *"has this been tried?"* → nodes + verdicts + causes of death, by keyword |
| `knoten_get(id)` | full node, including the post-mortem |
| `knoten_gates()` | *"what must this survive?"* → the gates, their rule, and their record |
| `knoten_commit(node)` | append a node (validates first; **rejects on rule violation**) |
| `knoten_update(id, …)` | move a node's status and append to it — the lifecycle in §3, walkable |
| `knoten_attach(id, files)` | the script that ran it and the plot that shows it |
| `knoten_path(start, end)` | show the research path — how did we get from A to B? |
| `knoten_validate()` | run the graph's own declared rules |

The gate is the point, on either surface: **a `status: alive` node with no
`kn:survivedGate` edge is refused**, whether it arrives via `knoten commit` or
`knoten_commit`. The system will not let an agent — or a human — record an unchallenged
claim as a finding.

The candidate node is parsed and checked **in memory**; nothing reaches the filesystem
until it is clean.

Each MCP tool is a plain function: its name is the tool name, its docstring is the
description the agent reads, and its annotated signature IS the input schema. There is no
second copy of any of that to drift — the failure mode being avoided is a hand-written
schema that quietly stops matching the code it documents. The loop above lives once, in
the server's `instructions`, rather than being re-asserted by every tool description.

And because the `id` becomes a filename and is authored by an LLM, it is constrained to
kebab-case — an id is not a path.

`knoten_path` takes `start` / `end`. It took `from` / `to` until the 2.0 migration, where
the schema is derived from the function signature and `from` is a Python keyword.

---

### 8.1 Retrieval — two questions, two mechanisms

*"Has this been tried?"* and *"have we done anything **like** this?"* are different
questions, and one mechanism cannot answer both.

`knoten_query` is keyword retrieval: tokens weighted by idf, ranked, capped. It was
originally an **AND** over tokens, which made the tool's headline question fail on its own
README example — `"has anyone tried self-consistency?"` matched nothing, because the node
contains no "has", "anyone" or "tried", and the agent was told the work was untested. A
false negative is the only failure mode of this system that causes real work to be redone.
So: OR with ranking, the full frontmatter in the haystack (`repro.model` was unsearchable),
and **a miss now says so honestly** — "no keyword match, this is NOT proof it is untested."

`knoten_index` answers the second question, and it does so by **not being a search engine
at all**. It emits the whole graph as one line per node — id, verdict, tags, claim — and
lets the reader judge relatedness. The reader is an LLM; it is a better semantic matcher
than any similarity metric we could ship, and it costs nothing to ship. On a 500-node
graph a broad `query` returned ~83k tokens; the same graph's index is ~9k, and one tag
narrows it to ~2.5k.

That is why `tags:` is now a declared, enforced vocabulary. Tags are not a search
mechanism — they are the **filter axis** that keeps the index readable as the graph grows,
which is what makes the whole scheme survive the accumulation it exists to encourage.

**Embeddings are deliberately not here.** The paraphrase gap they solve is real, but the
agent already closes it; `sentence-transformers` would put torch behind a project whose
pitch is one dependency, and an embedding API would end the offline story. All three
surfaces go through one function, `core.retrieve()`, so a `knoten[semantic]` extra can
replace that body without touching a tool schema — the day a graph outgrows a tag-filtered
index, which the 1k–5k node case does not.

## 9. Phasing

| phase | deliverable | status |
|---|---|---|
| **0** | **Dogfood** — encode a real investigation by hand | ✅ done |
| **1** | `knoten` CLI (`init/new/validate/query/index/frontier/gates/path/show/attach`) + rule engine | ✅ done |
| **2** | **MCP server** (the tools above) | ✅ done — later demoted to the shell-less fallback (§8) |
| **2.5** | CLI becomes the primary agent surface: `ops.py` as the one implementation behind CLI/MCP, `--json` on every read, `commit`/`update` on the CLI, `SKILL.md` | ✅ done |
| 3 | Static-site graph viewer → GitHub Pages | free hosting |
| 4 | Hosted multi-graph service | probably never needed |

Phase 0 **validated the schema against real content** — including retractions, structural
blockers, and prose that no JSON schema could hold.

---

## 10. Licensing note

**Basic Memory is AGPL-3.0** (strong copyleft). Its MCP tool design is excellent and
worth studying, but vendoring its code would make this AGPL too. **Reimplement, do
not fork**, if this may ever be distributed or hosted.

Micropublications, nanopublications, PROV-O and LinkML are all open and safe to adopt.

---

## 11. Open questions

1. **Do we emit RDF?** A `graph.ttl` export would make the graph interoperable with
   the nanopub ecosystem for ~nothing. Probably yes, phase 3.
2. ~~**Embeddings for `knoten_query`?**~~ **Answered (§8.1): no, and probably never.**
   The consumer is an LLM, so the whole graph as a one-line-per-node index beats vector
   similarity at the question that matters ("anything like this?") for zero dependencies.
   Revisit only when a tag-filtered index stops fitting in a context window.
3. **Multi-graph federation** — one agent querying trading *and* biology graphs.
   Defer until a second graph exists.
4. **A closed `cause` vocabulary (§5) as a rule primitive?** Would need a
   `require_field_one_of` key. Wait until a second graph wants it — a primitive with one
   caller is a guess.

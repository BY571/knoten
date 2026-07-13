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
- **We do not build a UI first.** A static site generator over `graph.json` is a
  phase-3 nicety.
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
    require_sections: Why it died, reopen
    message: The post-mortem IS the asset — a dead end must become a standing offer.
```

---

## 6. Rules as configuration

The core enforces whatever the graph declares. **Domain knowledge lives in data,
never in code.**

LinkML was the original plan and was dropped: it validates *shape*, and every rule that
matters here is a *predicate over a node* ("does this claim cite a gate?"). A LinkML
schema plus a bespoke predicate layer is strictly more machinery than the predicate
layer alone. The rule engine is ~40 lines in `validate.py`.

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
    require_sections: Why it died, reopen
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
| `if_result_any` | only apply `require_result` when one of these keys is present. |

**An unknown rule key is a hard error.** A rule the engine cannot understand would
enforce nothing while reporting `✓ all rules pass` — a validator that silently accepts is
worse than no validator, because you believe you are covered. The same goes for an
unknown edge relation (`kn:killdByGate`, one letter dropped) and for frontmatter that
does not parse.

Always-on structural checks, which no graph has to declare: `dangling-edge`,
`missing-attachment`, `unknown-relation`, `authored-backlink`.

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
  .knoten/graph.json       # GENERATED index (gitignored)
```

Git provides: history, blame, diff-a-claim-as-it-changed, branches-as-research-
directions, **PRs-as-peer-review**, and hosting. We write none of it.

---

## 8. The agent surface (MCP) — this is what makes it compound

The previous attempt failed because the graph was a **byproduct of automation**: when
the orchestrator did not run, nothing was written. Meanwhile a plain chat session
produced 15 experiments whose knowledge would have evaporated without hand-written
memory files.

**Fix: make the graph primary, and make writing to it the path of least resistance.**

| tool | purpose |
|---|---|
| `knoten_query(q)` | *"has this been tried?"* → nodes + verdicts + causes of death |
| `knoten_get(id)` | full node, including the post-mortem |
| `knoten_commit(node)` | append a node (validates first; **rejects on rule violation**) |
| `knoten_path(a, b)` | show the research path — how did we get from A to B? |
| `knoten_validate()` | run the graph's own declared rules |

The gate is the point: **`knoten_commit` refuses a `status: alive` node with no
`kn:survivedGate` edge.** The system will not let an agent — or a human — record an
unchallenged claim as a finding.

The candidate node is parsed and checked **in memory**; nothing reaches the filesystem
until it is clean. And because the `id` becomes a filename and is authored by an LLM, it
is constrained to kebab-case — an id is not a path.

---

## 9. Phasing

| phase | deliverable | status |
|---|---|---|
| **0** | **Dogfood** — encode a real investigation by hand | ✅ done |
| **1** | `knoten` CLI (`init/validate/query/path/show/attach/build`) + rule engine | ✅ done |
| **2** | **MCP server** (5 tools above) | ✅ done — the compounding step |
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
2. **Embeddings for `knoten_query`?** Start with grep + frontmatter filters. Add
   semantic search only if grep demonstrably fails.
3. **Multi-graph federation** — one agent querying trading *and* biology graphs.
   Defer until a second graph exists.
4. **A closed `cause` vocabulary (§5) as a rule primitive?** Would need a
   `require_field_one_of` key. Wait until a second graph wants it — a primitive with one
   caller is a guess.

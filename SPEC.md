# knoten — design spec

**Falsification-first research graphs in git.**

**Status:** draft · **Date:** 2026-07-13 · **Validated against:** a real 23-node investigation

---

## 1. The problem, and why nobody has solved it

A prior-art search (108 agents, 25+ primary sources) established that **no existing
tool combines git-native markdown nodes + falsification-aware typed edges +
agent-native read/write**. The gap is *structural*: every candidate buys one axis by
foreclosing another.

| system | markdown + git | falsification edges | agent-native surface |
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

`question` · `source` · `idea` · `hypothesis` · `experiment` · `finding` · `retraction` · `gate`

A convention only — the core checks `node_types` for membership and nothing else. A graph
begins with a `question` and everything descends from it; a `source` is where the work came
from, including the author's own intuition; `gate` stands outside the loop the others form,
the bar a claim must survive rather than a stage it passes through. `method` is
deliberately unclaimed, reserved for "the approach derived from findings that survived".

### Status lifecycle

```
open ──► alive ──────► superseded
   └───► dead          (a better claim replaced it)
   └───► retracted     (WE WERE WRONG — the most valuable node type)
```

Every arrow is walkable via `knoten update`, which appends and moves the status but cannot
rewrite a claim. Immutability protects **what was claimed**, never the status — the status
*is* the lifecycle, and git holds the before and after (§7). Without it an agent could open
a hypothesis and never close it, leaving a settled question `open` on every frontier.

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

#### How a claim was reached

| predicate | inverse | meaning |
|---|---|---|
| `kn:explains` | `kn:explainedBy` | this claim is the best explanation of that observation |
| `kn:generalises` | `kn:generalisedBy` | this claim generalises those instances |
| `kn:followsFrom` | `kn:entails` | this claim follows from that one by argument |

Peirce named three ways a claim gets proposed; Popper named the one way it gets tested.
knoten had rich vocabulary for the testing and one untyped `prov:wasDerivedFrom` for all
three of the others.

The distinction earns its place because a rule can act on it — `require_edge_target`
matches on the relation, so with one untyped derivation there is no way to say *"a
generalisation must cite three findings"* without saying it of every derivation. A
per-edge qualifier (`{rel: prov:wasDerivedFrom, mode: induction}`) parses today and no
rule key can see it. `prov:wasDerivedFrom` stays as the untyped form for graphs that do
not care to draw the distinction.

**A kind confers no status.** A generalisation is a conjecture that repeated observation
happened to suggest; it faces the same gates as any other claim and can still die. A
vocabulary in which "established by induction" exempted a claim from falsification would
contradict the thesis of this tool.

The field has no way to say *"this claim survived / was killed by a named
methodological gate."* DISK's `LineOfInquiry` is the closest blueprint and it
**cannot express failure** (zero falsification terms in its ontology).

| predicate | domain → range | meaning |
|---|---|---|
| `kn:survivedGate` | claim → gate | **claim passed this gate.** A `status: alive` claim MUST have ≥1. |
| `kn:killedByGate` | claim → gate | **the gate that killed it.** The predicate the entire field is missing. |
| `kn:blockedBy` | claim → finding | a *structural* blocker (a fee schedule, a venue, a data licence) — not a result, a wall. |

`kn:survivedGate` + the rule engine is the whole safety mechanism: **an unchallenged
claim cannot be marked alive.**

---

## 5. Cause of death — a closed vocabulary

Adapted from arXiv:2606.21024 (*Negative Knowledge as Failure-aware Shared Memory*,
Jun 2026), grounded in the seven real deaths of the source session:

| `cause` | what it means |
|---|---|
| `no_signal` | the effect is not there |
| `cost_hurdle` | real effect, too small to pay its costs |
| `structural_blocker` | a wall you cannot climb — a venue you cannot access |
| `selection_bias` | a cherry-pick, usually an unintentional one |
| `weak_baseline` | it only beat a strawman |
| `underpowered` | too few independent bets — any t-stat on n<30 |
| `crowding_decay` | it was real; it got arbitraged away |

Every dead or retracted node should carry a `cause`, a `## Why it died`, and a
**`## What would reopen this`**. The last is non-negotiable: it converts a dead end into a
**standing offer**, and it is what stops the next agent re-running it.

**The core does not enforce this vocabulary and must not** — a cause of death is domain
knowledge, and §2 says the core knows no domain. The list is a convention; your graph
enforces the parts it cares about, as data (§6's `require_field_one_of`). What that buys
is a death you can *query* rather than merely record:

```bash
knoten index --where cause=weak_baseline    # we have a stronger baseline now. what reopens?
```

That is a query when the cause is a field and a re-read of every post-mortem when it is
prose — the moment a research graph pays for itself.

---

## 6. Rules as configuration

The core enforces whatever the graph declares. **Domain knowledge lives in data, never in
code.** LinkML was the original plan and was dropped: it validates *shape*, and every rule
that matters here is a *predicate over a node* ("does this claim cite a gate?"). A schema
plus a bespoke predicate layer is strictly more machinery than the predicate layer alone,
which is ~50 lines in `validate.py`.

```yaml
# graph.yaml — each rule is a SCAR. Write one only when you have a corpse.
rules:
  - id: live-claims-must-cite-their-gates
    when_status: alive
    when_type: hypothesis, finding
    require_edge: kn:survivedGate
    message: An unchallenged claim is not a finding, it is a hope.

  - id: deaths-must-name-a-cause
    when_status: dead, retracted
    require_sections: Why it died, What would reopen this
    require_field_one_of:
      cause: [no_signal, cost_hurdle, structural_blocker, selection_bias,
              weak_baseline, underpowered, crowding_decay]
    message: A cause of death you cannot filter on is a story, not an index.
```

| key | effect |
|---|---|
| `id` | **required.** Names the rule in the violation. |
| `message` | what the human reads when it fires. |
| `when_status` / `when_type` | only apply to these statuses / types (comma-separated). |
| `require_edge` | node must declare this relation. |
| `require_sections` | body must contain these `## ` headings. |
| `require_field` | frontmatter must carry this key, with any non-empty value. |
| `forbid_fields` | this type must NOT carry these frontmatter keys (comma-separated). |
| `require_result` | `results:` must carry this key. |
| `require_result_min` | `{key: floor}` — numeric floor on a result. |
| `require_field_one_of` | `{field: [allowed]}` — a frontmatter field constrained to a closed set. |
| `require_edge_target` | an edge of this relation must point at a node of this type/status; `min` counts distinct targets. |
| `require_backlink` | something of this type/status must point AT this node (`rel` is the generated inverse). |

`forbid_fields` is the only key that says what a type must *not* be, and it exists because every other key is positive: a graph could demand a hypothesis carry a claim and never stop it also carrying the run and the result.

`require_field` is the open sibling of `require_field_one_of`: a url or a doi has no
closed set of legal values, so the only thing worth demanding is that the answer got
written down. `knoten new` scaffolds such a field EMPTY rather than as `TODO`, because a
placeholder would satisfy the rule and `new` + `validate` would stop being a checklist.

The last two are the only checks that look past the node being checked, and they are what
make a claim's fate outlive the moment it was written: `require_edge_target` fails
everything derived from a finding the day that finding dies, and `require_backlink` is the
only way to police work that was *abandoned* rather than written badly, since a hypothesis
nobody tested declares nothing wrong.

**An unknown rule key is a hard error**, as is an unknown edge relation
(`kn:killdByGate`, one letter dropped) and frontmatter that does not parse. A rule the
engine cannot understand would enforce nothing while reporting `✓ all rules pass`, and a
validator that silently accepts is worse than no validator.

The graph declares its vocabulary too. `node_types` takes a list, or a mapping when you
want to write down what the words mean — the only place they *can* be defined, since the
core refuses to:

```yaml
node_types:
  hypothesis: a falsifiable claim derived from an idea
  gate:       a standing rule every claim must survive; a bar, not a stage
statuses:   [open, alive, dead, retracted, superseded, active]
tags:       [decoding, reasoning, prompting, evaluation]
```

The keys are the vocabulary either way; the values are for the reader and for
`knoten viz`, which labels each column with them. `knoten init` writes the mapping form.
Declare none and none is checked (§2) — but a graph that *does* declare has said those are
the only legal words, so `type: hypthesis` is a typo and `status: ded` is a claim that
would silently vanish from every query.

Always-on structural checks, which no graph declares: `dangling-edge`,
`missing-attachment`, `unknown-relation`, `authored-backlink`, `missing-type`,
`unknown-type`, `unknown-status`, `unknown-tag`, `malformed-tags`, `mismatched-id`,
`malformed-results`, `malformed-repro`, and `not-a-gate` (a migration aid, deletable once
graphs written before the gate rename have moved).

A biology graph declares entirely different rules. The core never changes.

---

## 7. Layout

```
my-graph/
  graph.yaml               # identity + rules
  nodes/*.md               # one node per file — the source of truth
  attachments/<id>/*       # the script that killed it, the plot that shows why
```

`knoten new` and `knoten commit` stamp `created:`; `knoten update` stamps `updated:`.
Both are plain ISO date strings, which the YAML 1.2 loader keeps as strings rather than
coercing to `datetime.date`. Git knows when the *file* changed, which is not when the
*claim* did — a typo fix and a status flip are the same event to git — and reading it
would cost one subprocess per node to order a frontier.

Git provides: history, blame, diff-a-claim-as-it-changed, branches-as-research-
directions, **PRs-as-peer-review**, and hosting. We write none of it.

---

## 8. The agent surface — CLI first

This section once named a tool-protocol server as the thing that made knoten compound,
and meant it. That claim was wrong, and this is the reversal, stated plainly rather than
slid past.

The previous attempt failed because the graph was a **byproduct of automation**: when the
orchestrator did not run, nothing was written, while a plain chat session produced 15
experiments whose knowledge would have evaporated without hand-written memory files. The
fix was right — make the graph primary, and make writing to it the path of least
resistance. The delivery was not.

**The measurement.** That surface loaded ~2,340 tokens into every session whether the
agent touched the graph or not (1,928 of schema + 412 of instructions), against ~304 for
`knoten --help` and only when asked. Format compounded it: the same 55 nodes cost ~2,551
tokens as its JSON (46/node) against ~1,185 as columnar prose (21/node) — 2.2x. That is
why prose is the CLI's default and `--json` is opt-in.

**So the CLI is the agent surface, and the server has been deleted.** `ops.py` holds one
implementation of every read — index, query, frontier, gates, show, validate, path — as a
function returning a dict, which the CLI renders as prose or dumps with `--json`; `commit`
and `update` are shared the same way. `SKILL.md` teaches the loop: `frontier` →
`index`/`query` → `show` → `gates` → `commit`/`update` → `attach`.

Keeping the server for shell-less clients was defensible and stayed defensible; it was
just outweighed. Every cross-surface drift bug this project recorded came from having two
— `update`'s refusal built twice with different keys, `--field` coercing `2` to `2.0` on
one side only, `ops.index(query=...)` reachable in Python with no CLI flag. The shell is
the interface.

What it taught, which outlived it:

- **Derive the schema, never write one.** Its tools were plain functions: the name was the
  tool name, the docstring the description, the annotated signature the input schema. A
  hand-written schema quietly stops matching the code it documents; a derived one cannot.
- **State the loop once.** Four tools each shouting "CALL THIS FIRST" give an agent the
  same ordering signal as none. `SKILL.md` inherited that job.
- **The gate belongs under every door.** The check lives in `commit`, not in a transport,
  which is why deleting the transport removed no enforcement. The candidate node is parsed
  and checked **in memory**; nothing reaches the filesystem until it is clean.
- **An id authored by a model is not a path.** It becomes a filename, so it is constrained
  to kebab-case — a guard the CLI lacked until it was the only entry point left.

---

### 8.1 Retrieval — two questions, two mechanisms

*"Has this been tried?"* and *"have we done anything **like** this?"* are different
questions, and one mechanism cannot answer both.

`knoten query` is keyword retrieval: idf-weighted, ranked, capped. It was originally an
**AND** over tokens, which made the headline question fail on the README's own example —
`"has anyone tried self-consistency?"` matched nothing, because the node contains no
"has", "anyone" or "tried", and the agent was told the work was untested. A false negative
is the only failure of this system that causes real work to be redone. So: OR with
ranking, the full frontmatter in the haystack (`repro.model` was unsearchable), and a miss
that says so honestly — "no keyword match, this is NOT proof it is untested."

`knoten index` answers the second question by **not being a search engine**. It emits the
whole graph, one line per node — id, verdict, tags, claim — and lets the reader judge
relatedness. The reader is an LLM: a better semantic matcher than anything we could ship,
and free. On a 500-node graph a broad `query` returned ~83k tokens; the index is ~9k, and
one tag narrows it to ~2.5k. That is what `tags:` is for — not search, but the filter axis
that keeps the index readable as the graph grows.

**Embeddings are deliberately absent.** The paraphrase gap they close is real, but the
agent already closes it; `sentence-transformers` would put torch behind a project whose
pitch is one dependency, and an embedding API would end the offline story. Both readers go
through one function, `core.retrieve()`, so a `knoten[semantic]` extra could replace that
body the day a graph outgrows a tag-filtered index — which the 1k–5k node case does not.

## 9. Phasing

| phase | deliverable | status |
|---|---|---|
| **0** | **Dogfood** — encode a real investigation by hand | ✅ done |
| **1** | `knoten` CLI (`init/new/validate/query/index/frontier/gates/path/show/attach`) + rule engine | ✅ done |
| **2** | **Tool-protocol server** | ✅ done — later demoted to a fallback, then removed (§8) |
| **2.5** | CLI becomes the primary agent surface: `ops.py` as the one implementation behind every read, `--json` on every read, `commit`/`update` on the CLI, `SKILL.md` | ✅ done |
| 3 | Static-site graph viewer → GitHub Pages | free hosting |
| 4 | Hosted multi-graph service | probably never needed |

Phase 0 **validated the schema against real content** — including retractions, structural
blockers, and prose that no JSON schema could hold.

---

## 10. Licensing note

**Basic Memory is AGPL-3.0** (strong copyleft). Its tool design is excellent and
worth studying, but vendoring its code would make this AGPL too. **Reimplement, do
not fork**, if this may ever be distributed or hosted.

Micropublications, nanopublications, PROV-O and LinkML are all open and safe to adopt.

---

## 11. Open questions

1. **Do we emit RDF?** A `graph.ttl` export would make the graph interoperable with
   the nanopub ecosystem for ~nothing. Probably yes, phase 3.
2. ~~**Embeddings for `knoten query`?**~~ **Answered (§8.1): no, and probably never.**
   The consumer is an LLM, so the whole graph as a one-line-per-node index beats vector
   similarity at the question that matters ("anything like this?") for zero dependencies.
   Revisit only when a tag-filtered index stops fitting in a context window.
3. **Multi-graph federation** — one agent querying trading *and* biology graphs.
   Defer until a second graph exists.
4. ~~**A closed `cause` vocabulary (§5) as a rule primitive?**~~ **Answered: yes.**
   `require_field_one_of` shipped, and a second graph did want it.

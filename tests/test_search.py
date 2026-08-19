"""Retrieval: "has this been tried, or anything like it?"

Every finding here made the graph answer "no prior work" about work it was holding.
That is the one failure mode of this tool that causes real damage: it sends an agent
off to re-run an experiment that is already dead, which is the exact thing knoten
exists to prevent.
"""
import pytest

from knoten.core import load, retrieve

SELF_CONSISTENCY = """\
id: hyp-self-consistency
type: hypothesis
status: dead
tags: [decoding, reasoning]
repro:
  model: Qwen3-8B-Instruct
  data: GSM8K test, 1319 questions
results:
  acc_greedy: 0.741
"""

FEW_SHOT = """\
id: hyp-few-shot-format
type: hypothesis
status: alive
tags: [prompting]
repro:
  model: Qwen3-8B-Instruct
  data: GSM8K test, 1319 questions
"""

GATE = """\
id: gate-compute-matched-baseline
type: gate
status: active
tags: [evaluation]
"""


@pytest.fixture
def research(graph):
    """Three nodes mirroring examples/llm-research."""
    return (graph
            .node("hyp-self-consistency", SELF_CONSISTENCY,
                  "# Self-consistency (sample 5, majority vote) beats greedy decoding\n\n"
                  "Sampling five chains and taking the majority answer scored 79.2%.\n")
            .node("hyp-few-shot-format", FEW_SHOT,
                  "# Delimiting few-shot examples with XML tags improves accuracy\n\n"
                  "The gain survives at the same token budget as the plain baseline.\n")
            .node("gate-compute-matched-baseline", GATE,
                  "# Gate: compute-matched baseline\n\n"
                  "A method that spends more compute is compared against the same budget.\n"))


def ids(hits):
    return [n.id for n in hits]


def test_a_natural_question_finds_the_node_it_is_about(research):
    """The README's own example call — knoten query("has anyone tried self-consistency?")
    — returned NOTHING, because every token had to appear and the node contains no
    "has", "anyone" or "tried". The agent was told the work was untested."""
    hits = retrieve(load(research.root), "has anyone tried self-consistency?")

    assert ids(hits)[0] == "hyp-self-consistency"


def test_words_that_match_nothing_do_not_suppress_the_ones_that_do(research):
    """The AND was the whole bug: one unmatched token silenced the entire query."""
    hits = retrieve(load(research.root), "self-consistency zzzz qqqq")

    assert ids(hits) == ["hyp-self-consistency"]


def test_a_term_that_lives_only_in_frontmatter_is_searchable(research):
    """`repro.model: Qwen3-8B-Instruct` sat in the frontmatter and the haystack was
    id + body + tags, so `query "Qwen3"` found nothing at all."""
    hits = retrieve(load(research.root), "Qwen3")

    assert set(ids(hits)) == {"hyp-self-consistency", "hyp-few-shot-format"}


def test_every_node_that_used_the_benchmark_is_found(research):
    """Both nodes ran on GSM8K; only one said so in its prose. The other was invisible
    — a partial answer that looks like a complete one."""
    hits = retrieve(load(research.root), "GSM8K")

    assert set(ids(hits)) == {"hyp-self-consistency", "hyp-few-shot-format"}


def test_a_query_matching_nothing_returns_nothing(research):
    """Loosening AND to OR must not turn every query into "everything"."""
    assert retrieve(load(research.root), "quantum annealing") == []


def test_a_stopword_only_query_returns_nothing(research):
    """`the` appears in most nodes. Matching on it would return the whole graph and
    report it as prior art."""
    assert retrieve(load(research.root), "the") == []


def test_the_more_specific_node_ranks_first(research):
    """Two nodes mention XML tags; only one is about few-shot formatting."""
    hits = retrieve(load(research.root), "few-shot XML delimited examples")

    assert ids(hits)[0] == "hyp-few-shot-format"


# ------------------------------------------------------------------ filtering

def test_filters_narrow_without_a_query(research):
    """The index path: no search term, just "show me the decoding work"."""
    hits = retrieve(load(research.root), None, tags=["decoding"])

    assert ids(hits) == ["hyp-self-consistency"]


def test_filters_and_query_compose(research):
    hits = retrieve(load(research.root), "GSM8K", status=["alive"])

    assert ids(hits) == ["hyp-few-shot-format"]


def test_no_query_and_no_filter_returns_the_whole_graph(research):
    hits = retrieve(load(research.root), None)

    assert len(hits) == 3


def test_type_filter(research):
    hits = retrieve(load(research.root), None, type=["gate"])

    assert ids(hits) == ["gate-compute-matched-baseline"]


def test_a_malformed_tag_string_does_not_match_single_letters(graph):
    """`tags: decoding` (no brackets) is legal YAML that iterates as characters, so a
    filter for the tag `d` would match it. `validate` calls this malformed-tags; the
    filter must agree rather than inventing five one-letter tags."""
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead\ntags: decoding")

    assert retrieve(load(graph.root), None, tags=["d"]) == []


def test_where_filters_on_any_frontmatter_field(graph):
    """The core knows no domain, so this is a generic field filter rather than a `cause`
    parameter: "everything that died of a weak baseline" is one graph's question."""
    graph.node("hyp-a", "id: hyp-a\ntype: hypothesis\nstatus: dead\ncause: weak_baseline")
    graph.node("hyp-b", "id: hyp-b\ntype: hypothesis\nstatus: dead\ncause: no_signal")

    hits = retrieve(load(graph.root), None, where={"cause": ["weak_baseline"]})

    assert ids(hits) == ["hyp-a"]


def test_where_drops_a_node_that_lacks_the_field(graph):
    graph.node("hyp-a", "id: hyp-a\ntype: hypothesis\nstatus: dead")

    assert retrieve(load(graph.root), None, where={"cause": ["no_signal"]}) == []

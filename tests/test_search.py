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
    hits = retrieve(load(research.root), "has anyone tried self-consistency?")
    assert ids(hits)[0] == "hyp-self-consistency"


def test_words_that_match_nothing_do_not_suppress_the_ones_that_do(research):
    """The AND was the whole bug: one unmatched token silenced the entire query."""
    hits = retrieve(load(research.root), "self-consistency zzzz qqqq")

    assert ids(hits) == ["hyp-self-consistency"]


def test_a_term_that_lives_only_in_frontmatter_is_searchable(research):
    hits = retrieve(load(research.root), "Qwen3")
    assert set(ids(hits)) == {"hyp-self-consistency", "hyp-few-shot-format"}


def test_every_node_that_used_the_benchmark_is_found(research):
    """Both nodes ran on GSM8K; only one said so in its prose."""
    hits = retrieve(load(research.root), "GSM8K")
    assert set(ids(hits)) == {"hyp-self-consistency", "hyp-few-shot-format"}


def test_the_more_specific_node_ranks_first(research):
    """Two nodes mention XML tags; only one is about few-shot formatting."""
    hits = retrieve(load(research.root), "few-shot XML delimited examples")

    assert ids(hits)[0] == "hyp-few-shot-format"


# ------------------------------------------------------------------ filtering

def test_a_malformed_tag_string_does_not_match_single_letters(graph):
    graph.node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: dead\ntags: decoding")
    assert retrieve(load(graph.root), None, tags=["d"]) == []



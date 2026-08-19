"""Filing a claim.

This lived inside a transport layer, which meant two things: 49 lines of domain logic sat
in the wrong place, and creating a node programmatically required that transport's SDK — an
optional dependency for a transport you may not be using. `attach` and `update` never had
that problem; they live in their own modules and lock themselves.
"""

from knoten import commit as commit_mod
from knoten.commit import commit
from knoten.core import load

RULES = """\
name: t
statuses: [open, alive, dead]
node_types: [hypothesis, gate]
rules:
  - id: live-claims-must-cite-their-gates
    when_status: alive
    require_edge: kn:survivedGate
    message: An unchallenged claim is not a finding, it is a hope.
"""

ALIVE_CITING = ("type: hypothesis\nstatus: alive\n"
                "links:\n  - {rel: kn:survivedGate, to: gate-new}")

def test_a_valid_node_is_written(graph):
    graph.rules(RULES).node("gate-new", "id: gate-new\ntype: gate\nstatus: open")

    res = commit(graph.root, "hyp-x", ALIVE_CITING, "# A claim\n")

    assert res["status"] == "COMMITTED"
    assert load(graph.root)["hyp-x"].status == "alive"

def test_a_violating_node_never_reaches_disk(graph):
    graph.rules(RULES)

    res = commit(graph.root, "hyp-x", "type: hypothesis\nstatus: alive", "# A claim\n")

    assert res["status"] == "REJECTED"
    assert not (graph.root / "nodes" / "hyp-x.md").exists()

def test_commit_validates_the_graph_as_it_is_when_the_lock_is_held(graph, monkeypatch):
    """The graph was read BEFORE the lock was taken and validated against that stale
    snapshot — the same read-modify-write bug that `attach` was fixed for one commit
    earlier, reintroduced one function over.

    Reproduced with threads: agent B commits `gate-new`, agent A commits a claim citing
    it, and A is rejected with "gate-new does not exist" while gate-new.md is on disk.
    Here that race is made deterministic — a peer lands its node in the window between
    entering commit and acquiring the lock.
    """
    graph.rules(RULES)
    real_lock = commit_mod.graph_lock

    def peer_commits_first(root):
        graph.node("gate-new", "id: gate-new\ntype: gate\nstatus: open")
        monkeypatch.setattr(commit_mod, "graph_lock", real_lock)   # once, not every call
        return real_lock(root)

    monkeypatch.setattr(commit_mod, "graph_lock", peer_commits_first)

    res = commit(graph.root, "hyp-x", ALIVE_CITING, "# A claim\n")

    assert res["status"] == "COMMITTED", res

def test_an_existing_node_is_not_overwritten(graph):
    graph.rules(RULES).node("hyp-x", "id: hyp-x\ntype: hypothesis\nstatus: open")

    res = commit(graph.root, "hyp-x", "type: hypothesis\nstatus: open", "# again\n")

    assert res["status"] == "REJECTED"
    assert "already exists" in res["reason"]

def test_an_id_that_escapes_the_graph_is_refused(graph, tmp_path):
    graph.rules(RULES)

    res = commit(graph.root, "../../pwned", "type: hypothesis\nstatus: open", "# x\n")

    assert res["status"] == "REJECTED"
    assert not (tmp_path.parent / "pwned.md").exists()


# ------------------------------------------------------------- the duplicate warning

def test_commit_warns_when_the_new_claim_resembles_a_settled_one(graph):
    """A loop running for weeks WILL re-propose an idea it already settled, worded
    differently, under a new id. The graph then holds two answers to one question and the
    second has no post-mortem."""
    graph.node("hyp-self-consistency", "id: hyp-self-consistency\ntype: hypothesis\n"
                                       "status: dead",
               "# Self-consistency majority vote beats greedy decoding\n\n"
               "## Why it died\nThe gain was compute, not method.\n")

    res = commit(graph.root, "hyp-sample-and-vote", "type: hypothesis\nstatus: open",
                 "# Majority vote over sampled chains beats greedy decoding\n")

    assert res["status"] == "COMMITTED"          # a warning, never a block
    assert [s["id"] for s in res["similar"]] == ["hyp-self-consistency"]
    assert res["similar"][0]["verdict"] == "DEAD"
    assert "supersede" in res["warning"]


def test_an_unrelated_claim_gets_no_warning(graph):
    graph.node("hyp-self-consistency", "id: hyp-self-consistency\ntype: hypothesis\n"
                                       "status: dead", "# Self-consistency beats greedy\n")

    res = commit(graph.root, "hyp-tokeniser", "type: hypothesis\nstatus: open",
                 "# A byte-level tokeniser lowers perplexity\n")

    assert "similar" not in res


def test_one_shared_word_is_not_a_duplicate(graph):
    """The warning is worth nothing if it fires on every commit, so it takes two shared
    title words rather than one.

    Two IS a loose bar — "dropout improves accuracy" and "warmup improves accuracy" would
    trip it — and that is deliberate: a false positive costs the agent one line it can
    dismiss, while a false negative costs a duplicated experiment. The asymmetry says lean
    permissive. Anything tighter (idf over titles, overlap ratios) misbehaves on the small
    graphs where duplicates start appearing."""
    graph.node("hyp-a", "id: hyp-a\ntype: hypothesis\nstatus: dead",
               "# Dropout lowers perplexity\n")

    res = commit(graph.root, "hyp-b", "type: hypothesis\nstatus: open",
                 "# Warmup improves accuracy on reasoning\n")

    assert "similar" not in res


def test_an_unsettled_claim_is_not_reported_as_prior_art(graph):
    """`open` is not an answer. Warning about one would tell the agent the question is
    closed when it is exactly what is still being asked."""
    graph.node("hyp-open", "id: hyp-open\ntype: hypothesis\nstatus: open",
               "# Majority vote over sampled chains beats greedy decoding\n")

    res = commit(graph.root, "hyp-new", "type: hypothesis\nstatus: open",
                 "# Majority vote over sampled chains beats greedy decoding\n")

    assert "similar" not in res


def test_the_warning_reaches_the_reader(graph, monkeypatch, capsys):
    """The dict carried it and nothing printed it — a warning nobody sees is not a
    warning."""
    from knoten.cli import main
    graph.node("hyp-self-consistency", "id: hyp-self-consistency\ntype: hypothesis\n"
                                       "status: dead", "# Self-consistency beats greedy decoding\n")
    monkeypatch.chdir(graph.root)
    (graph.root / "fm").write_text("type: hypothesis\nstatus: open\n")
    (graph.root / "body").write_text("# Majority vote beats greedy decoding\n")

    main(["commit", "hyp-new", "--frontmatter", "fm", "--body", "body"])

    assert "resembles" in capsys.readouterr().out


def test_a_malformed_candidate_is_refused_not_raised(graph):
    """`commit` promises it never raises for a bad candidate, because the caller is
    usually an agent that needs a refusal it can read rather than a traceback. That
    promise matters more now that this IS the Python API."""
    res = commit(graph.root, "hyp-bad", "id: hyp-bad\n  oops: bad indent", "# x\n")

    assert res["status"] == "REJECTED"
    assert not (graph.root / "nodes" / "hyp-bad.md").exists()

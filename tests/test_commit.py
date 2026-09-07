"""Filing a claim.

This lived inside a transport layer, which meant two things: 49 lines of domain logic sat
in the wrong place, and creating a node programmatically required that transport's SDK — an
optional dependency for a transport you may not be using. `attach` and `update` never had
that problem; they live in their own modules and lock themselves.
"""

from conftest import compressible_graph

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


GENERAL = ("type: finding\nstatus: alive\nlinks:\n"
           "  - {rel: npx:supersedes, to: finding-1}\n"
           "  - {rel: npx:supersedes, to: finding-2}\n"
           "  - {rel: kn:survivedGate, to: gate-a}\n"
           "  - {rel: kn:survivedGate, to: gate-b}")
COVERS = "# G\n\n## Covers\n- finding-1: small\n- finding-2: large\n"


def test_committing_a_general_node_flips_its_targets_and_keeps_their_claims(graph):
    compressible_graph(graph, n=2, cap=4)
    before = {i: graph.read(f"finding-{i}") for i in (1, 2)}

    res = commit(graph.root, "finding-g", GENERAL, COVERS)

    assert res["status"] == "COMMITTED"
    nodes = load(graph.root)
    assert nodes["finding-1"].status == nodes["finding-2"].status == "superseded"
    for i in (1, 2):
        after = graph.read(f"finding-{i}")
        assert f"The claim {i}." in after and after.startswith("---\n")
        assert "\n## Superseded\nsuperseded by finding-g on " in after
        assert before[i].split("---")[2].strip() in after      # the body survives verbatim


def test_a_refused_general_node_flips_nothing(graph):
    compressible_graph(graph, n=2, cap=4)

    res = commit(graph.root, "finding-g", GENERAL, "# G\n")     # no Covers

    assert res["status"] == "REJECTED"
    assert any(v["rule"] == "supersession" for v in res["violations"])
    assert load(graph.root)["finding-1"].status == "alive"
    assert not (graph.root / "nodes" / "finding-g.md").exists()


def test_a_compression_reports_what_it_freed(graph):
    compressible_graph(graph, n=4, cap=4)

    res = commit(graph.root, "finding-g", GENERAL, COVERS)

    c = res["compressed"]
    assert c["targets"] == ["finding-1", "finding-2"]
    assert c["gates"] == 2 and c["gates_bonus"] is True
    assert c["question"] == "question-q"
    assert (c["free"], c["count"]) == (1, 4)          # 4 - 2 + 1 = 3 alive, budget 4
    assert (c["rules"], c["specifics"]) == (1, 2)


def test_the_budget_refuses_the_one_too_many_and_a_compression_unblocks_it(graph):
    compressible_graph(graph, n=4, cap=4)
    fifth = ("type: finding\nstatus: alive\ncreated: 2026-02-01\nlinks:\n"
             "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
             "  - {rel: kn:survivedGate, to: gate-a}")

    res = commit(graph.root, "finding-5", fifth, "# 5\n")
    assert res["status"] == "REJECTED"
    assert "Compress first" in res["violations"][0]["message"]
    assert not (graph.root / "nodes" / "finding-5.md").exists()

    assert commit(graph.root, "finding-g", GENERAL, COVERS)["status"] == "COMMITTED"
    assert commit(graph.root, "finding-5", fifth, "# 5\n")["status"] == "COMMITTED"


def test_a_backdated_one_too_many_is_still_refused(graph):
    """The cap is a fact about the graph, not a race the newest node loses. Refusing on
    blame let an author hand-write an old `created:`, put the violation on somebody else's
    node, and walk through a full budget."""
    compressible_graph(graph, n=4, cap=4)
    fifth = ("type: finding\nstatus: alive\ncreated: 2001-01-01\nlinks:\n"
             "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
             "  - {rel: kn:survivedGate, to: gate-a}")

    res = commit(graph.root, "finding-5", fifth, "# 5\n")

    assert res["status"] == "REJECTED"
    assert "5 alive finding under question-q, budget 4" in res["violations"][0]["message"]
    assert not (graph.root / "nodes" / "finding-5.md").exists()


def test_a_partial_compression_lands_on_a_graph_already_over_its_budget(graph):
    """Six alive under a cap of three. A general node over three of them still leaves four,
    so refusing anything over the cap would make the one move that helps impossible. What
    the budget refuses is a write that GROWS an over-cap group, which the next finding does.
    """
    compressible_graph(graph, n=6, cap=3)
    general = ("type: finding\nstatus: alive\nlinks:\n"
               "  - {rel: npx:supersedes, to: finding-1}\n"
               "  - {rel: npx:supersedes, to: finding-2}\n"
               "  - {rel: npx:supersedes, to: finding-3}\n"
               "  - {rel: kn:survivedGate, to: gate-a}\n"
               "  - {rel: kn:survivedGate, to: gate-b}")
    covers = "# G\n\n## Covers\n- finding-1: a\n- finding-2: b\n- finding-3: c\n"
    seventh = ("type: finding\nstatus: alive\nlinks:\n"
               "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
               "  - {rel: kn:survivedGate, to: gate-a}")

    assert commit(graph.root, "finding-g", general, covers)["status"] == "COMMITTED"

    res = commit(graph.root, "finding-7", seventh, "# 7\n")
    assert res["status"] == "REJECTED"
    assert "5 alive finding under question-q, budget 3" in res["violations"][0]["message"]


def test_a_target_whose_filename_is_not_a_legal_id_is_refused_not_raised(graph):
    """`commit` promises a refusal an agent can read rather than a traceback. `node_path`
    raises on an id that is not kebab-case, and a file written by hand carries whatever
    name the filesystem allowed -- so the promise has to cover the targets too."""
    compressible_graph(graph, n=1, cap=4)
    (graph.root / "nodes" / "Finding-A.md").write_text(
        "---\nid: Finding-A\ntype: finding\nstatus: alive\nlinks:\n"
        "  - {rel: prov:wasDerivedFrom, to: question-q}\n---\n\n# A\n", encoding="utf-8")

    res = commit(graph.root, "finding-g",
                 "type: finding\nstatus: alive\nlinks:\n"
                 "  - {rel: npx:supersedes, to: Finding-A}\n"
                 "  - {rel: npx:supersedes, to: finding-1}\n"
                 "  - {rel: kn:survivedGate, to: gate-a}",
                 "# G\n\n## Covers\n- Finding-A: a\n- finding-1: b\n")

    assert res["status"] == "REJECTED"
    assert "not a valid node id" in res["reason"]
    assert not (graph.root / "nodes" / "finding-g.md").exists()


def test_a_single_replacement_reports_one_line_worth(graph):
    compressible_graph(graph, n=2, cap=4)
    one = ("type: finding\nstatus: alive\nlinks:\n"
           "  - {rel: npx:supersedes, to: finding-1}\n"
           "  - {rel: kn:survivedGate, to: gate-a}")

    res = commit(graph.root, "finding-r", one, "# R\n\n## Covers\n- finding-1: replaced\n")

    assert res["compressed"]["targets"] == ["finding-1"]
    assert load(graph.root)["finding-1"].status == "superseded"


def test_a_graph_without_superseded_status_refuses_the_compression(graph):
    """The flip is validated BEFORE anything is written: a `graph.yaml` that never
    declared `superseded` makes every target invalid the instant it is flipped, and that
    must refuse the whole commit, not land the general node with a broken target beside
    it."""
    graph.rules("""\
name: t
statuses: [open, alive, dead]
node_types: [question, finding, gate]
rules: []
""")
    graph.node("question-q", "id: question-q\ntype: question\nstatus: open", "# Q\n")
    graph.node("gate-a", "id: gate-a\ntype: gate\nstatus: open", "# A\n")
    graph.node("gate-b", "id: gate-b\ntype: gate\nstatus: open", "# B\n")
    graph.node("finding-1", "id: finding-1\ntype: finding\nstatus: alive\nlinks:\n"
                            "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
                            "  - {rel: kn:survivedGate, to: gate-a}", "# 1\n\nThe claim 1.\n")
    graph.node("finding-2", "id: finding-2\ntype: finding\nstatus: alive\nlinks:\n"
                            "  - {rel: prov:wasDerivedFrom, to: question-q}\n"
                            "  - {rel: kn:survivedGate, to: gate-b}", "# 2\n\nThe claim 2.\n")

    res = commit(graph.root, "finding-g", GENERAL, COVERS)

    assert res["status"] == "REJECTED"
    assert not (graph.root / "nodes" / "finding-g.md").exists()
    nodes = load(graph.root)
    assert nodes["finding-1"].status == "alive" and nodes["finding-2"].status == "alive"


SECTION_RULES = """\
name: t
statuses: [open, alive, dead, superseded]
node_types: [question, finding, gate]
rules:
  - id: superseded-explains-itself
    when_status: superseded
    require_sections: [Why it was superseded]
    message: A superseded claim must say why.
"""


def test_a_rule_that_only_fires_on_superseded_refuses_the_compression(graph):
    """A target that is perfectly fine ALIVE can still fail once flipped, if a rule
    fires only on `superseded` (e.g. demanding a post-mortem section). That failure must
    be caught before the write, not raised after the general node is already on disk."""
    compressible_graph(graph, n=2, cap=4)
    graph.rules(SECTION_RULES)   # neither finding-1 nor finding-2 has the section

    res = commit(graph.root, "finding-g", GENERAL, COVERS)

    assert res["status"] == "REJECTED"
    assert any(v["rule"] == "superseded-explains-itself" for v in res["violations"])
    assert not (graph.root / "nodes" / "finding-g.md").exists()
    nodes = load(graph.root)
    assert nodes["finding-1"].status == "alive" and nodes["finding-2"].status == "alive"


THIRD_PARTY_RULES = """\
name: t
statuses: [open, alive, dead, superseded]
node_types: [question, finding, gate, note]
rules:
  - id: needs-two-alive-supports
    when_type: note
    require_edge_target: {rel: mp:supports, status: alive, min: 2}
    message: A note needs two alive supports.
"""


def test_a_third_party_node_broken_by_the_flip_refuses_the_compression(graph):
    """The flip can invalidate a node that is neither the general node nor one of its
    targets: `note-x` here rests on two alive supports, and flipping both to superseded
    breaks it. That must refuse the compression, naming `note-x` — not go through and
    leave a rule violation nobody was told about."""
    compressible_graph(graph, n=2, cap=4)
    graph.rules(THIRD_PARTY_RULES)
    graph.node("note-x", "id: note-x\ntype: note\nstatus: open\nlinks:\n"
                         "  - {rel: mp:supports, to: finding-1}\n"
                         "  - {rel: mp:supports, to: finding-2}", "# X\n")

    res = commit(graph.root, "finding-g", GENERAL, COVERS)

    assert res["status"] == "REJECTED"
    assert any(v["rule"] == "needs-two-alive-supports" and v["node"] == "note-x"
              for v in res["violations"])
    assert not (graph.root / "nodes" / "finding-g.md").exists()
    nodes = load(graph.root)
    assert nodes["finding-1"].status == "alive" and nodes["finding-2"].status == "alive"


PRINCIPLE_RULES = """\
name: t
statuses: [open, alive, dead, superseded]
node_types: [question, finding, gate, principle]
compressible: [finding, principle]
rules:
  - id: compress-before-you-accumulate
    max_alive: {type: finding, per: question, count: 4}
    message: Compress first.
"""


def test_a_principle_general_node_reports_the_finding_budget(graph):
    """`compression_report`'s budget slot is matched on what the TARGETS are typed, not
    what the general node itself is typed: a `principle` node generalising `finding`s
    must find the finding budget, not come up empty looking for a principle one."""
    compressible_graph(graph, n=4, cap=4)
    graph.rules(PRINCIPLE_RULES)
    principle = ("type: principle\nstatus: alive\nlinks:\n"
                "  - {rel: npx:supersedes, to: finding-1}\n"
                "  - {rel: npx:supersedes, to: finding-2}\n"
                "  - {rel: kn:survivedGate, to: gate-a}\n"
                "  - {rel: kn:survivedGate, to: gate-b}")

    res = commit(graph.root, "principle-g", principle, COVERS)

    assert res["status"] == "COMMITTED"
    c = res["compressed"]
    assert c["free"] == 2 and c["count"] == 4

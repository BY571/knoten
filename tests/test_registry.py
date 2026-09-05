"""What a server knows that the graph does not: who may connect, who is invited, who
owns the box. Files, hashed, locked. Losing this directory loses availability, never the
meaning of a graph."""
import os
import stat

import pytest

from knoten.core import GraphError
from knoten.registry import ROLES, Registry


@pytest.fixture
def reg(tmp_path):
    return Registry(tmp_path / "data")


def test_the_owner_secret_is_created_once_and_kept_private(reg):
    """Printed once by `knoten serve`. If it changed between calls, the owner would be
    locked out of their own server on restart."""
    first = reg.owner_secret()

    assert first == reg.owner_secret()
    assert len(first) >= 32
    mode = stat.S_IMODE((reg.data / "owner").stat().st_mode)
    assert mode == 0o600, f"owner secret is world-readable: {oct(mode)}"


def test_check_owner_accepts_only_the_secret(reg):
    assert reg.check_owner(reg.owner_secret())
    assert not reg.check_owner("nope")
    assert not reg.check_owner("")


def test_create_makes_a_gated_bare_repo(reg):
    """The gate is installed at creation, not later. A remote graph that exists for even
    one push without it has accepted whatever that push contained."""
    reg.create("trading", admin="seb")

    repo = reg.repo("trading")
    assert (repo / "HEAD").exists(), "not a git repo"
    assert not (repo / ".git").exists(), "not bare"
    hook = repo / "hooks" / "pre-receive"
    assert hook.exists() and os.access(hook, os.X_OK)
    assert "knoten" in hook.read_text(encoding="utf-8")


def test_create_refuses_a_second_graph_of_the_same_name(reg):
    reg.create("trading", admin="seb")

    with pytest.raises(GraphError, match="already exists"):
        reg.create("trading", admin="seb")


@pytest.mark.parametrize("bad", ["../etc", "Trading", "a b", "", "-x", "x/y"])
def test_graph_names_outside_id_re_are_refused_before_touching_disk(reg, bad):
    """A graph name becomes a directory. `../etc` would be created outside the data
    dir; the check has to run before mkdir, not after."""
    with pytest.raises(GraphError, match="not a valid graph name"):
        reg.create(bad, admin="seb")

    assert sorted(p.name for p in (reg.data / "graphs").iterdir()) == []


def test_repo_of_an_unknown_graph_is_an_error_not_a_path(reg):
    with pytest.raises(GraphError, match="no graph 'nope'"):
        reg.repo("nope")
    assert not reg.exists("nope")


def test_create_rolls_back_on_partial_failure(reg, monkeypatch):
    """A half-made repo that exists() calls valid would accept pushes with no gate, forever.
    Partial creation must be rolled back atomically so a retry can succeed."""
    import knoten.registry
    def failing_install(repo):
        raise OSError("disk full")
    monkeypatch.setattr(knoten.registry, "install_server", failing_install)

    with pytest.raises(GraphError, match="could not create"):
        reg.create("trading", admin="seb")

    assert not reg.exists("trading")
    assert sorted(p.name for p in (reg.data / "graphs").iterdir()) == []

    # Restore monkeypatch and retry succeeds
    monkeypatch.undo()
    reg.create("trading", admin="seb")
    assert reg.exists("trading")


# ---------------------------------------------------------------- tokens

def test_a_minted_token_authenticates_with_its_role(reg):
    reg.create("trading", admin="seb")
    tok = reg.mint("trading", "maria", "write")

    assert reg.authenticate("trading", "maria", tok) == "write"


def test_the_wrong_token_the_wrong_user_and_the_wrong_graph_all_fail_closed(reg):
    reg.create("trading", admin="seb")
    tok = reg.mint("trading", "maria", "write")

    assert reg.authenticate("trading", "maria", "not-it") is None
    assert reg.authenticate("trading", "seb", tok) is None          # someone else's token
    assert reg.authenticate("biology", "maria", tok) is None        # no such graph
    assert reg.authenticate("trading", "maria", "") is None
    assert reg.authenticate("trading", "", tok) is None


def test_tokens_are_stored_hashed(reg):
    """A leaked tokens.json must be worthless."""
    reg.create("trading", admin="seb")
    tok = reg.mint("trading", "maria", "write")

    on_disk = (reg.graph_dir("trading") / "tokens.json").read_text(encoding="utf-8")
    assert tok not in on_disk
    assert "maria" in on_disk


def test_create_returns_a_working_admin_token(reg):
    tok = reg.create("trading", admin="seb")

    assert reg.authenticate("trading", "seb", tok) == "admin"


@pytest.mark.parametrize("role", ["owner", "Admin", "", "rw"])
def test_a_role_outside_the_three_is_refused(reg, role):
    reg.create("trading", admin="seb")
    with pytest.raises(GraphError, match="role must be one of"):
        reg.mint("trading", "maria", role)


def test_contributor_names_follow_id_re(reg):
    """The name ends up as a JSON key and, in phase 2, as a filename in graph.yaml."""
    reg.create("trading", admin="seb")
    with pytest.raises(GraphError, match="not a valid contributor name"):
        reg.mint("trading", "Maria Lopez", "write")


def test_revoke_ends_access_and_nothing_else(reg):
    reg.create("trading", admin="seb")
    tok = reg.mint("trading", "maria", "write")

    reg.revoke("trading", "maria")

    assert reg.authenticate("trading", "maria", tok) is None
    assert reg.exists("trading")


def test_revoking_a_stranger_is_an_error(reg):
    reg.create("trading", admin="seb")
    with pytest.raises(GraphError, match="no contributor 'ghost'"):
        reg.revoke("trading", "ghost")


def test_revoking_on_an_unknown_graph_is_a_graph_error(reg):
    """graph_lock opens a file inside the graph directory. Without an existence check
    first, a well-formed name for a graph that does not exist raised a raw
    FileNotFoundError instead of the one-line refusal every other method gives."""
    with pytest.raises(GraphError, match="no graph 'biology'"):
        reg.revoke("biology", "maria")


# ---------------------------------------------------------------- invites

def test_an_invite_redeems_once_into_a_working_token(reg):
    reg.create("trading", admin="seb")
    code = reg.invite("trading", "maria", "write")

    user, role, tok = reg.redeem("trading", code)

    assert (user, role) == ("maria", "write")
    assert reg.authenticate("trading", "maria", tok) == "write"
    with pytest.raises(GraphError, match="not valid"):
        reg.redeem("trading", code)                       # spent


def test_an_expired_invite_is_refused_and_spent(reg):
    """Expired codes are removed when tried, so a stale invites.json does not grow
    forever and a late guess cannot be retried after the clock is fixed."""
    reg.create("trading", admin="seb")
    code = reg.invite("trading", "maria", "write", days=-1)

    with pytest.raises(GraphError, match="expired"):
        reg.redeem("trading", code)
    with pytest.raises(GraphError, match="not valid"):
        reg.redeem("trading", code)


def test_invite_codes_are_stored_hashed(reg):
    reg.create("trading", admin="seb")
    code = reg.invite("trading", "maria", "write")

    assert code not in (reg.graph_dir("trading") / "invites.json").read_text(encoding="utf-8")


def test_a_wrong_code_and_a_wrong_graph_both_fail(reg):
    reg.create("trading", admin="seb")
    reg.invite("trading", "maria", "write")

    with pytest.raises(GraphError, match="not valid"):
        reg.redeem("trading", "guess")
    with pytest.raises(GraphError, match="no graph 'biology'"):
        reg.redeem("biology", "anything")


def test_secrets_that_travel_on_a_command_line_never_start_with_a_dash(reg):
    """`--owner-secret VALUE` and `--invite CODE` are argv. A value beginning with `-`
    is a flag to argparse, and token_urlsafe produced one about one run in five."""
    reg.create("trading", admin="seb")
    for _ in range(64):
        assert not reg.invite("trading", "maria", "write").startswith("-")
    assert not reg.owner_secret().startswith("-")
    assert all(c in "0123456789abcdef" for c in reg.owner_secret())

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

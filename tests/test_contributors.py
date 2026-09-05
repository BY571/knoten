"""contributors.yaml: who may write to a graph, as data beside the rules, machine-owned so
graph.yaml's comments survive every join and revoke."""
import json

import pytest

from conftest import make_key, pub_line
from knoten import core, contributors
from knoten.core import GraphError
from knoten.contributors import (FILE, ROLES, active, admins, check_blob, diff, dump,
                                 invite_blob, keys, load, parse, parse_blob, verify_invite)
from knoten.keys import INVITE_NS, sign

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGxYm3v0R3f7pWJ2cQ5lD8Rk9x6ZQ7pMd2eS8rT1uV2w"


def test_absent_file_is_none_not_an_error(tmp_path):
    """A phase-1 graph has no contributors.yaml, and must keep working unsigned."""
    assert load(tmp_path) is None


def test_parse_accepts_the_documented_shape():
    c = parse(f"seb:\n  key: {KEY}\n  role: admin\nmaria:\n  key: {KEY}\n  role: write\n"
              f"  invited_by: seb\n")
    assert set(c) == {"seb", "maria"}
    assert c["seb"]["role"] == "admin"
    assert c["maria"]["invited_by"] == "seb"


@pytest.mark.parametrize("text, msg", [
    ("seb:\n  role: admin\n", "key"),
    (f"seb:\n  key: {KEY}\n  role: owner\n", "role"),
    (f"Seb:\n  key: {KEY}\n  role: admin\n", "not a valid"),
    (f"seb:\n  key: rsa-not-here\n  role: admin\n", "ssh-ed25519"),
    ("- seb\n", "mapping"),
    ("seb: [1\n", "YAML"),
    (f"seb:\n  key: {KEY}\n  role: admin\n  revoked: soon\n", "YYYY-MM-DD"),
])
def test_parse_refuses_malformed_entries(text, msg):
    with pytest.raises(GraphError, match=msg):
        parse(text)


def test_dump_and_load_round_trip_sorted_and_atomic(tmp_path):
    c = {"maria": {"key": KEY, "role": "write"}, "seb": {"key": KEY, "role": "admin"}}
    dump(tmp_path, c)

    text = (tmp_path / FILE).read_text(encoding="utf-8")
    assert text.index("maria") < text.index("seb")
    assert load(tmp_path) == c
    assert not list(tmp_path.glob(".contributors.yaml.*"))


def test_views_active_keys_admins():
    c = {"seb": {"key": KEY, "role": "admin"},
         "maria": {"key": KEY + "1", "role": "write"},
         "gone": {"key": KEY + "2", "role": "write", "revoked": "2026-09-01"},
         "reader": {"key": KEY + "3", "role": "read"}}
    assert set(active(c)) == {"seb", "maria", "reader"}
    assert keys(c) == {"seb": KEY, "maria": KEY + "1"}          # write and admin, not revoked
    assert admins(c) == {"seb": KEY}


def test_diff_names_what_changed():
    prev = {"seb": {"key": KEY, "role": "admin"}, "old": {"key": KEY, "role": "write"}}
    cur = {"seb": {"key": KEY, "role": "admin", "revoked": "2026-09-05"},
           "new": {"key": KEY, "role": "read"}}
    added, changed, removed = diff(prev, cur)
    assert set(added) == {"new"} and set(changed) == {"seb"} and removed == {"old"}
    assert diff(prev, prev) == ({}, {}, set())
    assert diff(None, cur) == (cur, {}, set())


def test_invite_blob_is_canonical_and_parses_back():
    b = invite_blob("trading", "maria", "write", "2026-09-12", "ab12")
    assert b == b'{"expires":"2026-09-12","graph":"trading","name":"maria","nonce":"ab12","role":"write"}'
    assert parse_blob(b) == json.loads(b)
    with pytest.raises(GraphError, match="invite"):
        parse_blob(b"not json")


def test_check_blob_matches_every_field():
    d = parse_blob(invite_blob("trading", "maria", "write", "2026-09-12", "ab12"))
    check_blob(d, "trading", "maria", "write")
    for graph, name, role in [("biology", "maria", "write"), ("trading", "eve", "write"),
                              ("trading", "maria", "admin")]:
        with pytest.raises(GraphError, match="invite"):
            check_blob(d, graph, name, role)


def test_verify_invite_returns_the_admin_who_signed(keys_dir):
    seb, mallory = make_key(keys_dir, "seb"), make_key(keys_dir, "mallory")
    c = {"seb": {"key": pub_line(seb), "role": "admin"},
         "mallory": {"key": pub_line(mallory), "role": "write"}}
    blob = invite_blob("trading", "maria", "write", "2026-09-12", "ab12")

    assert verify_invite(c, blob, sign(seb, blob, INVITE_NS)) == "seb"
    with pytest.raises(GraphError, match="not signed by an admin"):
        verify_invite(c, blob, sign(mallory, blob, INVITE_NS))          # a writer, not an admin
    with pytest.raises(GraphError, match="not signed by an admin"):
        verify_invite(c, blob + b" ", sign(seb, blob, INVITE_NS))       # tampered blob


def test_a_revoked_admin_cannot_sign_invites(keys_dir):
    seb = make_key(keys_dir, "seb")
    c = {"seb": {"key": pub_line(seb), "role": "admin", "revoked": "2026-09-01"}}
    blob = invite_blob("trading", "maria", "write", "2026-09-12", "ab12")
    with pytest.raises(GraphError, match="not signed by an admin"):
        verify_invite(c, blob, sign(seb, blob, INVITE_NS))


def test_an_unquoted_revoked_date_loads_as_the_string_it_was_written_as(tmp_path):
    """YAML reads `revoked: 2026-09-01` as a date object. Left alone, an entry loaded
    from disk compared unequal to the same entry built in code with today(), so diff()
    reported a change that had not happened, and json.dumps crashed on it."""
    (tmp_path / FILE).write_text(f"seb:\n  key: {KEY}\n  role: admin\n  revoked: 2026-09-01\n",
                                 encoding="utf-8")
    c = load(tmp_path)
    assert c["seb"]["revoked"] == "2026-09-01"
    assert diff(c, {"seb": {"key": KEY, "role": "admin", "revoked": "2026-09-01"}}) == ({}, {}, set())
    dump(tmp_path, c)
    assert load(tmp_path) == c


def test_the_name_cap_is_one_constant():
    """MAX_NAME must be the same in core, contributors, and registry so lengths are
    consistently enforced across the system."""
    assert contributors.MAX_NAME is core.MAX_NAME
    from knoten import registry
    assert registry.MAX_NAME is core.MAX_NAME

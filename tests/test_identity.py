"""identity.py: who may write here, and the keys that prove it."""
import json
import os
import stat

import pytest

from conftest import make_key, pub_line
from knoten import core
from knoten.core import GraphError
from knoten.identity import (FILE, INVITE_NS, ROLES, active, admins, allowed_signers,
                             check_blob, configure_signing, diff, dump, ensure_key,
                             invite_blob, key_dir, keys, load, parse, parse_blob,
                             public_line, sign, verify, verify_invite)

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGxYm3v0R3f7pWJ2cQ5lD8Rk9x6ZQ7pMd2eS8rT1uV2w"
KEY2 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHF3ZaMh1uWn5V0oGZ9rT2pQ8cX6yLdN4wK1sB3jR7t"


def test_absent_file_is_none_not_an_error(tmp_path):
    """A phase-1 graph has no contributors.yaml, and must keep working unsigned."""
    assert load(tmp_path) is None


@pytest.mark.parametrize("text, msg", [
    ("seb:\n  role: admin\n", "key"),
    # `^...$` in ID_RE also matched just before a trailing newline, so this name parsed
    # as valid -- and it goes on to be a filename, a directory and a URL segment.
    (f'"maria\\n":\n  key: {KEY}\n  role: write\n', "not a valid"),
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


def test_views_active_keys_admins():
    c = {"seb": {"key": KEY, "role": "admin"},
         "maria": {"key": KEY + "1", "role": "write"},
         "gone": {"key": KEY + "2", "role": "write", "revoked": "2026-09-01"},
         "reader": {"key": KEY + "3", "role": "read"}}
    assert set(active(c)) == {"seb", "maria", "reader"}
    assert keys(c) == {"seb": KEY, "maria": KEY + "1"}          # write and admin, not revoked
    assert admins(c) == {"seb": KEY}


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
    """YAML reads `revoked: 2026-09-01` as a date object."""
    (tmp_path / FILE).write_text(f"seb:\n  key: {KEY}\n  role: admin\n  revoked: 2026-09-01\n",
                                 encoding="utf-8")
    c = load(tmp_path)
    assert c["seb"]["revoked"] == "2026-09-01"
    assert diff(c, {"seb": {"key": KEY, "role": "admin", "revoked": "2026-09-01"}}) == ({}, {}, set())
    dump(tmp_path, c)
    assert load(tmp_path) == c


def test_two_names_may_not_share_one_key():
    with pytest.raises(GraphError, match="the same key is listed under 'eve' and 'seb'"):
        parse(f"eve:\n  key: {KEY}\n  role: write\nseb:\n  key: {KEY}\n  role: admin\n")


# --------------------------------------------------------------- signing keys

def test_ensure_key_generates_once_and_keeps_it_private(keys_dir):
    a = ensure_key("seb")
    was = a.read_bytes()
    b = ensure_key("seb")
    assert a == b == keys_dir / "seb"
    # The same PATH proves nothing on its own; a regenerated keypair lands at the same
    # path and would refuse every commit the old one signed.
    assert b.read_bytes() == was
    assert a.with_suffix(".pub").exists()
    assert stat.S_IMODE(a.stat().st_mode) == 0o600
    assert stat.S_IMODE(keys_dir.stat().st_mode) == 0o700


def test_ensure_key_refuses_a_name_too_long_to_be_a_filename(keys_dir):
    with pytest.raises(GraphError, match="too long"):
        ensure_key("a" * 65)
    assert not keys_dir.exists() or list(keys_dir.iterdir()) == []


def test_configure_signing_refuses_in_one_line_when_git_config_fails(tmp_path, keypair):
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    with pytest.raises(GraphError, match="could not configure signing"):
        configure_signing(not_a_repo, keypair)


@pytest.mark.parametrize("bad", ["Seb", "a b", "../x", ""])
def test_ensure_key_refuses_a_name_that_is_not_an_id(keys_dir, bad):
    """The name becomes a filename under the key dir."""
    with pytest.raises(GraphError, match="not a valid"):
        ensure_key(bad)
    assert not keys_dir.exists() or list(keys_dir.iterdir()) == []


def test_sign_and_verify_round_trip_in_a_namespace(keypair):
    data = b'{"graph":"trading","name":"maria"}'
    allowed = allowed_signers({"seb": public_line(keypair)})
    sig = sign(keypair, data, INVITE_NS)
    assert "-----BEGIN SSH SIGNATURE-----" in sig
    assert verify(allowed, "seb", data, sig, INVITE_NS)



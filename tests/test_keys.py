"""Held signing keys. A signing key proves who wrote a commit or an invite and grants
access to nothing; it uses the SSH key format only because git verifies that format
natively and ssh-keygen signs arbitrary bytes with it."""
import os
import stat

import pytest

from conftest import make_key, pub_line
from knoten.core import GraphError
from knoten.identity import (INVITE_NS, allowed_signers, configure_signing, ensure_key,
                         key_dir, public_line, sign, verify)


def test_key_dir_honours_the_environment(keys_dir):
    assert key_dir() == keys_dir


def test_ensure_key_generates_once_and_keeps_it_private(keys_dir):
    """A second call must return the SAME key: regenerating would orphan every
    contributors.yaml entry that lists the first one."""
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
    """The name becomes a file under the key dir, exactly as a graph name becomes a
    directory under the data dir. Unbounded, it surfaced NAME_MAX as an opaque OSError."""
    with pytest.raises(GraphError, match="too long"):
        ensure_key("a" * 65)
    assert not keys_dir.exists() or list(keys_dir.iterdir()) == []


def test_configure_signing_refuses_in_one_line_when_git_config_fails(tmp_path, keypair):
    """`check=True` raised CalledProcessError, which is not a GraphError and escaped the
    CLI's handler as a traceback. A repo whose config cannot be written is ordinary
    trouble and owes the user one line."""
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


def test_public_line_is_the_two_fields_git_wants(keypair):
    line = public_line(keypair)
    assert line.startswith("ssh-ed25519 AAAA")
    assert len(line.split()) == 2


def test_sign_and_verify_round_trip_in_a_namespace(keypair):
    data = b'{"graph":"trading","name":"maria"}'
    allowed = allowed_signers({"seb": public_line(keypair)})

    sig = sign(keypair, data, INVITE_NS)

    assert "-----BEGIN SSH SIGNATURE-----" in sig
    assert verify(allowed, "seb", data, sig, INVITE_NS)


def test_verify_fails_on_the_wrong_identity_data_namespace_or_key(keypair, keys_dir):
    """Each of the four things a forger can get wrong, separately."""
    data = b"x"
    other = make_key(keys_dir, "mallory")
    allowed = allowed_signers({"seb": public_line(keypair), "mallory": public_line(other)})
    sig = sign(keypair, data, INVITE_NS)

    assert verify(allowed, "seb", data, sig, INVITE_NS)
    assert not verify(allowed, "mallory", data, sig, INVITE_NS)       # not who signed
    assert not verify(allowed, "seb", b"y", sig, INVITE_NS)           # not what was signed
    assert not verify(allowed, "seb", data, sig, "git")               # a commit signature is not an invite
    assert not verify(allowed_signers({"seb": public_line(other)}), "seb", data, sig, INVITE_NS)


def test_verify_never_raises_on_garbage(keypair):
    allowed = allowed_signers({"seb": public_line(keypair)})
    assert not verify(allowed, "seb", b"x", "not a signature", INVITE_NS)
    assert not verify("", "seb", b"x", sign(keypair, b"x", INVITE_NS), INVITE_NS)


def test_allowed_signers_format_is_what_ssh_keygen_and_git_read(keypair):
    text = allowed_signers({"seb": public_line(keypair)}, namespaces=("git",))
    assert text == f'seb namespaces="git" {public_line(keypair)}\n'


def test_configure_signing_makes_every_commit_in_the_clone_signed(tmp_path, keypair):
    """After this, a plain `git commit` in that clone is signed. Verified by git itself."""
    import subprocess
    repo = tmp_path / "r"
    repo.mkdir()
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", "init", "-q", "-b", "master", str(repo)], check=True, env=env)
    for k, v in (("user.name", "seb"), ("user.email", "s@s")):
        subprocess.run(["git", "-C", str(repo), "config", k, v], check=True, env=env)

    configure_signing(repo, keypair)

    (repo / "a").write_text("a")
    subprocess.run(["git", "-C", str(repo), "add", "a"], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "signed"], check=True, env=env)
    signers = tmp_path / "signers"
    signers.write_text(allowed_signers({"seb": public_line(keypair)}, ("git",)))
    out = subprocess.run(["git", "-C", str(repo), "-c", f"gpg.ssh.allowedSignersFile={signers}",
                          "log", "-1", "--format=%G? %GS"], check=True, env=env,
                         capture_output=True, text=True).stdout.strip()
    assert out == "G seb"

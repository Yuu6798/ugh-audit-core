"""Invite administration never prints tokens or destroys existing files on failure."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import threading
import time

import pytest

from ugh_audit import invites


def registry_user(**changes):
    return {"user_id": "alice", "token_sha256": hashlib.sha256(b"old-token").hexdigest(),
            "enabled": True, **changes}


def test_issue_writes_only_hash_to_registry_and_private_new_token_file(tmp_path, capsys):
    registry, output = tmp_path / "invites.json", tmp_path / "alice.token"
    expiry = int(time.time()) + 3600
    assert invites.main(["issue", "--file", str(registry), "--user", "alice",
                         "--token-out", str(output), "--expires-at", str(expiry)]) == 0
    token = output.read_text().strip()
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", token)
    user, = json.loads(registry.read_text())["users"]
    assert user == {"user_id": "alice", "enabled": True, "expires_at": expiry,
                    "token_sha256": hashlib.sha256(token.encode()).hexdigest()}
    assert token not in registry.read_text()
    captured = capsys.readouterr()
    assert token not in captured.out + captured.err
    assert not registry.with_suffix(".json.lock").exists()
    if os.name != "nt":
        assert stat.S_IMODE(output.stat().st_mode) == 0o600
        assert stat.S_IMODE(registry.stat().st_mode) == 0o600


def test_rotation_preserves_owner_and_revoke_disables_all_keys(tmp_path, monkeypatch):
    from ugh_audit.beta_access import AccessError, authenticate

    registry = tmp_path / "invites.json"
    tokens = []
    for filename, owner in (("first", "alice"), ("second", "alice"), ("third", "bob")):
        output = tmp_path / filename
        invites.issue_invite(registry, owner, output)
        tokens.append(output.read_text().strip())
    monkeypatch.setenv("UGH_ACCESS_MODE", "invite")
    monkeypatch.setenv("UGH_INVITE_FILE", str(registry))
    for token in tokens[:2]:
        assert authenticate([(b"authorization", f"Bearer {token}".encode())]).owner_id == "alice"
    assert invites.revoke_invites(registry, "alice") == 2
    users = json.loads(registry.read_text())["users"]
    assert [user["enabled"] for user in users] == [False, False, True]
    for token in tokens[:2]:
        with pytest.raises(AccessError, match="invalid_credential"):
            authenticate([(b"authorization", f"Bearer {token}".encode())])
    assert authenticate([(b"authorization", f"Bearer {tokens[2]}".encode())]).owner_id == "bob"
    assert invites.revoke_invites(registry, "alice") == 2  # Idempotent state.


@pytest.mark.parametrize("user", ["", "-alice", "a b", "a/b", "日本語", "a" * 65, 3])
def test_invalid_user_does_not_create_files(tmp_path, user):
    with pytest.raises(invites.InviteError):
        invites.issue_invite(tmp_path / "registry", user, tmp_path / "token")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("expiry", [0, -1, True, 1.5, "tomorrow", 1])
def test_invalid_expiry_does_not_create_files(tmp_path, expiry):
    with pytest.raises(invites.InviteError, match="Expiry"):
        invites.issue_invite(tmp_path / "registry", "alice", tmp_path / "token", expiry)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("content", [
    "broken", "null", "[]", '{"users":[]}', '{"users":[],"users":[]}',
    json.dumps({"users": [registry_user(user_id=[])]}),
    json.dumps({"users": [registry_user(enabled="true")]}),
    json.dumps({"users": [registry_user(expires_at=True)]}),
    json.dumps({"users": [registry_user(token_sha256="invalid")]}),
    json.dumps({"users": [registry_user(), registry_user()]}),
    json.dumps({"users": [registry_user(token="plaintext-not-permitted")]}),
    " " * 262145,
])
def test_malformed_registry_is_never_overwritten(tmp_path, content):
    registry, output = tmp_path / "registry", tmp_path / "token"
    registry.write_text(content)
    with pytest.raises(invites.InviteError, match="Invalid invite registry"):
        invites.issue_invite(registry, "alice", output)
    assert registry.read_text() == content
    assert not output.exists()
    assert list(tmp_path.iterdir()) == [registry]


def test_existing_token_file_is_never_overwritten_or_deleted(tmp_path):
    registry, output = tmp_path / "registry", tmp_path / "token"
    output.write_text("important-existing-file")
    with pytest.raises(FileExistsError):
        invites.issue_invite(registry, "alice", output)
    assert output.read_text() == "important-existing-file"
    assert not registry.exists()
    assert list(tmp_path.iterdir()) == [output]


@pytest.mark.parametrize("target", ["directory", "missing-parent", "registry", "registry.lock"])
def test_invalid_token_destination_has_no_side_effects(tmp_path, target):
    registry = tmp_path / "registry"
    directory = tmp_path / "directory"
    directory.mkdir()
    output = (tmp_path / "missing" / "token") if target == "missing-parent" else tmp_path / target
    with pytest.raises(invites.InviteError):
        invites.issue_invite(registry, "alice", output)
    assert list(tmp_path.iterdir()) == [directory]


def test_failed_atomic_replace_preserves_registry_and_removes_only_new_token(tmp_path, monkeypatch):
    registry, output = tmp_path / "registry", tmp_path / "token"
    original = json.dumps({"users": [registry_user()]})
    registry.write_text(original)

    def fail_replace(*args):
        raise OSError("simulated write failure")

    monkeypatch.setattr(invites.os, "replace", fail_replace)
    with pytest.raises(OSError):
        invites.issue_invite(registry, "bob", output)
    assert registry.read_text() == original
    assert list(tmp_path.iterdir()) == [registry]


def test_failed_token_write_removes_new_output_and_lock(tmp_path, monkeypatch):
    def fail_fsync(*args):
        raise OSError("simulated flush failure")

    monkeypatch.setattr(invites.os, "fsync", fail_fsync)
    with pytest.raises(OSError):
        invites.issue_invite(tmp_path / "registry", "alice", tmp_path / "token")
    assert list(tmp_path.iterdir()) == []


def test_duplicate_generated_credential_is_rejected_without_new_output(tmp_path, monkeypatch):
    registry = tmp_path / "registry"
    invites.issue_invite(registry, "alice", tmp_path / "alice")
    token = (tmp_path / "alice").read_text().strip()
    original = registry.read_text()
    monkeypatch.setattr(invites.secrets, "token_urlsafe", lambda size: token)
    with pytest.raises(invites.InviteError, match="collision"):
        invites.issue_invite(registry, "bob", tmp_path / "bob")
    assert registry.read_text() == original
    assert not (tmp_path / "bob").exists()


def test_full_registry_refuses_new_credential_without_orphan_token(tmp_path):
    registry = tmp_path / "registry"
    users = [registry_user(token_sha256=hashlib.sha256(str(index).encode()).hexdigest())
             for index in range(1000)]
    original = json.dumps({"users": users})
    registry.write_text(original)
    with pytest.raises(invites.InviteError, match="capacity exceeded"):
        invites.issue_invite(registry, "bob", tmp_path / "bob")
    assert registry.read_text() == original
    assert list(tmp_path.iterdir()) == [registry]


def test_preexisting_lock_is_never_removed_automatically(tmp_path):
    lock = tmp_path / "registry.lock"
    lock.write_text("stale-or-active")
    with pytest.raises(invites.InviteError, match="stale locks are never removed"):
        invites.issue_invite(tmp_path / "registry", "alice", tmp_path / "token")
    assert lock.read_text() == "stale-or-active"
    assert list(tmp_path.iterdir()) == [lock]


def test_concurrent_update_fails_without_losing_the_first_credential(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    read_registry = invites._registry

    def pause_after_lock(path):
        entered.set()
        assert release.wait(10)
        return read_registry(path)

    monkeypatch.setattr(invites, "_registry", pause_after_lock)
    registry = tmp_path / "registry"
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(invites.issue_invite, registry, "alice", tmp_path / "alice")
        try:
            assert entered.wait(10)
            with pytest.raises(invites.InviteError, match="Registry is locked"):
                invites.issue_invite(registry, "bob", tmp_path / "bob")
        finally:
            release.set()
        pending.result()
    assert [user["user_id"] for user in json.loads(registry.read_text())["users"]] == ["alice"]
    assert not (tmp_path / "bob").exists()


def test_missing_revoke_user_leaves_registry_unchanged(tmp_path):
    registry = tmp_path / "registry"
    with pytest.raises(invites.InviteError, match="no registered credentials"):
        invites.revoke_invites(registry, "alice")
    assert list(tmp_path.iterdir()) == []


def test_cli_module_requires_token_output_and_never_echoes_issued_token(tmp_path):
    registry, output = tmp_path / "registry", tmp_path / "token"
    command = [sys.executable, "-m", "ugh_audit.invites", "issue", "--file", str(registry),
               "--user", "alice"]
    missing = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert missing.returncode == 2
    assert not registry.exists()
    completed = subprocess.run(command + ["--token-out", str(output)], capture_output=True,
                               text=True, timeout=30)
    assert completed.returncode == 0
    assert output.read_text().strip() not in completed.stdout + completed.stderr
    revoked = subprocess.run([sys.executable, "-m", "ugh_audit.invites", "revoke",
                              "--file", str(registry), "--user", "alice"],
                             capture_output=True, text=True, timeout=30)
    assert revoked.returncode == 0
    assert json.loads(registry.read_text())["users"][0]["enabled"] is False

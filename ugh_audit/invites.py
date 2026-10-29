"""Local invite administration; plaintext credentials are written only to a new file.

Registry updates use an exclusive ``<registry>.lock`` file. After a crashed
operation, verify that no administrator process is active before manually
removing that lock and inspecting the registry/token output. Locks are never
removed automatically as stale. On Windows, restrict directory ACLs separately;
the POSIX 0600 mode does not establish a Windows access-control policy.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import time


class InviteError(Exception):
    """An administration operation could not be safely completed."""


def _require_file_managed_registry() -> None:
    if "UGH_INVITES_JSON" in os.environ:
        raise InviteError(
            "Registry is managed by UGH_INVITES_JSON; update the environment registry and redeploy."
        )


def _user_id(value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate registry field")
        result[key] = value
    return result


def _registry(path: Path) -> dict:
    if not path.exists():
        return {"users": []}
    try:
        with path.open("rb") as stream:
            raw = stream.read(262145)
        if len(raw) > 262144:
            raise ValueError("Registry too large")
        config = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(config, dict) or set(config) != {"users"}:
            raise ValueError("Invalid root")
        users = config["users"]
        if not isinstance(users, list) or not users or len(users) > 1000:
            raise ValueError("Invalid users")
        digests = set()
        for user in users:
            if (not isinstance(user, dict)
                    or set(user) - {"user_id", "token_sha256", "enabled", "expires_at"}
                    or not _user_id(user.get("user_id"))):
                raise ValueError("Invalid user")
            digest = user.get("token_sha256")
            expires = user.get("expires_at")
            if (not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)
                    or digest in digests or type(user.get("enabled", True)) is not bool
                    or (expires is not None and (type(expires) is not int or expires <= 0))):
                raise ValueError("Invalid credential")
            digests.add(digest)
        return config
    except (ValueError, TypeError, RecursionError):
        raise InviteError("Invalid invite registry; left unchanged.") from None


def _path(value: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_symlink():
        raise InviteError("Symbolic-link file destinations are not supported.")
    path = path.resolve()
    if not path.parent.is_dir() or path.is_dir():
        raise InviteError("Destination must be a file in an existing directory.")
    return path


@contextmanager
def _locked(path: Path):
    lock = path.with_name(path.name + ".lock")
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise InviteError(
            "Registry is locked. Verify no administrator process is active before "
            "manually removing the .lock file; stale locks are never removed automatically."
        ) from None
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write(f"{os.getpid()}\n")
        yield
    finally:
        lock.unlink()


def _replace_registry(path: Path, config: dict) -> None:
    payload = (json.dumps(config, ensure_ascii=True, indent=2) + "\n").encode("utf-8")
    if len(config["users"]) > 1000 or len(payload) > 262144:
        raise InviteError("Registry capacity exceeded; left unchanged.")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def issue_invite(
    registry_file: Path, user_id: str, token_out: Path, expires_at: int | None = None,
) -> None:
    """Append a credential; existing credentials for the same owner remain active."""
    _require_file_managed_registry()
    if not _user_id(user_id):
        raise InviteError(
            "User ID must start with an ASCII letter/digit and use 1-64 letters/digits/_/-."
        )
    if expires_at is not None and (type(expires_at) is not int or expires_at <= time.time()):
        raise InviteError("Expiry must be a future Unix timestamp in whole seconds.")
    registry, destination = _path(registry_file), _path(token_out)
    if destination in (registry, registry.with_name(registry.name + ".lock")):
        raise InviteError("Token output must differ from registry and lock paths.")
    with _locked(registry):
        config = _registry(registry)
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        if any(user["token_sha256"] == digest for user in config["users"]):
            raise InviteError("Generated credential collision; retry issuance.")
        user = {"user_id": user_id, "token_sha256": digest, "enabled": True}
        if expires_at is not None:
            user["expires_at"] = expires_at
        config["users"].append(user)
        created = None
        committed = False
        try:
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created = os.fstat(descriptor)
            with os.fdopen(descriptor, "w", encoding="ascii", newline="\n") as stream:
                stream.write(token + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            _replace_registry(registry, config)
            committed = True
        finally:
            if created is not None and not committed:
                try:
                    current = destination.stat(follow_symlinks=False)
                    if (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino):
                        destination.unlink()
                except FileNotFoundError:
                    pass


def revoke_invites(registry_file: Path, user_id: str) -> int:
    """Disable every credential belonging to one owner; keep history ownership stable."""
    _require_file_managed_registry()
    if not _user_id(user_id):
        raise InviteError("Invalid user ID.")
    registry = _path(registry_file)
    with _locked(registry):
        config = _registry(registry)
        matched = [user for user in config["users"] if user["user_id"] == user_id]
        if not matched:
            raise InviteError("User has no registered credentials; registry left unchanged.")
        for user in matched:
            user["enabled"] = False
        _replace_registry(registry, config)
        return len(matched)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    issue = commands.add_parser("issue", help="Issue a new invite into a new private token file")
    revoke = commands.add_parser("revoke", help="Revoke every key for one user")
    for command in (issue, revoke):
        command.add_argument("--file", type=Path, required=True, help="Invite registry JSON path")
        command.add_argument("--user", required=True, help="Stable owner ID")
    issue.add_argument("--token-out", type=Path, required=True)
    issue.add_argument("--expires-at", type=int, help="Future Unix timestamp (seconds)")
    args = parser.parse_args(argv)
    try:
        if args.command == "issue":
            issue_invite(args.file, args.user, args.token_out, args.expires_at)
            print("Invite issued. Credential saved only to the requested token output file.")
        else:
            count = revoke_invites(args.file, args.user)
            print(f"Revoked {count} credential(s).")
        return 0
    except InviteError as exc:
        parser.exit(1, f"Invite operation failed: {exc}\n")
    except OSError:
        parser.exit(1, "Invite operation failed: filesystem access failed; check paths and permissions.\n")


if __name__ == "__main__":
    raise SystemExit(main())

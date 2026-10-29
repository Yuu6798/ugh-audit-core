"""Fail-closed deployment preparation before starting the HTTP server.

Only hashes belong in UGH_INVITES_JSON. No configuration contents or database
rows are logged. Existing databases are never silently replaced or restored.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import time
from typing import Mapping
import uuid

from . import invites
from .storage.audit_db import AuditDB, DEFAULT_DB_PATH
from .usage_limits import Limits, UsageLimiter, UsageUnavailable
from .retention_supervisor import service_lock, state_path, supervise, SupervisorError


class DeploymentError(Exception):
    """A fixed public code, never a private configuration or database value."""


def _is_mount(path: Path) -> bool:
    if os.path.ismount(path):
        return True
    # ismount does not identify every bind mount on Linux.
    try:
        lines = Path("/proc/self/mountinfo").read_text().splitlines()
    except OSError:
        return False
    for line in lines:
        fields = line.split()
        if len(fields) > 4:
            mount = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), fields[4])
            if Path(mount) == path:
                return True
    return False


def _file_path(value: str | Path, mount: Path | None) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() or path.is_symlink():
        raise DeploymentError("invalid_deployment_path")
    path = path.resolve()
    if not path.parent.is_dir() or (path.exists() and not path.is_file()):
        raise DeploymentError("invalid_deployment_path")
    if mount is not None and (path == mount or not path.is_relative_to(mount)):
        raise DeploymentError("path_outside_persistent_volume")
    return path


def _mount(env: Mapping[str, str]) -> Path | None:
    railway = any(name in env for name in (
        "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID", "RAILWAY_ENVIRONMENT_ID",
        "RAILWAY_VOLUME_MOUNT_PATH", "RAILWAY_DEPLOYMENT_ID", "RAILWAY_PUBLIC_DOMAIN",
    ))
    if not railway:
        return None
    raw = env.get("RAILWAY_VOLUME_MOUNT_PATH")
    if not raw:
        raise DeploymentError("persistent_volume_required")
    path = Path(raw)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise DeploymentError("persistent_volume_required")
    path = path.resolve()
    if path == Path(path.anchor) or not _is_mount(path):
        raise DeploymentError("persistent_volume_not_mounted")
    return path


@contextmanager
def _startup_lock(path: Path):
    """OS locks are released after a crash; no stale lock removal is needed."""
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def _validated_registry(raw: str, directory: Path) -> dict:
    payload = raw.encode("utf-8")
    if len(payload) > 262144:
        raise DeploymentError("invite_configuration_invalid")
    descriptor, name = tempfile.mkstemp(prefix=".invite-validation-", dir=directory)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
        # Reuse the administration validator, whose schema matches HTTP access.
        config = invites._registry(temporary)
        canonical = (json.dumps(config, ensure_ascii=True, indent=2) + "\n").encode("utf-8")
        if len(canonical) > 262144:
            raise DeploymentError("invite_configuration_invalid")
        return config
    except (invites.InviteError, UnicodeError):
        raise DeploymentError("invite_configuration_invalid") from None
    finally:
        temporary.unlink(missing_ok=True)


def _readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)


def _integrity(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise DeploymentError("database_integrity_failed")


def _schema(connection: sqlite3.Connection) -> dict:
    tables = [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]
    return {table: {
        "columns": [list(row[1:]) for row in connection.execute(
            'PRAGMA table_info("' + table.replace('"', '""') + '")')],
        "indexes": sorted(row[1] for row in connection.execute(
            'PRAGMA index_list("' + table.replace('"', '""') + '")') if not row[1].startswith("sqlite_")),
    } for table in tables}


def _schema_compatible(actual: dict, target: dict, *, legacy_audit: bool = False) -> bool:
    for table, expected in target.items():
        if table not in actual:
            return False
        columns = {row[0]: row for row in actual[table]["columns"]}
        for row in expected["columns"]:
            old = columns.get(row[0])
            # AuditDB adds these two v0.2 columns with nullable ALTER TABLE
            # definitions, unlike its fresh CREATE TABLE. Accept only those
            # supported variants when explicitly inspecting an audit database.
            # Usage ledgers and every other column retain strict nullability.
            if old is None or [old[1].upper(), old[4]] != [row[1].upper(), row[4]]:
                return False
            legacy_nullable = (legacy_audit and table == "audit_runs"
                               and row[0] in {"verdict", "hit_rate"}
                               and old[2] == 0 and row[2] == 1)
            if old[2] != row[2] and not legacy_nullable:
                return False
        if not set(expected["indexes"]).issubset(actual[table]["indexes"]):
            return False
    return True


def _copy_database(source: Path, destination: Path) -> None:
    # Exclusive creation prevents overwriting an earlier backup or another file.
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    started = time.monotonic()

    def progress(status, remaining, total):
        if time.monotonic() - started > 60:
            raise DeploymentError("database_backup_timeout")

    try:
        with closing(_readonly(source)) as old, closing(sqlite3.connect(destination)) as new:
            old.backup(new, pages=256, progress=progress, sleep=0.05)
            _integrity(new)
        with destination.open("r+b") as stream:
            os.fsync(stream.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def _rows_preserved(original: Path, migrated: Path) -> int:
    """Compare all original fields, including BLOBs, without emitting their values."""
    with closing(_readonly(original)) as source, closing(_readonly(migrated)) as current:
        columns = [row[1] for row in source.execute("PRAGMA table_info(audit_runs)")]
        if not columns:
            raise DeploymentError("audit_database_schema_invalid")
        quoted = ", ".join('"' + name.replace('"', '""') + '"' for name in columns)
        original_rows = source.execute(f"SELECT {quoted} FROM audit_runs ORDER BY id")
        current_rows = current.execute(f"SELECT {quoted} FROM audit_runs ORDER BY id")
        count = 0
        while True:
            previous = original_rows.fetchone()
            candidate = current_rows.fetchone()
            if previous is None and candidate is None:
                break
            if previous != candidate:
                raise DeploymentError("audit_rows_changed_during_migration")
            count += 1
        if "owner_id" not in columns and current.execute(
            "SELECT COUNT(*) FROM audit_runs WHERE owner_id IS NOT NULL"
        ).fetchone()[0]:
            raise DeploymentError("legacy_owner_assignment_detected")
        return count


def _limits(env: Mapping[str, str]) -> Limits:
    return Limits(**{name: int(env.get(variable, default)) for name, variable, default in (
        ("requests_per_minute", "UGH_REQUESTS_PER_MINUTE", "60"),
        ("audits_per_day", "UGH_AUDITS_PER_DAY", "100"),
        ("global_audits_per_day", "UGH_GLOBAL_AUDITS_PER_DAY", "500"),
        ("concurrent_per_user", "UGH_CONCURRENT_PER_USER", "1"),
        ("concurrent_global", "UGH_CONCURRENT_GLOBAL", "4"),
    )})


def _prepare(env: Mapping[str, str]) -> dict:
    mount = _mount(env)
    mode = env.get("UGH_ACCESS_MODE", "invite")
    if mode not in ("invite", "local") or (mount is not None and mode != "invite"):
        raise DeploymentError("invalid_access_mode")
    allow_new = env.get("UGH_ALLOW_NEW_AUDIT_DB", "0")
    if allow_new not in ("0", "1"):
        raise DeploymentError("invalid_database_initialization_option")
    limits = _limits(env)
    audit = _file_path(env.get("UGH_AUDIT_DB", str(DEFAULT_DB_PATH)), mount)
    usage = _file_path(env.get("UGH_USAGE_DB", str(audit.with_name("usage.db"))), mount)
    registry = _file_path(env.get("UGH_INVITE_FILE", str(audit.with_name("invites.json"))), mount)
    lock = _file_path(audit.with_name(audit.name + ".deployment.lock"), mount)
    database_paths = [{path, *(Path(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal"))}
                      for path in (audit, usage)]
    registry_lock = registry.with_name(registry.name + ".lock")
    runtime_lock = _file_path(Path(str(audit) + ".supervisor.lock"), mount)
    retention_state = _file_path(state_path(audit), mount)
    reserved = {registry, registry_lock, lock, runtime_lock, retention_state}
    backup_directory = audit.parent / "deployment-backups"
    if (len(reserved) != 5 or database_paths[0] & database_paths[1]
            or reserved & set.union(*database_paths)
            or any(path.is_relative_to(backup_directory) for path in (audit, usage, registry, lock))):
        raise DeploymentError("deployment_paths_overlap")
    existing = [path for path in (audit, usage, registry, lock) if path.exists()]
    if any(left.samefile(right) for pos, left in enumerate(existing) for right in existing[pos + 1:]):
        raise DeploymentError("deployment_paths_overlap")
    if not audit.exists() and allow_new != "1":
        raise DeploymentError("existing_audit_database_required")
    registry_config = None
    if "UGH_INVITES_JSON" in env:
        registry_config = _validated_registry(env["UGH_INVITES_JSON"], registry.parent)
    elif mode == "invite":
        if not registry.is_file():
            raise DeploymentError("invite_configuration_required")
        registry_config = invites._registry(registry)

    with _startup_lock(lock):
        if backup_directory.is_symlink():
            raise DeploymentError("invalid_backup_directory")
        backup_directory.mkdir(mode=0o700, exist_ok=True)
        backups = []
        with tempfile.TemporaryDirectory(prefix=".deployment-check-", dir=backup_directory) as scratch:
            scratch = Path(scratch)
            template_audit, template_usage = scratch / "audit.sqlite", scratch / "usage.sqlite"
            AuditDB(template_audit)
            UsageLimiter(template_usage, limits)
            with closing(_readonly(template_audit)) as conn:
                target_audit = _schema(conn)
            with closing(_readonly(template_usage)) as conn:
                target_usage = _schema(conn)
            target_id = hashlib.sha256(json.dumps(target_audit, sort_keys=True).encode()).hexdigest()[:12]

            # Validate all persistent inputs before applying registry or migrations.
            existing_audit = audit.exists()
            migration = not existing_audit
            row_count = 0
            if existing_audit:
                with closing(_readonly(audit)) as conn:
                    _integrity(conn)
                    actual = _schema(conn)
                    if "audit_runs" not in actual:
                        raise DeploymentError("audit_database_schema_invalid")
                    row_count = conn.execute("SELECT COUNT(*) FROM audit_runs").fetchone()[0]
                    migration = not _schema_compatible(actual, target_audit, legacy_audit=True)
                    if not migration and conn.execute("""
                        SELECT 1 FROM audit_runs WHERE verdict = '' OR
                        (quality_score = 5 AND delta_e != 0
                         AND MAX(1.0, MIN(5.0, 5.0 - 4.0 * delta_e)) != quality_score)
                        LIMIT 1
                    """).fetchone():
                        raise DeploymentError("audit_backfill_requires_review")
            if usage.exists():
                with closing(_readonly(usage)) as conn:
                    _integrity(conn)
                    if not _schema_compatible(_schema(conn), target_usage):
                        raise DeploymentError("usage_database_schema_unsupported")
                    clock = conn.execute("SELECT last_seen FROM beta_usage_clock WHERE id=1").fetchone()
                    if (clock is None or type(clock[0]) not in (int, float)
                            or not math.isfinite(clock[0]) or not 0 <= clock[0] <= 253402300799):
                        raise DeploymentError("usage_database_schema_unsupported")

            if existing_audit and migration:
                backup = backup_directory / f"audit-before-{target_id}-{uuid.uuid4().hex}.sqlite"
                backup_started = datetime.now(timezone.utc)
                _copy_database(audit, backup)
                from .managed_files import register_managed_backup
                register_managed_backup(backup, now=backup_started)
                backups.append(str(backup))
                print(json.dumps({"event": "deployment_backup_created", "path": str(backup),
                                  "integrity": "ok", "audit_rows": row_count}), flush=True)
                candidate = scratch / "candidate.sqlite"
                _copy_database(backup, candidate)
                AuditDB(candidate)
                with closing(_readonly(candidate)) as conn:
                    _integrity(conn)
                    if not _schema_compatible(_schema(conn), target_audit, legacy_audit=True):
                        raise DeploymentError("audit_database_schema_unsupported")
                row_count = _rows_preserved(backup, candidate)
            if migration:
                AuditDB(audit)
                if not existing_audit:
                    audit.chmod(0o600)
                if existing_audit:
                    _rows_preserved(backup, audit)
            with closing(_readonly(audit)) as conn:
                _integrity(conn)
                row_count = conn.execute("SELECT COUNT(*) FROM audit_runs").fetchone()[0]
            usage_existed = usage.exists()
            UsageLimiter(usage, limits)
            if not usage_existed:
                usage.chmod(0o600)
            if "UGH_INVITES_JSON" in env:
                with invites._locked(registry):
                    invites._replace_registry(registry, registry_config)

        return {
            "event": "deployment_prepared", "audit_database": str(audit),
            "usage_database": str(usage), "invite_file": str(registry),
            "volume_verified": mount is not None, "integrity": "ok", "audit_rows": row_count,
            "audit_schema": target_id, "audit_migration": "initialized" if not existing_audit else (
                "migrated" if migration else "unchanged"), "backups": backups,
            "usage_state": "preserved" if usage_existed else "initialized",
            "invite_source": "environment" if "UGH_INVITES_JSON" in env else "file",
        }


def prepare_deployment(environ: Mapping[str, str] | None = None) -> dict:
    """Prepare an existing deployment; return only a nonsecret receipt."""
    from .managed_files import ManagedFilesError

    try:
        return _prepare(os.environ if environ is None else environ)
    except DeploymentError:
        raise
    except invites.InviteError:
        raise DeploymentError("invite_configuration_invalid") from None
    except ManagedFilesError:
        raise DeploymentError("managed_backup_registration_failed") from None
    except (OSError, sqlite3.Error, ValueError, TypeError, UsageUnavailable):
        raise DeploymentError("deployment_preparation_failed") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["start"])
    parser.parse_args(argv)
    try:
        port = int(os.environ.get("PORT", "8000"))
        if not 1 <= port <= 65535:
            raise DeploymentError("invalid_port")
        automatic = os.environ.get("UGH_RETENTION_AUTOMATION", "0")
        if automatic not in ("0", "1"):
            raise DeploymentError("invalid_retention_automation_option")
        if automatic == "1" and os.name != "posix":
            raise DeploymentError("retention_automation_requires_posix")
        audit = _file_path(os.environ.get("UGH_AUDIT_DB", str(DEFAULT_DB_PATH)), _mount(os.environ))
        with service_lock(audit) as descriptor:
            receipt = prepare_deployment()
            os.environ["UGH_AUDIT_DB"] = receipt["audit_database"]
            os.environ["UGH_USAGE_DB"] = receipt["usage_database"]
            os.environ["UGH_INVITE_FILE"] = receipt["invite_file"]
            print(json.dumps(receipt, sort_keys=True), flush=True)
            command = [sys.executable, "-m", "uvicorn", "ugh_audit.server:app",
                       "--host", "0.0.0.0", "--port", str(port)]
            if automatic == "1":
                return supervise([*command, "--workers", "1"], dict(os.environ), descriptor)
            os.execvp(sys.executable, command)
    except (DeploymentError, SupervisorError, ValueError, OSError) as exc:
        code = str(exc) if isinstance(exc, (DeploymentError, SupervisorError)) else "deployment_start_failed"
        print(json.dumps({"event": "deployment_failed", "code": code}), file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

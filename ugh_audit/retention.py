"""Offline, explicitly scoped audit deletion. Never starts a service or calls a model.

Stop the service and pause deploys before --apply. SQLite locks cannot prove that
an idle HTTP process is stopped. Backups and invites are separate operator
responsibilities. The optional usage mode deletes expired records only.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
from typing import Optional, Sequence


class RetentionError(Exception):
    """Fixed error codes only: never include row content or credentials."""

    def __init__(self, code: str, *, deletion_committed: bool = False):
        super().__init__(code)
        self.deletion_committed = deletion_committed


def _path(value: Path) -> Path:
    path = Path(value)
    if (not path.is_absolute() or any(part.is_symlink() for part in (path, *path.parents))
            or not path.is_file() or path.stat().st_nlink != 1):
        raise RetentionError("existing_regular_database_required")
    path = path.resolve(strict=True)
    for suffix in ("-wal", "-shm", "-journal", ".deployment.lock"):
        sidecar = Path(str(path) + suffix)
        if sidecar.is_symlink() or (sidecar.exists() and (
                not sidecar.is_file() or sidecar.stat().st_nlink != 1)):
            raise RetentionError("invalid_database_sidecar")
    return path


@contextmanager
def _deployment_lock(path: Path):
    # The same lock used by deployment.prepare_deployment; OS releases on crash.
    descriptor = os.open(str(path) + ".deployment.lock", os.O_RDWR | os.O_CREAT, 0o600)
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


def _validate(conn: sqlite3.Connection) -> None:
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    if tables != [("audit_runs",)]:
        raise RetentionError("unsupported_audit_schema")
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger', 'view')").fetchone():
        raise RetentionError("unsupported_audit_schema")
    columns = {row[1]: row for row in conn.execute("PRAGMA table_info(audit_runs)")}
    required = {"id": "INTEGER", "owner_id": "TEXT", "created_at": "TEXT",
                "retry_of": "INTEGER", "question": "TEXT", "response": "TEXT",
                "session_id": "TEXT"}
    if (any(name not in columns or columns[name][2].upper() != kind
            for name, kind in required.items())
            or not _rowid_primary_key(conn, "audit_runs", "id")
            or conn.execute("PRAGMA foreign_key_list(audit_runs)").fetchone()):
        raise RetentionError("unsupported_audit_schema")
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='audit_runs'").fetchone()[0]
    # Strip quoted values/identifiers and comments before checking the actual
    # AUTOINCREMENT keyword, which preserves the never-reuse-ID contract.
    unquoted = re.sub(r"'(''|[^'])*'|\"(\"\"|[^\"])*\"|`(``|[^`])*`|\[[^]]*\]"
                      r"|/\*.*?\*/|--[^\n]*", " ", sql, flags=re.DOTALL)
    if (not re.search(r"\bAUTOINCREMENT\b", unquoted, re.IGNORECASE)
            or conn.execute("SELECT 1 FROM audit_runs WHERE id IS NULL OR typeof(id)!='integer' "
                            "LIMIT 1").fetchone()
            or conn.execute("SELECT 1 FROM audit_runs GROUP BY id HAVING COUNT(*)>1 LIMIT 1").fetchone()):
        raise RetentionError("unsupported_audit_schema")
    if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise RetentionError("audit_integrity_failed")


def _rowid_primary_key(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Require a sole INTEGER rowid alias, not composite or nullable indexed PKs."""
    columns = list(conn.execute(f'PRAGMA table_info("{table}")'))
    primary = [(row[1], row[2].upper(), row[5]) for row in columns if row[5]]
    # WITHOUT ROWID and INTEGER PRIMARY KEY DESC use a separate PK index.
    # A genuine INTEGER PRIMARY KEY rowid alias has no PK index and cannot
    # contain duplicate or NULL IDs. Shadow rowid names are not supported.
    return (primary == [(column, "INTEGER", 1)]
            and not any(row[1].lower() in {"rowid", "_rowid_", "oid"} for row in columns)
            and not any(row[3] == "pk" for row in conn.execute(f'PRAGMA index_list("{table}")')))


def _timestamp(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.utcoffset() is None:
            raise ValueError
        return result.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError, OverflowError):
        raise RetentionError("invalid_owned_audit_timestamp") from None


def _selection(
    conn: sqlite3.Connection, owner: Optional[str], cutoff: Optional[datetime],
    audit_ids: Optional[tuple[int, ...]] = None,
) -> list:
    if audit_ids is not None:
        rows = []
        missing = []
        # Indexed lookups avoid SQLite's bound-parameter limit. All lookups run
        # within the same read snapshot or exclusive deletion transaction.
        for audit_id in audit_ids:
            row = conn.execute(
                "SELECT id, created_at, owner_id FROM audit_runs WHERE id=?", (audit_id,),
            ).fetchone()
            if row is None:
                missing.append(audit_id)
            elif row[2] != owner:
                # Never silently skip a foreign/legacy ID and delete the rest.
                raise RetentionError("audit_id_owner_mismatch")
            else:
                rows.append(row[:2])
        if missing:
            sequence = conn.execute(
                "SELECT seq FROM sqlite_sequence WHERE name='audit_runs'",
            ).fetchall()
            if (len(sequence) > 1 or (sequence and (
                    type(sequence[0][0]) is not int or sequence[0][0] < 0))):
                raise RetentionError("unsupported_audit_schema")
            if not sequence or max(missing) > sequence[0][0]:
                # Do not register a future ID that could erase a later audit on
                # replay. This boundary is not evidence of a missing ID's owner.
                raise RetentionError("audit_id_not_previously_allocated")
    elif owner is not None:
        rows = conn.execute(
            "SELECT id, created_at FROM audit_runs WHERE owner_id=?", (owner,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, created_at FROM audit_runs WHERE owner_id IS NOT NULL",
        ).fetchall()
    # Reject ambiguous dates instead of silently claiming retention is complete.
    selected = []
    for row_id, value in rows:
        date = _timestamp(value)
        if cutoff is None or date <= cutoff:
            selected.append(row_id)
    return selected


def _begin_maintenance(conn: sqlite3.Connection) -> str:
    conn.execute("PRAGMA locking_mode=EXCLUSIVE")
    conn.execute("PRAGMA secure_delete=ON")
    conn.execute("PRAGMA temp_store=MEMORY")
    journal = conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
    if journal not in {"delete", "wal"}:
        raise RetentionError("unsupported_journal_mode")
    # A pinned WAL reader must cause failure before deleting anything.
    if journal == "wal" and conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]:
        raise RetentionError("audit_database_busy")
    conn.execute("BEGIN EXCLUSIVE")
    return journal


def _compact(conn: sqlite3.Connection, journal: str) -> None:
    conn.execute("VACUUM")
    if journal == "wal" and conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]:
        raise RetentionError("post_delete_cleanup_incomplete", deletion_committed=True)


def maintain_audits(
    db: Path, *, owner: Optional[str] = None, older_than_days: Optional[int] = None,
    audit_ids: Optional[Sequence[int]] = None,
    apply: bool = False, service_stopped: bool = False, now: Optional[datetime] = None,
) -> dict:
    """Preview or delete owned audits; retain NULL-owner rows and quota state.

    --apply compacts the active SQLite file and truncates WAL. It is not a secure
    erase guarantee for storage hardware, snapshots or independently held copies.
    A failure with deletion_committed=true requires cleanup before restarting.
    audit_ids narrows an owner's scope; it must be a nonempty sequence of unique
    positive SQLite integers. Missing IDs at or below the AUTOINCREMENT boundary
    are idempotent no-ops, not proof of ownership; future IDs are refused. Reapply
    recorded deletions to restored snapshots before accepting new writes.
    """
    if ((owner is None) == (older_than_days is None)
            or (owner is not None and (not isinstance(owner, str) or not owner.strip()))
            or (older_than_days is not None
                and (type(older_than_days) is not int or not 1 <= older_than_days <= 36500))):
        raise RetentionError("one_valid_deletion_scope_required")
    if audit_ids is not None:
        if owner is None or older_than_days is not None:
            raise RetentionError("one_valid_deletion_scope_required")
        if (not isinstance(audit_ids, Sequence) or isinstance(audit_ids, (str, bytes, bytearray))
                or not audit_ids
                or any(type(value) is not int or not 1 <= value <= 2**63 - 1
                       for value in audit_ids)
                or len(set(audit_ids)) != len(audit_ids)):
            raise RetentionError("valid_unique_audit_ids_required")
        audit_ids = tuple(audit_ids)
    scope = "audit_ids" if audit_ids is not None else "owner" if owner is not None else "retention"
    if apply and not service_stopped:
        raise RetentionError("service_stop_acknowledgment_required")
    clock = now or datetime.now(timezone.utc)
    if not isinstance(clock, datetime) or clock.utcoffset() is None:
        raise RetentionError("timezone_aware_clock_required")
    clock = clock.astimezone(timezone.utc)
    try:
        cutoff = clock - timedelta(days=older_than_days) if older_than_days is not None else None
    except OverflowError:
        raise RetentionError("retention_cutoff_out_of_range") from None
    committed = False
    try:
        path = _path(db)
        if not apply:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)) as conn:
                conn.execute("BEGIN")
                _validate(conn)
                ids = _selection(conn, owner, cutoff, audit_ids)
                return {"mode": "preview", "scope": scope,
                        **({"requested_audits": len(audit_ids),
                            "already_absent_audits": len(audit_ids) - len(ids)}
                           if audit_ids is not None else {}),
                        "cutoff_utc": cutoff.isoformat() if cutoff else None,
                        "matched_audits": len(ids), "deleted_audits": 0,
                        "legacy_rows_preserved": conn.execute(
                            "SELECT COUNT(*) FROM audit_runs WHERE owner_id IS NULL",
                        ).fetchone()[0]}
        with _deployment_lock(path), closing(sqlite3.connect(
                path.as_uri() + "?mode=rw", uri=True, timeout=0, isolation_level=None)) as conn:
            journal = _begin_maintenance(conn)
            try:
                _validate(conn)
                ids = _selection(conn, owner, cutoff, audit_ids)
                conn.execute("CREATE TEMP TABLE deletion_ids (id INTEGER PRIMARY KEY)")
                conn.executemany("INSERT INTO deletion_ids VALUES (?)", ((value,) for value in ids))
                # Public writes disallow cross-owner retries; don't mutate another
                # owner's corrupt reference while honoring a deletion request.
                if conn.execute("""SELECT 1 FROM audit_runs child JOIN audit_runs parent
                        ON child.retry_of=parent.id JOIN deletion_ids d ON d.id=parent.id
                        WHERE child.owner_id IS NOT parent.owner_id LIMIT 1""").fetchone():
                    raise RetentionError("cross_owner_retry_reference")
                unlinked = conn.execute("""UPDATE audit_runs SET retry_of=NULL
                    WHERE retry_of IN (SELECT id FROM deletion_ids)
                    AND id NOT IN (SELECT id FROM deletion_ids)""").rowcount
                deleted = conn.execute(
                    "DELETE FROM audit_runs WHERE id IN (SELECT id FROM deletion_ids)",
                ).rowcount
                conn.execute("COMMIT")
                committed = True
            except Exception:
                conn.execute("ROLLBACK")
                raise
            _compact(conn, journal)
            _validate(conn)
            return {"mode": "applied", "scope": scope,
                    **({"requested_audits": len(audit_ids),
                        "already_absent_audits": len(audit_ids) - len(ids)}
                       if audit_ids is not None else {}),
                    "cutoff_utc": cutoff.isoformat() if cutoff else None,
                    "matched_audits": len(ids), "deleted_audits": deleted,
                    "retry_links_cleared": unlinked, "database_compacted": True,
                    "legacy_rows_preserved": conn.execute(
                        "SELECT COUNT(*) FROM audit_runs WHERE owner_id IS NULL",
                    ).fetchone()[0], "backups_processed": False,
                    "quota_and_invite_state_changed": False}
    except RetentionError as error:
        if committed:
            error.deletion_committed = True
        raise
    except (OSError, sqlite3.Error):
        raise RetentionError("audit_database_unavailable_or_busy",
                             deletion_committed=committed) from None


def _usage_expiry(conn: sqlite3.Connection, now: float) -> tuple[float, dict]:
    required = {
        "beta_requests": {"id": ("INTEGER", 0, 1), "owner_id": ("TEXT", 1, 0),
                          "at": ("REAL", 1, 0)},
        "beta_daily_audits": {"day": ("INTEGER", 1, 1), "owner_id": ("TEXT", 1, 2),
                              "attempts": ("INTEGER", 1, 0)},
        "beta_audit_leases": {"lease_id": ("TEXT", 0, 1), "owner_id": ("TEXT", 1, 0),
                              "expires_at": ("REAL", 1, 0)},
        "beta_usage_clock": {"id": ("INTEGER", 0, 1), "last_seen": ("REAL", 1, 0)},
    }
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'",
    )}
    if tables != set(required) or conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('trigger', 'view')").fetchone():
        raise RetentionError("unsupported_usage_schema")
    for table, expected in required.items():
        columns = {row[1]: (row[2].upper(), row[3], row[5])
                   for row in conn.execute(f'PRAGMA table_info("{table}")')}
        if columns != expected or conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchone():
            raise RetentionError("unsupported_usage_schema")
    if not all(_rowid_primary_key(conn, table, "id")
               for table in ("beta_requests", "beta_usage_clock")):
        raise RetentionError("unsupported_usage_schema")
    if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise RetentionError("usage_integrity_failed")
    watermark = conn.execute("SELECT id, last_seen FROM beta_usage_clock").fetchall()
    if (len(watermark) != 1 or watermark[0][0] != 1
            or type(watermark[0][1]) not in (int, float) or not math.isfinite(watermark[0][1])
            or not 0 <= watermark[0][1] <= 253402300799):
        raise RetentionError("invalid_usage_clock")
    now = max(now, watermark[0][1])
    for table, column in (("beta_requests", "at"), ("beta_audit_leases", "expires_at")):
        if any(type(value) not in (int, float) or not math.isfinite(value)
               or not 0 <= value <= 253402300799
               for (value,) in conn.execute(f'SELECT "{column}" FROM "{table}"')):
            raise RetentionError("invalid_usage_timestamp")
    if any(type(day) is not int or day < 0 or type(attempts) is not int or attempts < 0
           for day, attempts in conn.execute("SELECT day, attempts FROM beta_daily_audits")):
        raise RetentionError("invalid_daily_usage")
    return now, {
        "beta_requests": ("at<=?", now - 60),
        "beta_daily_audits": ("day<?", int(now // 86400) - 7),
        "beta_audit_leases": ("expires_at<=?", now),
    }


def maintain_usage(
    db: Path, *, apply: bool = False, service_stopped: bool = False,
    now: Optional[datetime] = None,
) -> dict:
    """Remove only already expired quota records, never reset an owner's limits."""
    if apply and not service_stopped:
        raise RetentionError("service_stop_acknowledgment_required")
    clock = now or datetime.now(timezone.utc)
    if (not isinstance(clock, datetime) or clock.utcoffset() is None
            or not 0 <= clock.timestamp() <= 253402300799):
        raise RetentionError("timezone_aware_clock_required")
    committed = False
    try:
        path = _path(db)
        if not apply:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)) as conn:
                conn.execute("BEGIN")
                _, expiry = _usage_expiry(conn, clock.timestamp())
                counts = {table: conn.execute(f'SELECT COUNT(*) FROM "{table}" WHERE {predicate}',
                                             (cutoff,)).fetchone()[0]
                          for table, (predicate, cutoff) in expiry.items()}
                return {"mode": "preview", "scope": "expired_usage", "matched_records": counts}
        with _deployment_lock(path), closing(sqlite3.connect(
                path.as_uri() + "?mode=rw", uri=True, timeout=0, isolation_level=None)) as conn:
            journal = _begin_maintenance(conn)
            try:
                current, expiry = _usage_expiry(conn, clock.timestamp())
                counts = {table: conn.execute(f'DELETE FROM "{table}" WHERE {predicate}',
                                             (cutoff,)).rowcount
                          for table, (predicate, cutoff) in expiry.items()}
                conn.execute("UPDATE beta_usage_clock SET last_seen=? WHERE id=1", (current,))
                conn.execute("COMMIT")
                committed = True
            except Exception:
                conn.execute("ROLLBACK")
                raise
            _compact(conn, journal)
            _usage_expiry(conn, current)
            return {"mode": "applied", "scope": "expired_usage", "deleted_records": counts,
                    "database_compacted": True, "current_quotas_preserved": True,
                    "backups_processed": False}
    except RetentionError as error:
        if committed:
            error.deletion_committed = True
        raise
    except (OSError, sqlite3.Error):
        raise RetentionError("usage_database_unavailable_or_busy",
                             deletion_committed=committed) from None


def _audit_id_argument(value: str) -> int:
    try:
        audit_id = int(value)
        if not 1 <= audit_id <= 2**63 - 1:
            raise ValueError
        return audit_id
    except ValueError:
        # argparse's default int conversion echoes the supplied argument.
        raise argparse.ArgumentTypeError("positive SQLite integer required") from None


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--db", type=Path, help="Existing absolute audit DB path")
    target.add_argument("--usage-db", type=Path, help="Existing absolute usage DB: expired rows only")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--owner", help="Exact owner ID; never a token or API key")
    scope.add_argument("--older-than-days", type=int, help="Delete owned audits at least this old")
    parser.add_argument("--audit-id", action="append", type=_audit_id_argument, dest="audit_ids",
                        help="Repeat to select exact audit IDs; requires --owner, no age scope")
    parser.add_argument("--apply", action="store_true", help="Delete; default only previews counts")
    parser.add_argument("--service-stopped", action="store_true",
                        help="Operator confirms all audit processes and auto-deploy are stopped")
    args = parser.parse_args(argv)
    try:
        if args.usage_db is not None:
            if (args.owner is not None or args.older_than_days is not None
                    or args.audit_ids is not None):
                raise RetentionError("usage_scope_is_expiry_only")
            result = maintain_usage(args.usage_db, apply=args.apply,
                                    service_stopped=args.service_stopped)
        else:
            result = maintain_audits(args.db, owner=args.owner, older_than_days=args.older_than_days,
                                    audit_ids=args.audit_ids,
                                    apply=args.apply, service_stopped=args.service_stopped)
    except RetentionError as error:
        print(json.dumps({"error": str(error), "deletion_committed": error.deletion_committed}),
              file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

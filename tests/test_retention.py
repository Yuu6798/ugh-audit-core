"""Exercise offline retention on real isolated SQLite files, never production data."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from ugh_audit.retention import main, maintain_audits, maintain_usage, RetentionError
from ugh_audit.storage.audit_db import AuditDB
from ugh_audit.usage_limits import Limits, LimitExceeded, UsageLimiter


NOW = datetime(2026, 10, 29, tzinfo=timezone.utc)
OLD = "2026-09-28T23:59:59+00:00"
NEW = "2026-10-28T12:00:00+00:00"


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "audit.db"
    db = AuditDB(path)
    a_old = db.save(owner_id="alice", response="ALICE_PRIVATE_OLD_SENTINEL")
    a_new = db.save(owner_id="alice", response="ALICE_PRIVATE_NEW_SENTINEL", retry_of=a_old)
    b = db.save(owner_id="bob", response="BOB_PRIVATE_SENTINEL")
    legacy = db.save(response="LEGACY_PRIVATE_SENTINEL")
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE audit_runs SET created_at=?", (NEW,))
        conn.execute("UPDATE audit_runs SET created_at=? WHERE id=?", (OLD, a_old))
        conn.execute("UPDATE audit_runs SET created_at='legacy-unknown' WHERE id=?", (legacy,))
    return path, (a_old, a_new, b, legacy)


def _rows(path):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute("SELECT id, owner_id, response, retry_of FROM audit_runs ORDER BY id").fetchall()


def test_default_preview_is_read_only_and_omits_identifiers(database):
    path, _ = database
    digest = hashlib.sha256(path.read_bytes()).digest()
    result = maintain_audits(path, owner="alice", now=NOW)
    assert result["matched_audits"] == 2 and result["deleted_audits"] == 0
    assert result["legacy_rows_preserved"] == 1
    assert hashlib.sha256(path.read_bytes()).digest() == digest
    assert not Path(str(path) + ".deployment.lock").exists()
    assert "alice" not in json.dumps(result) and "PRIVATE" not in json.dumps(result)


def test_owner_deletion_preserves_other_users_legacy_and_separate_state(database):
    path, ids = database
    before = _rows(path)
    quota = path.with_name("usage.db")
    quota.write_bytes(b"separate quota state")
    invites = path.with_name("invites.json")
    invites.write_bytes(b"separate invitation state")
    backup = path.parent / "deployment-backups"
    backup.mkdir()
    (backup / "historical.sqlite").write_bytes(path.read_bytes())
    result = maintain_audits(path, owner="alice", apply=True, service_stopped=True, now=NOW)
    assert result["deleted_audits"] == 2 and result["database_compacted"]
    assert _rows(path) == before[2:]
    assert b"ALICE_PRIVATE" not in path.read_bytes()
    assert quota.read_bytes() == b"separate quota state"
    assert invites.read_bytes() == b"separate invitation state"
    assert b"ALICE_PRIVATE" in (backup / "historical.sqlite").read_bytes()
    assert not result["backups_processed"] and not result["quota_and_invite_state_changed"]
    assert maintain_audits(path, owner="alice", apply=True, service_stopped=True)["deleted_audits"] == 0
    # Preserve SQLite's sequence; don't let a deleted ID refer to a new audit.
    assert AuditDB(path).save(owner_id="bob") > max(ids)


def test_retention_unlinks_retry_but_keeps_newer_audit_and_legacy(database):
    path, (old, new, _, _) = database
    result = maintain_audits(path, older_than_days=30, apply=True, service_stopped=True, now=NOW)
    assert result["deleted_audits"] == 1 and result["retry_links_cleared"] == 1
    rows = _rows(path)
    assert [row[0] for row in rows] == [2, 3, 4]
    assert rows[0][0] == new and rows[0][3] is None
    assert b"ALICE_PRIVATE_OLD_SENTINEL" not in path.read_bytes()
    assert b"ALICE_PRIVATE_NEW_SENTINEL" in path.read_bytes()


@pytest.mark.parametrize("stamp,expected", [
    ("2026-09-29T00:00:00Z", 1),
    ("2026-09-29T09:00:00+09:00", 1),
    ("2026-09-29T00:00:00.000001+00:00", 0),
])
def test_cutoff_normalizes_timezone_and_includes_exact_boundary(database, stamp, expected):
    path, (old, *_) = database
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE audit_runs SET created_at=? WHERE id=?", (stamp, old))
    result = maintain_audits(path, older_than_days=30, now=NOW)
    assert result["matched_audits"] == expected


@pytest.mark.parametrize("stamp", ["not-a-date", "2026-09-01T00:00:00", "2026-99-99T00:00:00Z"])
def test_unknown_owned_dates_fail_without_deleting(database, stamp):
    path, (old, *_) = database
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE audit_runs SET created_at=? WHERE id=?", (stamp, old))
    before = _rows(path)
    with pytest.raises(RetentionError, match="invalid_owned_audit_timestamp"):
        maintain_audits(path, older_than_days=30, apply=True, service_stopped=True, now=NOW)
    assert _rows(path) == before


@pytest.mark.parametrize("kwargs", [
    {}, {"owner": ""}, {"owner": "   "}, {"owner": "alice", "older_than_days": 30},
    {"older_than_days": 0}, {"older_than_days": -1}, {"older_than_days": True},
    {"older_than_days": 36501},
])
def test_invalid_scopes_do_not_touch_database(database, kwargs):
    path, _ = database
    before = path.read_bytes()
    with pytest.raises(RetentionError, match="one_valid_deletion_scope_required"):
        maintain_audits(path, **kwargs)
    assert path.read_bytes() == before


def test_apply_requires_service_stop_acknowledgment(database):
    path, _ = database
    before = path.read_bytes()
    with pytest.raises(RetentionError, match="service_stop_acknowledgment_required"):
        maintain_audits(path, owner="alice", apply=True)
    assert path.read_bytes() == before


def test_missing_relative_symlink_and_hardlinked_paths_are_rejected(database, tmp_path):
    path, _ = database
    for candidate in (tmp_path / "missing.db", Path("relative.db"), tmp_path):
        with pytest.raises(RetentionError, match="existing_regular_database_required"):
            maintain_audits(candidate, owner="alice")
    alias = tmp_path / "alias.db"
    alias.hardlink_to(path)
    with pytest.raises(RetentionError, match="existing_regular_database_required"):
        maintain_audits(alias, owner="alice")
    alias.unlink()
    try:
        alias.symlink_to(path)
    except OSError:
        return  # Windows may not grant symlink privilege.
    with pytest.raises(RetentionError, match="existing_regular_database_required"):
        maintain_audits(alias, owner="alice")


@pytest.mark.parametrize("sql", [
    "CREATE TABLE private_copy (value TEXT)",
    "CREATE VIEW private_view AS SELECT response FROM audit_runs",
    "CREATE TRIGGER duplicate_on_delete AFTER DELETE ON audit_runs "
    "BEGIN INSERT INTO audit_runs (created_at, response) VALUES ('unknown', OLD.response); END",
    "ALTER TABLE audit_runs RENAME COLUMN owner_id TO unrecognized_owner",
])
def test_unknown_schema_is_refused(database, sql):
    path, _ = database
    with sqlite3.connect(path) as conn:
        conn.execute(sql)
    before = path.read_bytes()
    with pytest.raises(RetentionError, match="unsupported_audit_schema"):
        maintain_audits(path, owner="alice", apply=True, service_stopped=True)
    assert path.read_bytes() == before


def test_cross_owner_retry_corruption_does_not_modify_foreign_owner(database):
    path, (a, _, b, _) = database
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE audit_runs SET retry_of=? WHERE id=?", (a, b))
    before = _rows(path)
    with pytest.raises(RetentionError, match="cross_owner_retry_reference"):
        maintain_audits(path, owner="alice", apply=True, service_stopped=True)
    assert _rows(path) == before


@pytest.mark.parametrize("id_definition,table_suffix,ids", [
    ("id INTEGER,", ", PRIMARY KEY(id, owner_id)", (1, 1)),
    ("id INTEGER PRIMARY KEY,", ") WITHOUT ROWID", (1, 2)),
    ("id INTEGER PRIMARY KEY DESC,", "", (None, None)),
    ("id INTEGER UNIQUE,", "", (1, 2)),
    ("id INTEGER PRIMARY KEY,", "", (1, 2)),
    ("id INTEGER PRIMARY KEY /* AUTOINCREMENT */,", "", (1, 2)),
])
def test_non_rowid_or_non_unique_audit_ids_are_rejected_without_touching_owners(
        tmp_path, id_definition, table_suffix, ids):
    path = tmp_path / "unsupported.db"
    sql = f"""CREATE TABLE audit_runs ({id_definition}
        owner_id TEXT, created_at TEXT, retry_of INTEGER,
        question TEXT, response TEXT, session_id TEXT"""
    sql += table_suffix if table_suffix.startswith(")") else table_suffix + ")"
    with sqlite3.connect(path) as conn:
        conn.execute(sql)
        conn.executemany("INSERT INTO audit_runs (id, owner_id, created_at, response) VALUES (?, ?, ?, ?)",
                         [(ids[0], "alice", OLD, "alice-private"),
                          (ids[1], "bob", OLD, "bob-private")])
    before = path.read_bytes()
    for apply in (False, True):
        with pytest.raises(RetentionError, match="unsupported_audit_schema"):
            maintain_audits(path, owner="alice", apply=apply, service_stopped=True)
        assert path.read_bytes() == before
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT owner_id, response FROM audit_runs ORDER BY owner_id").fetchall() == [
            ("alice", "alice-private"), ("bob", "bob-private"),
        ]


def test_active_reader_refuses_apply_without_partial_deletion(database):
    path, _ = database
    before = _rows(path)
    with closing(sqlite3.connect(path)) as reader:
        reader.execute("BEGIN")
        reader.execute("SELECT id FROM audit_runs").fetchall()
        with pytest.raises(RetentionError) as error:
            maintain_audits(path, owner="alice", apply=True, service_stopped=True)
        assert not error.value.deletion_committed
    assert _rows(path) == before


def test_wal_mode_removes_deleted_text_from_active_database_and_sidecar(database):
    path, _ = database
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
    result = maintain_audits(path, owner="alice", apply=True, service_stopped=True)
    assert result["deleted_audits"] == 2
    for candidate in (path, Path(str(path) + "-wal")):
        if candidate.exists():
            assert b"ALICE_PRIVATE" not in candidate.read_bytes()


def test_wal_pinned_reader_refuses_apply_before_deleting(database):
    path, _ = database
    with closing(sqlite3.connect(path)) as reader, closing(sqlite3.connect(path)) as writer:
        reader.execute("PRAGMA journal_mode=WAL")
        reader.execute("BEGIN")
        reader.execute("SELECT id FROM audit_runs").fetchall()
        writer.execute("UPDATE audit_runs SET session_id='newer WAL frame'")
        writer.commit()
        with pytest.raises(RetentionError) as error:
            maintain_audits(path, owner="alice", apply=True, service_stopped=True)
        assert not error.value.deletion_committed
    assert len(_rows(path)) == 4


def test_cli_outputs_only_receipt_and_fixed_error_codes(database, capsys):
    path, _ = database
    assert main(["--db", str(path), "--owner", "alice"]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["mode"] == "preview"
    assert "alice" not in output.out and "PRIVATE" not in output.out
    assert main(["--db", str(path), "--owner", "alice", "--apply"]) == 1
    output = capsys.readouterr()
    assert json.loads(output.err) == {"error": "service_stop_acknowledgment_required",
                                     "deletion_committed": False}


def test_owner_parameter_is_not_interpreted_as_sql(database):
    path, _ = database
    result = maintain_audits(path, owner="alice' OR 1=1 --", apply=True, service_stopped=True)
    assert result["deleted_audits"] == 0 and len(_rows(path)) == 4


@pytest.fixture
def usage_database(tmp_path):
    path = tmp_path / "usage.db"
    UsageLimiter(path)
    now = NOW.timestamp()
    day = int(now // 86400)
    with sqlite3.connect(path) as conn:
        conn.executemany("INSERT INTO beta_requests(owner_id, at) VALUES (?, ?)",
                         [("expired-request-owner", now - 60), ("alice", now - 59)])
        conn.executemany("INSERT INTO beta_daily_audits VALUES (?, ?, ?)",
                         [(day - 8, "expired-daily-owner", 99), (day - 7, "bob", 2),
                          (day, "alice", 1)])
        conn.executemany("INSERT INTO beta_audit_leases VALUES (?, ?, ?)",
                         [("expired", "expired-lease-owner", now), ("active", "bob", now + 300)])
        conn.execute("UPDATE beta_usage_clock SET last_seen=?", (now,))
    return path


def test_usage_preview_and_expiry_preserve_current_limits(usage_database):
    path = usage_database
    before = path.read_bytes()
    preview = maintain_usage(path, now=NOW)
    assert preview["matched_records"] == {
        "beta_requests": 1, "beta_daily_audits": 1, "beta_audit_leases": 1,
    }
    assert path.read_bytes() == before
    result = maintain_usage(path, now=NOW, apply=True, service_stopped=True)
    assert result["deleted_records"] == preview["matched_records"]
    assert b"expired-" not in path.read_bytes()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT owner_id, at FROM beta_requests").fetchall() == [
            ("alice", NOW.timestamp() - 59),
        ]
        assert conn.execute("SELECT owner_id, attempts FROM beta_daily_audits ORDER BY day").fetchall() == [
            ("bob", 2), ("alice", 1),
        ]
        assert conn.execute("SELECT lease_id FROM beta_audit_leases").fetchall() == [("active",)]
        assert conn.execute("SELECT last_seen FROM beta_usage_clock").fetchone()[0] == NOW.timestamp()
    limiter = UsageLimiter(path, Limits(audits_per_day=1), clock=lambda: NOW.timestamp())
    with pytest.raises(LimitExceeded, match="daily_audit_limit"):
        limiter.acquire_audit("alice")


def test_usage_cleanup_honors_clock_watermark_after_clock_rollback(usage_database):
    path = usage_database
    earlier = datetime(2026, 10, 1, tzinfo=timezone.utc)
    result = maintain_usage(path, now=earlier, apply=True, service_stopped=True)
    assert result["deleted_records"]["beta_daily_audits"] == 1
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT last_seen FROM beta_usage_clock").fetchone()[0] == NOW.timestamp()
        assert conn.execute("SELECT attempts FROM beta_daily_audits WHERE owner_id='alice'").fetchone() == (1,)


@pytest.mark.parametrize("sql,code", [
    ("DELETE FROM beta_usage_clock", "invalid_usage_clock"),
    ("UPDATE beta_usage_clock SET last_seen=-1", "invalid_usage_clock"),
    ("UPDATE beta_requests SET at='unknown'", "invalid_usage_timestamp"),
    ("UPDATE beta_daily_audits SET attempts=-1", "invalid_daily_usage"),
    ("CREATE TABLE private_copy (body TEXT)", "unsupported_usage_schema"),
])
def test_invalid_usage_ledgers_fail_before_deletion(usage_database, sql, code):
    path = usage_database
    with sqlite3.connect(path) as conn:
        conn.execute(sql)
    before = path.read_bytes()
    with pytest.raises(RetentionError, match=code):
        maintain_usage(path, now=NOW, apply=True, service_stopped=True)
    assert path.read_bytes() == before


def test_audit_and_usage_modes_reject_each_others_database(database, usage_database):
    path, _ = database
    with pytest.raises(RetentionError, match="unsupported_usage_schema"):
        maintain_usage(path)
    with pytest.raises(RetentionError, match="unsupported_audit_schema"):
        maintain_audits(usage_database, owner="alice")


def test_usage_cli_forbids_owner_erasure(usage_database, capsys):
    assert main(["--usage-db", str(usage_database), "--owner", "alice"]) == 1
    assert json.loads(capsys.readouterr().err)["error"] == "usage_scope_is_expiry_only"
    assert main(["--usage-db", str(usage_database)]) == 0
    assert json.loads(capsys.readouterr().out)["scope"] == "expired_usage"


def test_usage_active_writer_refuses_cleanup(usage_database):
    with closing(sqlite3.connect(usage_database)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(RetentionError) as error:
            maintain_usage(usage_database, now=NOW, apply=True, service_stopped=True)
        assert not error.value.deletion_committed


@pytest.mark.parametrize("replacement", [
    "CREATE TABLE beta_requests (id INTEGER, owner_id TEXT NOT NULL, at REAL NOT NULL, "
    "PRIMARY KEY(id, owner_id))",
    "CREATE TABLE beta_requests (id INTEGER PRIMARY KEY DESC, owner_id TEXT NOT NULL, at REAL NOT NULL)",
    "CREATE TABLE beta_requests (id INTEGER PRIMARY KEY, owner_id TEXT NOT NULL, at REAL NOT NULL) "
    "WITHOUT ROWID",
])
def test_usage_requires_expected_primary_key_contract(usage_database, replacement):
    path = usage_database
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE beta_requests")
        conn.execute(replacement)
        conn.execute("INSERT INTO beta_requests VALUES (1, 'alice', ?)", (NOW.timestamp(),))
    before = path.read_bytes()
    with pytest.raises(RetentionError, match="unsupported_usage_schema"):
        maintain_usage(path, apply=True, service_stopped=True, now=NOW)
    assert path.read_bytes() == before


def test_usage_apply_requires_stop_acknowledgment(usage_database):
    before = usage_database.read_bytes()
    with pytest.raises(RetentionError, match="service_stop_acknowledgment_required"):
        maintain_usage(usage_database, apply=True)
    assert usage_database.read_bytes() == before


def test_inflight_deployment_blocks_audit_maintenance(database):
    from ugh_audit.deployment import _startup_lock

    path, _ = database
    before = _rows(path)
    with _startup_lock(Path(str(path) + ".deployment.lock")):
        with pytest.raises(RetentionError) as error:
            maintain_audits(path, owner="alice", apply=True, service_stopped=True)
        assert not error.value.deletion_committed
    assert _rows(path) == before


def test_persistent_journal_does_not_retain_deleted_payload(database):
    path, _ = database
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA journal_mode=PERSIST")
        conn.execute("UPDATE audit_runs SET session_id='populate persistent rollback journal'")
        conn.commit()
    maintain_audits(path, owner="alice", apply=True, service_stopped=True)
    for suffix in ("", "-journal", "-wal"):
        candidate = Path(str(path) + suffix)
        if candidate.exists():
            assert b"ALICE_PRIVATE" not in candidate.read_bytes()


def test_cleanup_failure_reports_that_deletion_already_committed(database, monkeypatch):
    from ugh_audit import retention

    path, _ = database

    def fail_cleanup(conn, journal):
        raise sqlite3.OperationalError("synthetic disk failure during compaction")

    monkeypatch.setattr(retention, "_compact", fail_cleanup)
    with pytest.raises(RetentionError) as error:
        maintain_audits(path, owner="alice", apply=True, service_stopped=True)
    assert error.value.deletion_committed
    assert [row[1] for row in _rows(path)] == ["bob", None]
    assert "synthetic disk" not in str(error.value)

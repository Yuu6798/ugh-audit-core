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


def test_selected_audit_preview_and_delete_preserve_unselected_rows(database):
    path, (old, new, _, _) = database
    before = _rows(path)
    digest = path.read_bytes()
    preview = maintain_audits(path, owner="alice", audit_ids=[old])
    assert preview["scope"] == "audit_ids"
    assert preview["requested_audits"] == preview["matched_audits"] == 1
    assert preview["already_absent_audits"] == preview["deleted_audits"] == 0
    assert "alice" not in json.dumps(preview) and "PRIVATE" not in json.dumps(preview)
    assert path.read_bytes() == digest
    assert not Path(str(path) + ".deployment.lock").exists()

    result = maintain_audits(path, owner="alice", audit_ids=[old],
                             apply=True, service_stopped=True)
    assert result["deleted_audits"] == result["retry_links_cleared"] == 1
    assert result["database_compacted"]
    assert _rows(path) == [(new, "alice", before[1][2], None), *before[2:]]
    assert b"ALICE_PRIVATE_OLD_SENTINEL" not in path.read_bytes()
    assert b"ALICE_PRIVATE_NEW_SENTINEL" in path.read_bytes()


@pytest.mark.parametrize("foreign_index", [2, 3])
@pytest.mark.parametrize("foreign_first", [False, True])
@pytest.mark.parametrize("apply", [False, True])
def test_selected_foreign_or_legacy_id_rejects_entire_request(
        database, foreign_index, foreign_first, apply):
    path, ids = database
    selected = [ids[0], ids[foreign_index]]
    if foreign_first:
        selected.reverse()
    before = path.read_bytes()
    with pytest.raises(RetentionError, match="audit_id_owner_mismatch") as error:
        maintain_audits(path, owner="alice", audit_ids=selected,
                        apply=apply, service_stopped=True)
    assert not error.value.deletion_committed
    assert path.read_bytes() == before


@pytest.mark.parametrize("audit_ids", [
    [], (), True, 1, "1", b"\x01", bytearray([1]), {1}, [True], [False],
    [1.0], ["1"], [None], [0], [-1],
    [1, 1], [2**63],
])
def test_invalid_selected_ids_are_rejected_before_database_access(database, audit_ids):
    path, _ = database
    before = path.read_bytes()
    with pytest.raises(RetentionError, match="valid_unique_audit_ids_required"):
        maintain_audits(path, owner="alice", audit_ids=audit_ids,
                        apply=True, service_stopped=True)
    assert path.read_bytes() == before
    assert not Path(str(path) + ".deployment.lock").exists()


@pytest.mark.parametrize("kwargs", [
    {"audit_ids": [1]}, {"audit_ids": [1], "older_than_days": 30},
    {"audit_ids": [1], "owner": "alice", "older_than_days": 30},
    {"audit_ids": [1], "owner": ""}, {"audit_ids": [1], "owner": True},
])
def test_selected_ids_require_owner_and_forbid_age_scope(database, kwargs):
    path, _ = database
    before = path.read_bytes()
    with pytest.raises(RetentionError, match="one_valid_deletion_scope_required"):
        maintain_audits(path, **kwargs)
    assert path.read_bytes() == before


def test_selected_ids_require_service_stop_acknowledgment(database):
    path, (old, *_) = database
    before = path.read_bytes()
    with pytest.raises(RetentionError, match="service_stop_acknowledgment_required"):
        maintain_audits(path, owner="alice", audit_ids=[old], apply=True)
    assert path.read_bytes() == before


@pytest.mark.parametrize("missing", [5, 2**63 - 1])
@pytest.mark.parametrize("apply", [False, True])
def test_future_audit_ids_fail_without_partial_deletion(database, missing, apply):
    path, (old, *_) = database
    before = path.read_bytes()
    with pytest.raises(RetentionError, match="audit_id_not_previously_allocated") as error:
        maintain_audits(path, owner="alice", audit_ids=[old, missing],
                        apply=apply, service_stopped=True)
    assert not error.value.deletion_committed
    assert path.read_bytes() == before


def test_selected_deletion_restores_and_reapplies_without_deleting_new_audits(database):
    path, (old, new, _, _) = database
    last = AuditDB(path).save(owner_id="alice", response="SELECTED_HIGHEST_PRIVATE")
    snapshot = path.read_bytes()
    selected = (old, last)
    original_rows = _rows(path)
    for restore in (False, True):
        if restore:
            # Reapply the recorded deletion before accepting writes on restore.
            path.write_bytes(snapshot)
        result = maintain_audits(path, owner="alice", audit_ids=selected,
                                 apply=True, service_stopped=True)
        assert result["deleted_audits"] == 2
        assert result["retry_links_cleared"] == 1
        assert _rows(path) == [(new, "alice", original_rows[1][2], None), *original_rows[2:4]]
        added = AuditDB(path).save(owner_id="alice", response="POST_DELETE_NEW_AUDIT")
        assert added > last
        before_replay = _rows(path)
        repeated = maintain_audits(path, owner="alice", audit_ids=selected,
                                   apply=True, service_stopped=True)
        assert repeated["deleted_audits"] == repeated["matched_audits"] == 0
        assert repeated["already_absent_audits"] == 2
        assert repeated["requested_audits"] == 2
        assert _rows(path) == before_replay
        assert b"POST_DELETE_NEW_AUDIT" in path.read_bytes()


def test_selected_deletion_accepts_mixed_existing_and_already_deleted_ids(database):
    path, (old, new, _, _) = database
    maintain_audits(path, owner="alice", audit_ids=[old], apply=True, service_stopped=True)
    result = maintain_audits(path, owner="alice", audit_ids=[old, new],
                             apply=True, service_stopped=True)
    assert result["already_absent_audits"] == result["deleted_audits"] == 1
    assert result["requested_audits"] == 2
    assert [row[1] for row in _rows(path)] == ["bob", None]


def test_restore_before_selected_id_allocation_refuses_replay(database):
    path, _ = database
    snapshot = path.read_bytes()
    selected = AuditDB(path).save(owner_id="alice")
    maintain_audits(path, owner="alice", audit_ids=[selected], apply=True, service_stopped=True)
    path.write_bytes(snapshot)
    with pytest.raises(RetentionError, match="audit_id_not_previously_allocated"):
        maintain_audits(path, owner="alice", audit_ids=[selected],
                        apply=True, service_stopped=True)
    assert path.read_bytes() == snapshot


@pytest.mark.parametrize("child_index", [2, 3])
def test_selected_deletion_refuses_corrupt_foreign_retry_without_mutating_rows(database, child_index):
    path, ids = database
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE audit_runs SET retry_of=? WHERE id=?", (ids[0], ids[child_index]))
    before = path.read_bytes()
    with pytest.raises(RetentionError, match="cross_owner_retry_reference") as error:
        maintain_audits(path, owner="alice", audit_ids=[ids[0]],
                        apply=True, service_stopped=True)
    assert not error.value.deletion_committed
    assert path.read_bytes() == before


def test_selected_deletion_ignores_unrelated_invalid_date(database):
    path, (old, new, _, _) = database
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE audit_runs SET created_at='unknown' WHERE id=?", (new,))
    result = maintain_audits(path, owner="alice", audit_ids=[old],
                             apply=True, service_stopped=True)
    assert result["deleted_audits"] == 1
    assert _rows(path)[0][0] == new


def test_selected_cli_repeated_ids_and_private_receipts(database, capsys):
    path, _ = database
    first, second = 123456789, 123456790
    with sqlite3.connect(path) as conn:
        conn.executemany("INSERT INTO audit_runs (id, owner_id, created_at, response) "
                         "VALUES (?, 'selected-private-owner', ?, 'selected-private-body')",
                         [(first, OLD), (second, NEW)])
    command = ["--db", str(path), "--owner", "selected-private-owner",
               "--audit-id", str(first), "--audit-id", str(second)]
    assert main(command) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["matched_audits"] == 2
    assert main([*command, "--apply", "--service-stopped"]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["deleted_audits"] == 2
    for private in (str(first), str(second), "selected-private-owner", "selected-private-body"):
        assert private not in output.out and private not in output.err


@pytest.mark.parametrize("value", ["secret-not-an-id", "0", "-1", "True", str(2**63)])
def test_cli_invalid_audit_id_omits_supplied_value(database, capsys, value):
    path, _ = database
    with pytest.raises(SystemExit) as error:
        main(["--db", str(path), "--owner", "alice", "--audit-id", value])
    assert error.value.code == 2
    output = capsys.readouterr()
    assert "positive SQLite integer required" in output.err
    assert f"'{value}'" not in output.err
    assert "secret-not-an-id" not in output.err


def test_cli_selected_scope_errors_are_fixed_and_non_destructive(database, capsys):
    path, (old, _, foreign, _) = database
    before = path.read_bytes()
    for args, expected in [
        (["--audit-id", str(old)], "one_valid_deletion_scope_required"),
        (["--older-than-days", "30", "--audit-id", str(old)],
         "one_valid_deletion_scope_required"),
        (["--owner", "alice", "--audit-id", str(old), "--audit-id", str(old)],
         "valid_unique_audit_ids_required"),
        (["--owner", "alice", "--audit-id", str(old), "--audit-id", str(foreign)],
         "audit_id_owner_mismatch"),
    ]:
        assert main(["--db", str(path), *args, "--apply", "--service-stopped"]) == 1
        output = capsys.readouterr()
        assert not output.out
        assert json.loads(output.err) == {"error": expected, "deletion_committed": False}
        assert path.read_bytes() == before


def test_usage_cli_forbids_audit_id_scope(usage_database, capsys):
    before = usage_database.read_bytes()
    assert main(["--usage-db", str(usage_database), "--audit-id", "1"]) == 1
    assert json.loads(capsys.readouterr().err)["error"] == "usage_scope_is_expiry_only"
    assert usage_database.read_bytes() == before

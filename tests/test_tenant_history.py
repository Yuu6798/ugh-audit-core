"""Per-owner history boundaries, legacy migration, and atomic retry ownership."""
from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from typing import Optional

import pytest

from ugh_audit.semantic_matching import DECISION_SCOPE, METRICS_SCOPE, assess_requirements
from ugh_audit.storage.audit_db import AuditDB


def _save_verified(
    db: AuditDB, owner: Optional[str], answer: str, judge, *, distance: float = 0.1,
) -> int:
    meta = {"core_propositions": [answer]}
    assessment = assess_requirements(meta, answer, judge)
    return db.save(
        owner_id=owner, session_id="shared-session", response=answer,
        C=1.0, verdict="accept", schema_version="4.0.0", decision_scope=DECISION_SCOPE,
        metrics_scope=METRICS_SCOPE, question_meta=meta, semantic_matching=assessment,
        requirement_verdict="complete", requires_review=False, is_reliable=True,
        legacy_metrics={"delta_e": distance, "quality_score": 5 - 4 * distance},
    )


def _legacy_database(path: Path) -> tuple:
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE audit_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL DEFAULT '', question TEXT NOT NULL DEFAULT '',
                response TEXT NOT NULL DEFAULT '', reference TEXT,
                S REAL, C REAL, delta_e REAL, quality_score REAL,
                verdict TEXT NOT NULL DEFAULT '', f1 REAL, f2 REAL, f3 REAL, f4 REAL,
                hit_rate TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, original_bytes BLOB
            );
        """)
        conn.execute(
            "INSERT INTO audit_runs(id,session_id,response,S,C,delta_e,quality_score,verdict,"
            "created_at,original_bytes) VALUES (7,'shared-session','legacy private answer',"
            "0.75,0.5,0.2,4.2,'rewrite','2026-01-01T00:00:00',?)",
            (b"\x00original\r\n\xff",),
        )
        return conn.execute("SELECT * FROM audit_runs").fetchone()


def test_migration_preserves_legacy_values_and_assigns_no_owner(tmp_path):
    path = tmp_path / "legacy.db"
    original = _legacy_database(path)
    db = AuditDB(path)
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT * FROM audit_runs WHERE id=7").fetchone()
        assert row[:len(original)] == original
        assert conn.execute("SELECT owner_id FROM audit_runs WHERE id=7").fetchone() == (None,)
        columns = {row[1]: row for row in conn.execute("PRAGMA table_info(audit_runs)")}
        assert columns["owner_id"][2] == "TEXT"
        assert columns["owner_id"][3] == 0
        assert {row[1] for row in conn.execute("PRAGMA index_list(audit_runs)")} >= {
            "idx_owner_session", "idx_owner_created_at",
        }

    assert db.get_by_id(7) is not None
    assert db.get_by_id(7, owner_id="alice") is None
    assert db.list_recent(owner_id="alice") == []
    assert db.drift_history(owner_id="alice") == []
    assert db.session_summary("shared-session", owner_id="alice")["total"] == 0
    owned = db.save(owner_id="alice", response="new private answer", session_id="shared-session")
    AuditDB(path)
    assert db.get_by_id(owned, owner_id="alice")["response"] == "new private answer"
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT * FROM audit_runs WHERE id=7").fetchone() == row


def test_owner_filter_applies_before_limits_and_shared_session_aggregation(
    tmp_path, semantic_complete_judge,
):
    db = AuditDB(tmp_path / "owners.db")
    legacy = db.save(response="unowned private answer", session_id="shared-session")
    bob_first = _save_verified(db, "bob", "bob first", semantic_complete_judge, distance=0.9)
    alice_first = _save_verified(db, "alice", "alice first", semantic_complete_judge, distance=0.1)
    _save_verified(db, "bob", "bob middle", semantic_complete_judge, distance=0.9)
    alice_last = _save_verified(db, "alice", "alice last", semantic_complete_judge, distance=0.3)
    _save_verified(db, "bob", "bob last", semantic_complete_judge, distance=0.9)

    # Explicit timestamps keep this an ordering/authorization test, independent
    # of OS clock resolution and the speed of the writes.
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("UPDATE audit_runs SET created_at = printf('2026-01-%02d',id)")

    assert db.get_by_id(alice_first, owner_id="alice")["verdict"] == "accept"
    assert db.get_by_id(bob_first, owner_id="alice") is None
    assert db.get_by_id(legacy, owner_id="alice") is None
    assert db.get_by_id(99999, owner_id="alice") is None
    assert db.list_recent(1, owner_id="alice")[0]["id"] == alice_last
    assert [row["response"] for row in db.drift_history(1, owner_id="alice")] == ["alice first"]
    assert {row["id"] for row in db.list_recent(10, owner_id="alice")} == {
        alice_first, alice_last,
    }
    assert {row["response"] for row in db.drift_history(10, owner_id="alice")} == {
        "alice first", "alice last",
    }
    summary = db.session_summary("shared-session", owner_id="alice")
    assert summary["total"] == summary["verified_total"] == 2
    assert summary["unverified_total"] == 0
    assert summary["legacy_summary"]["total"] == 2
    assert summary["legacy_summary"]["avg_delta_e"] == pytest.approx(0.2)
    assert summary["legacy_summary"]["avg_quality_score"] == pytest.approx(4.2)
    assert db.session_summary("shared-session", owner_id="bob")["total"] == 3

    assert db.list_recent(owner_id="unknown") == []
    assert db.drift_history(owner_id="unknown") == []
    assert db.session_summary("shared-session", owner_id="unknown")["total"] == 0
    # None deliberately preserves the trusted local/admin API, including legacy rows.
    assert len(db.list_recent()) == len(db.drift_history()) == 6
    assert db.session_summary("shared-session")["total"] == 6
    assert db.get_by_id(legacy)["response"] == "unowned private answer"
    with sqlite3.connect(db.db_path) as conn:
        assert conn.execute("SELECT owner_id FROM audit_runs WHERE id=?", (legacy,)).fetchone() == (None,)


@pytest.mark.parametrize("invalid_owner", ["", " \t\n", 0, False, [], b"alice"])
@pytest.mark.parametrize("operation", ["save", "get_by_id", "list_recent", "session_summary", "drift_history"])
def test_invalid_owner_never_falls_through_to_unscoped_access(tmp_path, invalid_owner, operation):
    db = AuditDB(tmp_path / "invalid-owner.db")
    row_id = db.save(response="trusted local record")
    args = {"get_by_id": (row_id,), "session_summary": ("session",)}.get(operation, ())
    with pytest.raises(ValueError, match="owner_id"):
        getattr(db, operation)(*args, owner_id=invalid_owner)
    assert len(db.list_recent()) == 1


def test_owner_ids_are_bound_as_sql_values(tmp_path):
    db = AuditDB(tmp_path / "bound-owner.db")
    db.save(owner_id="alice", response="alice private answer")
    unusual_owner = "' OR 1=1 --"
    own_id = db.save(owner_id=unusual_owner, response="unusual identity answer")
    assert [row["id"] for row in db.list_recent(owner_id=unusual_owner)] == [own_id]
    assert db.get_by_id(own_id, owner_id="alice") is None
    assert [row["response"] for row in db.drift_history(owner_id=unusual_owner)] == [
        "unusual identity answer",
    ]


def test_scoped_retries_require_existing_same_owner(tmp_path):
    db = AuditDB(tmp_path / "retry.db")
    original = db.save(owner_id="alice", response="alice original")
    foreign = db.save(owner_id="bob", response="bob original")
    legacy = db.save(response="unowned original")
    own_retry = db.save(owner_id="alice", retry_of=original, response="alice retry")
    assert db.get_by_id(own_retry, owner_id="alice")["retry_of"] == original
    count = len(db.list_recent())
    for invalid_reference in (foreign, legacy, 99999, 0, -1, True, "1"):
        with pytest.raises(ValueError, match="^Invalid retry reference$"):
            db.save(owner_id="alice", retry_of=invalid_reference)
        assert len(db.list_recent()) == count
    # The trusted local/admin contract remains unscoped for existing callers.
    admin_retry = db.save(retry_of=foreign)
    assert db.get_by_id(admin_retry)["retry_of"] == foreign


def test_retry_checks_owner_after_waiting_for_concurrent_writer(tmp_path):
    path = tmp_path / "retry-atomic.db"
    attempting = threading.Event()

    class WaitingDB(AuditDB):
        def _conn(self):
            conn = super()._conn()
            conn.set_trace_callback(
                lambda sql: attempting.set() if sql == "BEGIN IMMEDIATE" else None,
            )
            return conn

    db = WaitingDB(path)
    original = db.save(owner_id="alice", response="original")
    attempting.clear()
    with sqlite3.connect(path) as writer, ThreadPoolExecutor(max_workers=1) as workers:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE audit_runs SET owner_id='bob' WHERE id=?", (original,))
        future = workers.submit(db.save, owner_id="alice", retry_of=original)
        try:
            assert attempting.wait(timeout=5)
            with pytest.raises(TimeoutError):
                future.result(timeout=0.2)
        finally:
            writer.commit()
        with pytest.raises(ValueError, match="^Invalid retry reference$"):
            future.result(timeout=10)
    assert len(db.list_recent()) == 1
    assert db.get_by_id(original, owner_id="bob") is not None


@pytest.mark.parametrize("legacy", [False, True])
def test_parallel_initialization_installs_owner_columns_and_indexes(tmp_path, legacy):
    path = tmp_path / "parallel.db"
    if legacy:
        _legacy_database(path)
    start = threading.Barrier(2)

    class ConcurrentDB(AuditDB):
        def _conn(self):
            conn = super()._conn()
            start.wait(timeout=10)
            return conn

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(ConcurrentDB, path) for _ in range(2)]
        for future in futures:
            assert future.result(timeout=15).db_path == path

    db = AuditDB(path)
    row_id = db.save(owner_id="alice", response="new answer")
    assert db.get_by_id(row_id, owner_id="alice") is not None
    assert db.get_by_id(row_id, owner_id="bob") is None
    if legacy:
        assert db.get_by_id(7) is not None
        assert db.get_by_id(7, owner_id="alice") is None
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)

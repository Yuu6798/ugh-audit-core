"""Database initialization waits for concurrent writers without losing records."""
from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError

import pytest

from ugh_audit.storage.audit_db import AuditDB


def _legacy_database(path):
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE audit_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL DEFAULT '', question TEXT NOT NULL DEFAULT '',
                response TEXT NOT NULL DEFAULT '', reference TEXT,
                S REAL NOT NULL DEFAULT 0, C REAL NOT NULL DEFAULT 0,
                delta_e REAL NOT NULL DEFAULT 0, quality_score REAL NOT NULL DEFAULT 5,
                verdict TEXT NOT NULL DEFAULT '', f1 REAL NOT NULL DEFAULT 0,
                f2 REAL NOT NULL DEFAULT 0, f3 REAL NOT NULL DEFAULT 0,
                f4 REAL NOT NULL DEFAULT 0, hit_rate TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            INSERT INTO audit_runs(id,response,created_at)
                VALUES (7,'preserved legacy answer','old time');
        """)


@pytest.mark.parametrize("initial_schema", ["empty", "legacy", "current"])
def test_two_workers_initialize_and_preserve_data(tmp_path, initial_schema):
    path = tmp_path / f"{initial_schema}.db"
    expected = []
    if initial_schema == "legacy":
        _legacy_database(path)
        expected = [(7, "preserved legacy answer")]
    elif initial_schema == "current":
        db = AuditDB(path)
        row_id = db.save(response="preserved current answer")
        expected = [(row_id, "preserved current answer")]

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

    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT id,response FROM audit_runs ORDER BY id").fetchall() == expected
        metric_columns = {row[1].lower(): row[3] for row in conn.execute("PRAGMA table_info(audit_runs)")}
        assert all(metric_columns[name] == 0 for name in (
            "s", "c", "delta_e", "quality_score", "f1", "f2", "f3", "f4",
        ))
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_initialization_waits_for_an_existing_writer(tmp_path):
    path = tmp_path / "writer.db"
    _legacy_database(path)
    attempting = threading.Event()

    class WaitingDB(AuditDB):
        def _conn(self):
            conn = super()._conn()
            conn.set_trace_callback(lambda sql: attempting.set() if sql.startswith("BEGIN") else None)
            return conn

    with sqlite3.connect(path) as writer, ThreadPoolExecutor(max_workers=1) as workers:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE audit_runs SET response='committed by existing writer' WHERE id=7")
        future = workers.submit(WaitingDB, path)
        try:
            assert attempting.wait(timeout=5)
            with pytest.raises(TimeoutError):
                future.result(timeout=0.2)
        finally:
            writer.commit()
        assert future.result(timeout=10).db_path == path

    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT id,response FROM audit_runs").fetchall() == [
            (7, "committed by existing writer"),
        ]

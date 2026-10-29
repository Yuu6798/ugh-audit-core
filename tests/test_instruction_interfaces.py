"""Scoped v4 contracts, persistence migration and fail-closed remote boundaries."""
from __future__ import annotations

import copy
import json
import sqlite3

import pytest
import test_semantic_interfaces as shared_interfaces

import audit as python_audit
from ugh_audit import mcp_server, server
from ugh_audit.semantic_matching import DECISION_SCOPE, METRICS_SCOPE, assess_requirements
from ugh_audit.storage.audit_db import AuditDB, guard_history_row
from ugh_calculator import Evidence
from test_semantic_interfaces import (
    ANSWER, META, ControlledJudge, fake_remote, install_judge, remote_frame,
)

infrastructure = shared_interfaces.infrastructure


@pytest.mark.parametrize("channel", ["rest", "mcp"])
def test_complete_requirements_override_advisory_structural_rejection(
    infrastructure, monkeypatch, channel,
):
    client, db = infrastructure
    install_judge(monkeypatch, ("fulfilled",) * 3)

    def structural_failure(qid, answer, meta):
        return Evidence(question_id=qid, f1_anchor=1.0, f3_operator=1.0,
                        f4_premise=0.0, propositions_total=3, propositions_hit=0,
                        hit_ids=[], miss_ids=[0, 1, 2])

    monkeypatch.setattr(server, "_detect", structural_failure)
    monkeypatch.setattr(mcp_server, "_detect", structural_failure)
    payload = dict(question="Q", response=ANSWER, question_meta=META)
    result = (client.post("/api/audit", json=payload).json() if channel == "rest"
              else vars(mcp_server.audit_answer(**payload)))
    assert result["verdict"] == "accept"
    assert result["C"] == 1.0 and result["hit_rate"] == "3/3"
    assert result["S"] is result["delta_e"] is result["quality_score"] is None
    assert result["legacy_metrics"]["verdict"] == "regenerate"
    assert result["legacy_metrics"]["C"] == 0.0
    assert result["structural_gate"]["binding"] is False
    assert result["structural_gate"]["f1"] == 1.0
    assert db.get_by_id(result["saved_id"])["verdict"] == "accept"


@pytest.mark.parametrize("failure", ["unavailable", "throws"])
def test_unavailable_diagnostics_do_not_erase_valid_coverage(infrastructure, monkeypatch, failure):
    client, db = infrastructure
    install_judge(monkeypatch, ("fulfilled",) * 3)
    if failure == "unavailable":
        monkeypatch.setattr(server, "_HAS_DETECTOR", False)
    else:
        def broken(*args):
            raise RuntimeError("diagnostic failure")
        monkeypatch.setattr(server, "_detect", broken)
    result = client.post("/api/audit", json=dict(
        question="Q", response=ANSWER, question_meta=META,
    )).json()
    assert result["verdict"] == "accept" and result["C"] == 1.0
    assert all(result["structural_gate"][name] is None for name in ("f1", "f2", "f3", "f4"))
    with db._conn() as conn:
        raw = conn.execute("SELECT S,C,delta_e,quality_score,f1,f2,f3,f4 FROM audit_runs").fetchone()
    assert raw == (None, 1.0, None, None, None, None, None, None)
    assert db.get_by_id(result["saved_id"])["verdict"] == "accept"


@pytest.mark.parametrize("statuses,expected", [
    (("fulfilled",) * 3, ["STOP_REWRITE"]),
    (("missing", "fulfilled", "fulfilled"), ["ADD_PROPOSITION", "STOP_REWRITE"]),
    (("unknown", "fulfilled", "fulfilled"), ["STOP_REWRITE"]),
])
def test_python_repairs_are_derived_only_from_validated_missing_requirements(monkeypatch, statuses, expected):
    def bad_legacy(qid, answer, meta):
        return Evidence(question_id=qid, f1_anchor=1, f3_operator=1,
                        f4_premise=0, propositions_total=3, propositions_hit=0)
    monkeypatch.setattr(python_audit, "detect", bad_legacy)
    result = python_audit.audit("q", ANSWER, META, semantic_judge=ControlledJudge(statuses))
    assert result["policy"]["repair_order"] == expected
    assert result["state"]["S"] is result["state"]["delta_e"] is result["state"]["quality_score"] is None
    assert result["state"]["C_bin"] is result["state"]["delta_e_bin"] is None
    assert result["decision_scope"] == DECISION_SCOPE
    assert result["metrics_scope"] == METRICS_SCOPE


def test_old_v3_complete_history_is_not_reclassified_as_current_measurement(tmp_path):
    db = AuditDB(tmp_path / "old.db")
    assessment = assess_requirements(META, ANSWER, ControlledJudge(("fulfilled",) * 3))
    row_id = db.save(response=ANSWER, S=1, C=1, delta_e=0, quality_score=5,
                     verdict="regenerate", schema_version="3.0.0", question_meta=META,
                     semantic_matching=assessment, requirement_verdict="complete")
    first = db.get_by_id(row_id)
    second = guard_history_row(first)
    assert first["verdict"] == second["verdict"] == "degraded"
    assert first["C"] is second["C"] is None
    assert first["legacy_metrics"]["verdict"] == "regenerate"
    with db._conn() as conn:
        assert conn.execute("SELECT schema_version,verdict,S,C FROM audit_runs").fetchone() == (
            "3.0.0", "regenerate", 1.0, 1.0,
        )


def test_saved_current_proof_is_revalidated_against_saved_answer(tmp_path):
    db = AuditDB(tmp_path / "current.db")
    frame = remote_frame()
    row_id = db.save(response=ANSWER, C=1, verdict="accept", schema_version="4.0.0",
                     decision_scope=DECISION_SCOPE, metrics_scope=METRICS_SCOPE,
                     question_meta=META, semantic_matching=frame["semantic_matching"],
                     legacy_metrics=frame["legacy_metrics"], structural_gate=frame["structural_gate"])
    assert db.get_by_id(row_id)["verdict"] == "accept"
    with db._conn() as conn:
        conn.execute("UPDATE audit_runs SET response='different answer' WHERE id=?", (row_id,))
    assert db.get_by_id(row_id)["verdict"] == "degraded"


def test_nullable_migration_preserves_rows_constraints_indexes_triggers_and_sequence(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.executescript('''
            CREATE TABLE audit_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL DEFAULT '', question TEXT NOT NULL DEFAULT '',
                response TEXT NOT NULL DEFAULT '' UNIQUE, reference TEXT,
                "S" REAL CONSTRAINT "s not null" NOT NULL ON CONFLICT ABORT DEFAULT 0,
                [C] REAL NOT NULL DEFAULT 0,
                delta_e REAL NOT NULL DEFAULT 0, quality_score REAL NOT NULL DEFAULT 5,
                verdict TEXT NOT NULL DEFAULT '',
                f1 REAL NOT NULL DEFAULT 0, f2 REAL NOT NULL DEFAULT 0,
                f3 REAL NOT NULL DEFAULT 0, f4 REAL NOT NULL DEFAULT 0,
                hit_rate TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT 'NOT NULL, literal' CHECK (instr(note, 'NOT NULL') > 0)
            );
            CREATE INDEX metric_positive ON audit_runs(question) WHERE S > 0;
            CREATE VIEW saved_ids AS SELECT id FROM audit_runs;
            CREATE TABLE saved_log(id INTEGER);
            CREATE TRIGGER record_saved AFTER INSERT ON audit_runs
                BEGIN INSERT INTO saved_log VALUES (new.id); END;
            INSERT INTO audit_runs(id,response,S,C,delta_e,quality_score,verdict,created_at)
                VALUES (7,'old',0.9,0.8,0.2,4.2,'rewrite','old-time');
            INSERT INTO audit_runs(id,response,created_at) VALUES (100,'deleted','later');
            DELETE FROM audit_runs WHERE id=100;
        ''')
        before = conn.execute("SELECT * FROM audit_runs WHERE id=7").fetchone()
        old_columns = [row[1] for row in conn.execute("PRAGMA table_info(audit_runs)")]
    db = AuditDB(path)
    row_id = db.save(response="new", S=None, C=None, delta_e=None, quality_score=None,
                     f1=None, f2=None, f3=None, f4=None, verdict="degraded")
    assert row_id == 101
    with sqlite3.connect(path) as conn:
        quoted = ",".join('"' + name + '"' for name in old_columns)
        assert conn.execute(f"SELECT {quoted} FROM audit_runs WHERE id=7").fetchone() == before
        nullable = {row[1].lower(): row[3] for row in conn.execute("PRAGMA table_info(audit_runs)")}
        assert all(nullable[name] == 0 for name in ("s", "c", "delta_e", "quality_score", "f1", "f2", "f3", "f4"))
        assert nullable["note"] == 1
        assert conn.execute("SELECT note FROM audit_runs WHERE id=101").fetchone()[0] == "NOT NULL, literal"
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='metric_positive'").fetchone()
        assert conn.execute("SELECT id FROM saved_ids ORDER BY id").fetchall() == [(7,), (101,)]
        assert conn.execute("SELECT id FROM saved_log ORDER BY rowid").fetchall() == [(7,), (100,), (101,)]
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE audit_runs SET note='bad' WHERE id=101")
    with pytest.raises(sqlite3.IntegrityError):
        db.save(response="new")
    AuditDB(path)  # migration remains idempotent


def test_failed_nullable_migration_rolls_back_without_losing_the_original_schema(tmp_path):
    path = tmp_path / "migration-error.db"
    with sqlite3.connect(path) as conn:
        conn.executescript('''
            CREATE TABLE audit_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT DEFAULT '',
                question TEXT DEFAULT '', response TEXT DEFAULT '', reference TEXT,
                S REAL NOT NULL DEFAULT 0, C REAL NOT NULL DEFAULT 0,
                delta_e REAL NOT NULL DEFAULT 0, quality_score REAL NOT NULL DEFAULT 5,
                verdict TEXT DEFAULT '', created_at TEXT NOT NULL
            );
            CREATE INDEX original_index ON audit_runs(response);
            INSERT INTO audit_runs(response,created_at) VALUES ('keep','time');
            CREATE TABLE audit_runs_nullable_v4(unrelated TEXT);
            INSERT INTO audit_runs_nullable_v4 VALUES ('keep this too');
        ''')
        original = conn.execute("SELECT sql FROM sqlite_master WHERE name='audit_runs'").fetchone()[0]
    with pytest.raises(sqlite3.OperationalError):
        AuditDB(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT sql FROM sqlite_master WHERE name='audit_runs'").fetchone()[0] == original
        assert conn.execute("SELECT id,response FROM audit_runs").fetchall() == [(1, "keep")]
        assert conn.execute("SELECT unrelated FROM audit_runs_nullable_v4").fetchall() == [("keep this too",)]
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='original_index'").fetchone()


@pytest.mark.parametrize("body,status", [
    (None, 500), (b'{"verdict":"accept","verdict":"accept"}', 200),
    (b'{"C":NaN}', 200), (b'{"C":1e999}', 200), (b"[", 200),
    (b" " * (2 * 1024 * 1024 + 1), 200),
])
def test_remote_invalid_transport_or_json_cannot_reuse_embedded_complete_proof(monkeypatch, body, status):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "offline-test-provider-key")
    monkeypatch.setenv("UGH_REMOTE_TOKEN", "offline-test-invite-token")
    complete = json.dumps(remote_frame(), ensure_ascii=False).encode()
    calls = []

    class Response:
        def __init__(self, value, response_status):
            self.value, self.status = value, response_status
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, size=-1):
            value = self.value
            return value if size < 0 else value[:size]

    def remote_open(request, timeout, *, forward_provider_key=False):
        calls.append((request.get_method(), request.full_url, forward_provider_key))
        if request.full_url.endswith("/api/capabilities"):
            return Response(json.dumps(mcp_server.BYOK_CAPABILITIES).encode(), 200)
        return Response(complete if body is None else body, status)

    monkeypatch.setattr(mcp_server, "_remote_open", remote_open)
    result = mcp_server._proxy_audit("https://example.invalid", question="Q", response=ANSWER, question_meta=META)
    assert calls == [
        ("GET", "https://example.invalid/api/capabilities", False),
        ("POST", "https://example.invalid/api/audit", True),
    ]
    assert result.verdict == "degraded" and result.C is None
    assert result.saved_id is None and result.is_reliable is False


def test_remote_generated_requirements_are_exposed_and_revalidated(monkeypatch):
    frame = remote_frame()
    frame["metadata_source"] = "llm_generated"
    frame["mode"] = "computed_ai_draft"
    fake_remote(monkeypatch, frame)
    result = mcp_server._proxy_audit("https://example.invalid", question="Q", response=ANSWER,
                                    auto_generate_meta=True)
    assert result.verdict == "accept" and result.requires_review is True
    assert result.assessment_context == META
    del frame["assessment_context"]
    fake_remote(monkeypatch, frame)
    assert mcp_server._proxy_audit("https://example.invalid", question="Q", response=ANSWER).verdict == "degraded"


@pytest.mark.parametrize("location", ["coverage", "legacy", "flag"])
def test_remote_extreme_integer_metrics_fail_closed_without_numeric_overflow(monkeypatch, location):
    frame = remote_frame()
    if location == "coverage":
        frame["C"] = 10 ** 4000
    elif location == "legacy":
        frame["legacy_metrics"]["S"] = 10 ** 4000
    else:
        frame["structural_gate"]["f1"] = 10 ** 4000
    fake_remote(monkeypatch, frame)
    result = mcp_server._proxy_audit("https://example.invalid", question="Q", response=ANSWER, question_meta=META)
    assert result.verdict == "degraded" and result.C is None


def test_history_extreme_integer_diagnostic_cannot_crash_or_bind_the_semantic_verdict():
    frame = remote_frame()
    row = {**frame, "id": 1, "question": "Q", "response": ANSWER, "created_at": "time",
           "question_meta": META, "legacy_metrics": None, "f1": 10 ** 4000,
           "f2": 0, "f3": 0, "f4": 0}
    row.pop("structural_gate")
    result = server._row_to_history(row)
    assert result.verdict == "accept" and result.C == 1.0
    assert result.f1 is None and result.structural_gate["f1"] is None
    assert result.legacy_metrics["structural_gate"]["f1"] == 10 ** 4000


@pytest.mark.parametrize("legacy_change", [{}, {"verified_total": 2, "unverified_total": 0}])
def test_remote_old_aggregates_cannot_claim_scoped_verified_statistics(monkeypatch, legacy_change):
    frame = {"session_id": "old", "total": 2, "avg_delta_e": 0.0, "avg_quality_score": 5.0,
             **legacy_change}
    fake_remote(monkeypatch, copy.deepcopy(frame))
    result = mcp_server._proxy_get("https://example.invalid", "/api/session/old")
    assert result["verified_total"] == 0 and result["unverified_total"] == 2
    assert result["avg_S"] is result["avg_delta_e"] is result["avg_quality_score"] is None
    assert result["legacy_summary"] == frame

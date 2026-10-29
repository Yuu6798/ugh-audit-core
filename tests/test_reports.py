"""Reports preserve schema 4 proof, unknown coverage, and CSV cell boundaries."""
from __future__ import annotations

import copy
import csv
import io
import json
from pathlib import Path
import runpy
import sqlite3

import pytest

from ugh_audit import AuditDB, generate_csv, generate_text_report
from ugh_audit.semantic_matching import MODEL, apply_semantic_gate, assess_requirements


ANSWER = "原本を提出し、写しは提出しないでください。"
META = {"question": "提出方法を案内してください。",
        "core_propositions": ["原本を提出する", "写しは提出しない"]}


def current_row(*statuses: str, metadata_source: str = "inline") -> dict:
    remaining = iter(statuses or ("fulfilled", "fulfilled"))

    def judge(requirement, answer, *, timeout_seconds):
        status = next(remaining)
        value = {"status": status, "missing_kind": "absence" if status == "missing" else None,
                 "evidence": [answer] if status == "fulfilled" else [], "reason": "test proof"}
        return {**value, "unknown_source": "model" if status == "unknown" else None,
                "api_called": True, "requested_model": MODEL, "returned_model": MODEL,
                "stop_reason": "end_turn", "raw_response": json.dumps(value, ensure_ascii=False)}

    assessment = assess_requirements(META, ANSWER, judge)
    return apply_semantic_gate({
        "created_at": "2026-10-29T10:00:00Z", "schema_version": "4.0.0",
        "metadata_source": metadata_source, "question_meta": copy.deepcopy(META),
        "response": ANSWER,
    }, assessment, response=ANSWER, question_meta=META)


def csv_rows(history: list[dict]) -> list[dict]:
    return list(csv.DictReader(io.StringIO(generate_csv(history), newline="")))


def test_empty_reports_are_valid_and_do_not_invent_metrics():
    assert generate_text_report([]) == "データなし"
    assert csv_rows([]) == []
    assert "requirement_verdict" in next(csv.reader(io.StringIO(generate_csv([]))))


@pytest.mark.parametrize(("statuses", "verdict", "coverage", "requirement", "review"), [
    (("fulfilled", "fulfilled"), "accept", "1.0", "complete", "false"),
    (("fulfilled", "missing"), "rewrite", "0.5", "incomplete", "true"),
    (("missing", "missing"), "rewrite", "0.0", "incomplete", "true"),
    (("fulfilled", "unknown"), "degraded", "null", "undetermined", "true"),
])
def test_verified_current_rows_have_coverage_not_legacy_quality(
    statuses, verdict, coverage, requirement, review,
):
    row = current_row(*statuses)
    original = copy.deepcopy(row)
    report = generate_text_report([row])
    exported, = csv_rows([row])
    assert row == original
    assert verdict in report
    assert "品質の保証ではありません" in report
    assert "未確定" in report if coverage == "null" else f"{float(coverage):.3f}" in report
    assert exported["verdict"] == verdict
    assert exported["C"] == coverage
    assert exported["requirement_verdict"] == requirement
    assert exported["requires_review"] == review
    assert exported["schema_version"] == exported["source_schema_version"] == "4.0.0"
    assert exported["decision_scope"] == "instruction_coverage_v2"
    assert exported["metrics_scope"] == "semantic_coverage_only"
    assert [exported[name] for name in ("S", "delta_e", "quality_score")] == ["null"] * 3


def test_unknown_rows_remain_in_total_without_a_misleading_average():
    report = generate_text_report([current_row(), current_row("fulfilled", "unknown")])
    assert "総計: 2 件" in report
    assert "照合確定: 1 / 2 件" in report
    assert "保留・未検証: 1 件" in report
    assert "未確定行を除いた平均充足率は計算しません" in report
    assert "平均 ΔE" not in report
    assert "平均 quality" not in report


def test_ai_draft_complete_still_requires_review():
    row = current_row(metadata_source="llm_generated")
    exported, = csv_rows([row])
    assert exported["verdict"] == "accept"
    assert exported["requires_review"] == "true"
    assert "必要" in generate_text_report([row])


def test_mixed_legacy_and_current_history_does_not_promote_old_accept():
    old = {"schema_version": "3.0.0", "S": 0.7, "C": 0.8, "delta_e": 0.2,
           "quality_score": 4.2, "verdict": "accept", "created_at": None}
    old_csv, new_csv = csv_rows([old, current_row()])
    assert old_csv["verdict"] == "degraded"
    assert old_csv["C"] == "null"
    assert old_csv["legacy_verdict"] == "accept"
    assert old_csv["legacy_C"] == "0.8"
    assert old_csv["legacy_quality_score"] == "4.2"
    assert old_csv["source_schema_version"] == "3.0.0"
    assert old_csv["created_at"] == "null"
    assert new_csv["verdict"] == "accept"
    report = generate_text_report([old, current_row()])
    assert "照合確定: 1 / 2 件" in report
    assert "旧数値は非拘束の診断" in report
    assert "4.200" not in report


@pytest.mark.parametrize("row", [None, {}, {"verdict": "accept", "C": 1},
                                     {"C": None, "S": None, "delta_e": None,
                                      "quality_score": None, "verdict": "accept"}])
def test_partial_or_null_history_is_unverified_instead_of_crashing(row):
    exported, = csv_rows([row])
    assert exported["verdict"] == "degraded"
    assert exported["C"] == "null"
    assert "照合確定: 0 / 1 件" in generate_text_report([row])


def test_csv_quotes_commas_newlines_and_quotes_without_changing_cell_boundaries():
    timestamp = 'October 29, "10:00"\nsecond line'
    row = current_row()
    row["created_at"] = timestamp
    exported, = csv_rows([row])
    assert exported["created_at"] == timestamp
    assert None not in exported  # Extra cells would appear under a None key.
    assert exported["verdict"] == "accept"


@pytest.mark.parametrize("formula", ["=1+1", "+SUM(A1:A2)", "-1+2", "@SUM(1)",
                                      " \t=1+1", "\tplain", "\rplain", "\nplain"])
def test_csv_neutralizes_untrusted_formula_cells_but_keeps_numeric_values(formula):
    row = {"created_at": formula, "schema_version": formula, "verdict": formula,
           "delta_e": -0.25, "S": 1}
    exported, = csv_rows([row])
    assert exported["created_at"] == "'" + formula
    assert exported["source_schema_version"] == "'" + formula
    assert exported["legacy_verdict"] == "'" + formula
    assert exported["legacy_delta_e"] == "-0.25"
    assert exported["legacy_S"] == "1"


def test_nonfinite_or_malformed_legacy_numbers_are_unavailable():
    exported, = csv_rows([{"S": float("nan"), "C": float("inf"),
                           "delta_e": "=1+1", "quality_score": 10 ** 1000}])
    assert [exported["legacy_" + name] for name in ("S", "C", "delta_e", "quality_score")] == [
        "null", "null", "null", "null",
    ]


def test_example_persists_nullable_current_metrics_and_verifiable_context(tmp_path):
    example_path = Path(__file__).resolve().parents[1] / "examples" / "basic_audit.py"
    example = runpy.run_path(str(example_path))
    row = current_row()
    result = {**row, "state": {name: row[name] for name in ("S", "C", "delta_e", "quality_score")},
              "assessment_context": META}
    db = AuditDB(tmp_path / "example.db")
    assert example["_save_result"](db, result, META, ANSWER) is not None
    saved, = db.drift_history()
    exported, = csv_rows([saved])
    assert exported["verdict"] == "accept"
    assert exported["C"] == "1.0"
    assert [saved[name] for name in ("S", "delta_e", "quality_score")] == [None] * 3
    assert saved["source_schema_version"] == "4.0.0"
    assert example["_save_result"](db, {"verdict": "degraded"}, META, ANSWER) is None
    assert len(db.drift_history()) == 1


def test_report_handles_migrated_legacy_database_and_new_null_metrics(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute("""CREATE TABLE audit_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL DEFAULT '', question TEXT NOT NULL DEFAULT '',
            response TEXT NOT NULL DEFAULT '', reference TEXT,
            S REAL NOT NULL DEFAULT 1, C REAL NOT NULL DEFAULT 1,
            delta_e REAL NOT NULL, quality_score REAL NOT NULL DEFAULT 5,
            verdict TEXT NOT NULL DEFAULT 'accept', created_at TEXT NOT NULL
        )""")
        connection.execute("INSERT INTO audit_runs(delta_e, created_at) VALUES (0, '2000-01-01')")
    db = AuditDB(path)
    example_path = Path(__file__).resolve().parents[1] / "examples" / "basic_audit.py"
    example = runpy.run_path(str(example_path))
    row = current_row()
    result = {**row, "state": {name: row[name] for name in ("S", "C", "delta_e", "quality_score")},
              "assessment_context": META}
    example["_save_result"](db, result, META, ANSWER)
    history = db.drift_history()
    exported = csv_rows(history)
    assert len(exported) == 2
    current, = [row for row in exported if row["source_schema_version"] == "4.0.0"]
    legacy, = [row for row in exported if row["source_schema_version"] == ""]
    assert current["verdict"] == "accept"
    assert current["S"] == current["delta_e"] == current["quality_score"] == "null"
    assert legacy["verdict"] == "degraded"
    assert legacy["legacy_verdict"] == "accept"
    assert legacy["C"] == "null"
    assert "照合確定: 1 / 2 件" in generate_text_report(history)

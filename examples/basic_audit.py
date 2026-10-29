"""Explicit instruction coverage, schema 4 persistence, and report demonstration.

With no configured semantic checker, all examples remain degraded and unsaved.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audit import audit
from ugh_audit import AuditDB
from ugh_audit.report.phase_map import generate_text_report

DB_PATH = Path(__file__).resolve().parent.parent / ".tmp" / "example_audit.db"
SESSION_ID = "example-session-01"

CASES = [
    {
        "question_id": "example-send",
        "question_meta": {
            "question": "申請書の送付方法を案内してください。",
            "core_propositions": ["申請書を金曜日までに担当者へ送付する"],
        },
        "response_text": "申請書を金曜日までに担当者へ送付してください。",
    },
    {
        "question_id": "example-deadline",
        "question_meta": {
            "question": "申請書の送付方法を案内してください。",
            "core_propositions": ["申請書を金曜日までに担当者へ送付する"],
        },
        "response_text": "申請書を担当者へ送付してください。",
    },
    {
        "question_id": "example-prohibition",
        "question_meta": {
            "question": "提出時の注意事項を案内してください。",
            "core_propositions": ["原本を提出する", "写しは提出しない"],
        },
        "response_text": "原本を提出してください。写しは提出しないでください。",
    },
]


def _fmt(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{float(value):.4f}"
    return "None"


def _save_result(db: AuditDB, result: dict, question_meta: dict, response: str) -> int | None:
    """Persist current values and proof together; never fill null metrics with zero."""
    if result["verdict"] == "degraded":
        return None
    state, gate = result["state"], result["structural_gate"]
    return db.save(
        session_id=SESSION_ID,
        question=question_meta["question"], response=response,
        S=state["S"], C=state["C"], delta_e=state["delta_e"],
        quality_score=state["quality_score"], verdict=result["verdict"],
        f1=gate.get("f1"), f2=gate.get("f2"), f3=gate.get("f3"), f4=gate.get("f4"),
        hit_rate=result["hit_rate"], hit_sources=json.dumps(result["hit_sources"]),
        metadata_source="inline", semantic_matching=result["semantic_matching"],
        requirement_verdict=result["requirement_verdict"],
        requires_review=result["requires_review"], legacy_metrics=result["legacy_metrics"],
        schema_version=result["schema_version"], decision_scope=result["decision_scope"],
        metrics_scope=result["metrics_scope"], question_meta=result["assessment_context"],
        structural_gate=gate, mode=result["mode"], is_reliable=result["is_reliable"],
    )


def main() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
    db = AuditDB(db_path=DB_PATH)

    print("UGH Audit Core - explicit instruction coverage demo\n")

    for i, case in enumerate(CASES, start=1):
        qid = case["question_id"]
        response_text = case["response_text"]
        question_meta = case["question_meta"]

        result = audit(qid, response_text, question_meta)
        state = result["state"]
        verdict = result["policy"]["decision"]

        s = state.get("S")
        c = state.get("C")
        delta_e = state.get("delta_e")
        quality_score = state.get("quality_score")
        saved_id = _save_result(db, result, question_meta, response_text)

        question_preview = str(question_meta.get("question", ""))[:30]
        print(
            f"[{i}] Q: {question_preview}... "
            f"S={_fmt(s)} C={_fmt(c)} ΔE={_fmt(delta_e)} "
            f"quality_score={_fmt(quality_score)} verdict={verdict} saved_id={saved_id}"
        )

    history = db.drift_history(limit=50)
    print("\n" + generate_text_report(history))

    summary = db.session_summary(SESSION_ID)
    print(f"\nsession_summary({SESSION_ID}): {summary}")


if __name__ == "__main__":
    main()

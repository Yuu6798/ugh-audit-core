"""examples/basic_audit.py
detect → calculate → decide パイプラインの最小デモ。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audit import _load_question_meta, audit
from ugh_audit import AuditDB
from ugh_audit.report.phase_map import generate_text_report

DATA_PATH = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "question_sets"
    / "ugh-audit-100q-v3-1.jsonl"
)
DB_PATH = Path(__file__).resolve().parent.parent / ".tmp" / "example_audit.db"
SESSION_ID = "example-session-01"

CASES = [
    {
        "question_id": "q001",
        "response_text": (
            "PoRは共鳴度を示す指標ですが、PoRが高いだけで誠実性は保証されません。"
            "表層的な語彙一致でもPoRが高く出るため、結論は誤る可能性があります。"
            "だからこそΔEやgrvを含む複合評価で判断する必要があります。"
        ),
    },
    {
        "question_id": "q001",
        "response_text": "PoRが高ければ誠実です。以上。",
    },
    {
        "question_id": "q002",
        "response_text": (
            "grvは回答内の語彙の偏在を検出する指標です。"
            "例えば安全性フレーズばかり繰り返す回答では語彙が一部に集中します。"
            "その結果、問いの核心が空洞化するパターンを捉えられます。"
        ),
    },
]


def _fmt(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{float(value):.4f}"
    return "None"


def _as_float(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _load_meta(question_id: str) -> dict:
    meta = _load_question_meta(str(DATA_PATH), question_id)
    if meta is None:
        raise RuntimeError(f"question_id '{question_id}' not found: {DATA_PATH}")
    return meta


def main() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
    db = AuditDB(db_path=DB_PATH)

    print("UGH Audit Core - basic pipeline demo\n")

    for i, case in enumerate(CASES, start=1):
        qid = case["question_id"]
        response_text = case["response_text"]
        question_meta = _load_meta(qid)

        result = audit(qid, response_text, question_meta)
        evidence = result["evidence"]
        state = result["state"]
        verdict = result["policy"]["decision"]

        s = state.get("S")
        c = state.get("C")
        delta_e = state.get("delta_e")
        quality_score = state.get("quality_score")
        propositions_hit = int(evidence.get("propositions_hit", 0) or 0)
        propositions_total = int(evidence.get("propositions_total", 0) or 0)
        hit_rate = (
            f"{propositions_hit}/{propositions_total}"
            if propositions_total > 0 else ""
        )

        saved_id = None
        if verdict != "degraded":
            saved_id = db.save(
                session_id=SESSION_ID,
                question=str(question_meta.get("question", "")),
                response=response_text,
                reference=None,
                S=_as_float(s),
                C=_as_float(c),
                delta_e=_as_float(delta_e),
                quality_score=_as_float(quality_score),
                verdict=str(verdict),
                f1=_as_float(evidence.get("f1_anchor")),
                f2=_as_float(evidence.get("f2_unknown")),
                f3=_as_float(evidence.get("f3_operator")),
                f4=_as_float(evidence.get("f4_premise")),
                hit_rate=hit_rate,
                metadata_source="inline",
            )

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

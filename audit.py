"""audit.py — エンドツーエンド統合 + CLI

detect → calculate → decide と必須v2意味照合のパイプライン。
CLIとしても動作する。
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from typing import Callable, Optional

from ugh_calculator import calculate
from detector import detect
from decider import _compute_budget, _load_opcodes
from ugh_audit.pipeline import run_audit
from ugh_audit.semantic_matching import make_judge_from_env, _json_safe


_DEFAULT_JUDGE = object()


def audit(
    question_id: str, response_text: str, question_meta: dict,
    *, semantic_judge: Optional[Callable] = _DEFAULT_JUDGE,
) -> dict:
    """Run the same required semantic gate used by REST and MCP.

    Credentials come from server environment only. An unavailable checker is
    undetermined, never a fallback to an unverified legacy accept. Explicit
    checker injection is an internal Python testing/embedding interface.
    """
    detected = []

    def detect_once(qid, answer, meta):
        value = detect(qid, answer, meta)
        detected.append(value)
        return value

    judge = make_judge_from_env() if semantic_judge is _DEFAULT_JUDGE else semantic_judge
    meta = {**question_meta, "id": question_id} if isinstance(question_meta, dict) else {"id": question_id}
    output = run_audit(
        question=meta.get("question", ""), response=response_text, reference=None,
        question_meta=meta, session_id=None, detect_fn=detect_once, semantic_judge=judge,
    )
    if detected:
        evidence = detected[0]
    else:
        from ugh_calculator import Evidence
        evidence = Evidence(question_id=question_id, f4_premise=None)
    legacy_errors = []
    try:
        visible_state = asdict(calculate(evidence))
    except Exception as exc:
        visible_state = {"por_state": "inactive", "grv": None, "grv_tag": "none"}
        legacy_errors.append("python_legacy_state_error:" + type(exc).__name__)
    try:
        visible_evidence = asdict(evidence)
        if isinstance(visible_evidence.get("hit_sources"), dict):
            visible_evidence["hit_sources"] = {
                str(key): value for key, value in visible_evidence["hit_sources"].items()
            }
    except Exception as exc:
        visible_evidence = None
        legacy_errors.append("python_legacy_evidence_error:" + type(exc).__name__)
    if legacy_errors:
        output["legacy_metrics"] = dict(output["legacy_metrics"])
        output["legacy_metrics"]["errors"] = (
            list(output["legacy_metrics"].get("errors") or []) + legacy_errors
        )
    decision = output["verdict"]
    order = (["ADD_PROPOSITION"] * output["semantic_matching"]["missing_count"]
             if decision == "rewrite" else [])
    order.append("STOP_REWRITE")
    for field in ("S", "C", "delta_e", "quality_score"):
        visible_state[field] = output[field]
    visible_state["C_bin"] = None
    visible_state["delta_e_bin"] = None
    return _json_safe({
        "evidence": visible_evidence,
        "state": visible_state,
        "policy": {"decision": decision, "repair_order": order},
        "budget": _compute_budget(order, _load_opcodes()),
        "schema_version": output["schema_version"],
        "decision_scope": output["decision_scope"],
        "metrics_scope": output["metrics_scope"],
        "assessment_context": output.get("_question_meta"),
        "structural_gate": output["structural_gate"],
        "hit_rate": output["hit_rate"],
        "hit_sources": output.get("hit_sources"),
        "verdict": decision,
        "mode": output["mode"],
        "is_reliable": output["is_reliable"],
        "requirement_verdict": output["requirement_verdict"],
        "requires_review": output["requires_review"],
        "semantic_matching": output["semantic_matching"],
        "legacy_metrics": output["legacy_metrics"],
    })


def _load_question_meta(data_path: str, question_id: str) -> Optional[dict]:
    """JONLファイルから question_id に一致するメタデータを取得する"""
    with open(data_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("id") == question_id:
                return record
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="UGH Audit Engine — AI回答の意味論的監査",
    )
    parser.add_argument(
        "--question-id",
        required=True,
        help="問題ID（例: q001）",
    )
    parser.add_argument(
        "--response",
        default=None,
        help="AIの回答テキスト（省略時はstdinから読み取り）",
    )
    parser.add_argument(
        "--data",
        required=True,
        help="メタデータJSONLファイルのパス",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="JSON出力を整形する",
    )

    args = parser.parse_args()

    # メタデータの読み込み
    question_meta = _load_question_meta(args.data, args.question_id)
    if question_meta is None:
        print(
            f"Error: question_id '{args.question_id}' not found in {args.data}",
            file=sys.stderr,
        )
        sys.exit(1)

    # 回答テキストの取得
    if args.response is not None:
        response_text = args.response
    else:
        response_text = sys.stdin.read().strip()

    if not response_text:
        print("Error: response text is empty", file=sys.stderr)
        sys.exit(1)

    # 監査実行
    result = audit(args.question_id, response_text, question_meta)

    # JSON出力
    indent = 2 if args.pretty else None
    print(json.dumps(result, ensure_ascii=False, indent=indent))


if __name__ == "__main__":
    main()

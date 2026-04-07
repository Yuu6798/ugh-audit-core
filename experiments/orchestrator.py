"""experiments/orchestrator.py
Claude × Codex オーケストレーション — 自由質問メタデータ動的生成 PoC

使用方法:
    python -m experiments.orchestrator --question "AIは本当に創造性を持てるのか？"
    python -m experiments.orchestrator --question "..." --iterate 3
    python -m experiments.orchestrator --question "..." --no-codex
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# パイプライン import (既存コード、変更なし)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from experiments.meta_generator import generate_meta, improve_meta  # noqa: E402
from experiments.response_source import get_response, improve_response  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# ΔE 改善の最小値（これ以下なら改善ループを停止）
_DELTA_E_IMPROVEMENT_MIN = 0.02


def _make_question_id(question: str) -> str:
    return "free_" + hashlib.sha256(question.encode()).hexdigest()[:8]


def _run_audit(question_id: str, response_text: str, question_meta: dict) -> dict:
    """既存パイプラインを呼び出す"""
    from audit import audit  # noqa: E402
    return audit(question_id, response_text, question_meta)


def _extract_summary(audit_result: dict) -> dict:
    """ログ用に監査結果のサマリーを抽出"""
    state = audit_result.get("state", {})
    evidence = audit_result.get("evidence", {})
    policy = audit_result.get("policy", {})
    return {
        "S": round(state.get("S", 0.0), 4),
        "C": round(state["C"], 4) if state.get("C") is not None else None,
        "delta_e": round(state["delta_e"], 4) if state.get("delta_e") is not None else None,
        "quality_score": (
            round(state["quality_score"], 4)
            if state.get("quality_score") is not None
            else None
        ),
        "verdict": policy.get("decision", policy.get("verdict", "unknown")),
        "hit_ids": evidence.get("hit_ids", []),
        "miss_ids": evidence.get("miss_ids", []),
        "propositions_hit": evidence.get("propositions_hit", 0),
        "propositions_total": evidence.get("propositions_total", 0),
        "f1": evidence.get("f1_anchor", 0.0),
        "f2": evidence.get("f2_unknown", 0.0),
        "f3": evidence.get("f3_operator", 0.0),
        "f4": evidence.get("f4_premise"),
    }


def _write_log(log_dir: Path, record: dict) -> None:
    """JSONL にログを追記"""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "orchestration.jsonl"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    logger.info("ログ書き込み: %s", log_file)


def run_single(
    question: str,
    use_codex: bool = True,
    iterate: int = 0,
    log_dir: Optional[Path] = None,
) -> list[dict]:
    """1つの自由質問に対してオーケストレーションを実行

    Args:
        question: 質問テキスト
        use_codex: Codex MCP を使用するか
        iterate: 改善ループ回数 (0=ループなし)
        log_dir: ログ出力先ディレクトリ

    Returns:
        各イテレーションのログ record のリスト
    """
    if log_dir is None:
        log_dir = Path(__file__).parent / "logs"

    question_id = _make_question_id(question)
    run_id = str(uuid.uuid4())
    records = []

    # Phase 1: メタ生成
    logger.info("=== メタ生成 ===")
    meta = generate_meta(question)
    n_props = len(meta.get("core_propositions", []))
    logger.info("生成された命題数: %d, trap_type: %s", n_props, meta.get("trap_type", ""))

    if n_props == 0:
        logger.warning("命題が生成されなかった: C=None (degraded) になります")

    # Phase 2: 回答生成
    logger.info("=== 回答生成 ===")
    response_text, source = get_response(question, use_codex=use_codex)
    logger.info("回答ソース: %s, 長さ: %d 文字", source, len(response_text))

    if not response_text:
        logger.warning("回答が空: パイプラインは動作しますが結果は低品質です")

    # Phase 3: 監査実行
    logger.info("=== 監査実行 ===")
    audit_result = _run_audit(question_id, response_text, meta)
    summary = _extract_summary(audit_result)
    logger.info(
        "verdict=%s, S=%.4f, C=%s, ΔE=%s",
        summary["verdict"],
        summary["S"],
        summary["C"],
        summary["delta_e"],
    )

    # ログ記録
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "iteration": 0,
        "question": question,
        "question_id": question_id,
        "generated_meta": meta,
        "response_source": source,
        "response_text": response_text[:3000],
        "audit_result": summary,
        "improvement_context": None,
    }
    _write_log(log_dir, record)
    records.append(record)

    # Phase 4: 改善ループ
    prev_delta_e = summary.get("delta_e")
    for i in range(1, iterate + 1):
        if summary["verdict"] == "accept":
            logger.info("verdict=accept に到達: ループ終了")
            break

        if (
            prev_delta_e is not None
            and summary.get("delta_e") is not None
            and i > 1
        ):
            improvement = prev_delta_e - summary["delta_e"]
            if improvement < _DELTA_E_IMPROVEMENT_MIN:
                logger.info("ΔE 改善 %.4f < %.4f: プラトー到達、ループ終了", improvement,
                            _DELTA_E_IMPROVEMENT_MIN)
                break

        logger.info("=== 改善ループ %d/%d ===", i, iterate)
        prev_delta_e = summary.get("delta_e")

        # Claude: meta 改善（出題者として質問の品質を磨く）
        meta = improve_meta(question, meta, audit_result, response_text)
        logger.info(
            "Claude 改善後の命題数: %d",
            len(meta.get("core_propositions", [])),
        )

        # Codex: 回答改善（被監査者として回答の品質を磨く）
        response_text, source = improve_response(
            question=question,
            previous_response=response_text,
            audit_result=audit_result,
            core_propositions=meta.get("core_propositions", []),
            use_codex=use_codex,
        )
        logger.info("Codex/回答者 改善ソース: %s", source)

        # 再監査
        audit_result = _run_audit(question_id, response_text, meta)
        summary = _extract_summary(audit_result)
        logger.info(
            "verdict=%s, S=%.4f, C=%s, ΔE=%s",
            summary["verdict"],
            summary["S"],
            summary["C"],
            summary["delta_e"],
        )

        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "iteration": i,
            "question": question,
            "question_id": question_id,
            "generated_meta": meta,
            "response_source": source,
            "response_text": response_text[:3000],
            "audit_result": summary,
            "improvement_context": {
                "previous_verdict": records[-1]["audit_result"]["verdict"],
                "previous_delta_e": records[-1]["audit_result"].get("delta_e"),
            },
        }
        _write_log(log_dir, record)
        records.append(record)

    # 最終サマリー
    first = records[0]["audit_result"]
    last = records[-1]["audit_result"]
    logger.info("=== 最終結果 ===")
    logger.info("イテレーション数: %d", len(records))
    logger.info(
        "初回: verdict=%s, ΔE=%s → 最終: verdict=%s, ΔE=%s",
        first["verdict"], first.get("delta_e"),
        last["verdict"], last.get("delta_e"),
    )

    return records


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Claude × Codex オーケストレーション PoC",
    )
    parser.add_argument(
        "--question",
        required=True,
        help="監査対象の質問テキスト",
    )
    parser.add_argument(
        "--iterate",
        type=int,
        default=0,
        help="改善ループ回数 (デフォルト: 0=ループなし)",
    )
    parser.add_argument(
        "--no-codex",
        action="store_true",
        help="Codex MCP を使用しない",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path(__file__).parent / "logs",
        help="ログ出力先ディレクトリ",
    )

    args = parser.parse_args()

    records = run_single(
        question=args.question,
        use_codex=not args.no_codex,
        iterate=args.iterate,
        log_dir=args.log_dir,
    )

    # 最終結果を stdout に JSON 出力
    last = records[-1]
    output = {
        "question": last["question"],
        "verdict": last["audit_result"]["verdict"],
        "S": last["audit_result"]["S"],
        "C": last["audit_result"]["C"],
        "delta_e": last["audit_result"]["delta_e"],
        "quality_score": last["audit_result"].get("quality_score"),
        "iterations": len(records),
        "response_source": last["response_source"],
        "generated_meta": last["generated_meta"],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

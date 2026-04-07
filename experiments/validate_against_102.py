"""experiments/validate_against_102.py
102 問の手動メタ vs LLM 生成メタの比較検証

手動キュレーション済みの 102 問に対して LLM でメタを生成し、
同じ回答テキストで監査結果を比較する。

使用方法:
    python -m experiments.validate_against_102
    python -m experiments.validate_against_102 --limit 10  # 最初の10問のみ
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from experiments.meta_generator import generate_meta  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# データファイルパス
META_JSONL = ROOT / "data" / "question_sets" / "q_metadata_structural_reviewed_102q.jsonl"
RESPONSES_JSONL = ROOT / "data" / "phase_c_scored_v1_t0_only.jsonl"
OUTPUT_DIR = Path(__file__).parent / "logs"


def _load_jsonl(path: Path) -> list[dict]:
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _build_hand_meta(record: dict) -> dict:
    """JSONL レコードから detect() が消費する question_meta を構築"""
    return {
        "question": record.get("question", ""),
        "core_propositions": record.get("original_core_propositions", []),
        "disqualifying_shortcuts": record.get("original_disqualifying_shortcuts", []),
        "acceptable_variants": record.get("original_acceptable_variants", []),
        "trap_type": record.get("original_trap_type", ""),
    }


def _run_audit(question_id: str, response_text: str, question_meta: dict) -> dict:
    from audit import audit  # noqa: E402
    return audit(question_id, response_text, question_meta)


def _extract_key_metrics(audit_result: dict) -> dict:
    state = audit_result.get("state", {})
    evidence = audit_result.get("evidence", {})
    policy = audit_result.get("policy", {})
    return {
        "S": state.get("S"),
        "C": state.get("C"),
        "delta_e": state.get("delta_e"),
        "quality_score": state.get("quality_score"),
        "verdict": policy.get("decision", policy.get("verdict", "unknown")),
        "propositions_hit": evidence.get("propositions_hit", 0),
        "propositions_total": evidence.get("propositions_total", 0),
    }


def run_validation(limit: int = 0) -> dict:
    """102問比較検証を実行

    Args:
        limit: 検証する問数 (0=全件)

    Returns:
        サマリー dict
    """
    if not META_JSONL.exists():
        logger.error("メタデータファイルが見つかりません: %s", META_JSONL)
        sys.exit(1)
    if not RESPONSES_JSONL.exists():
        logger.error("回答ファイルが見つかりません: %s", RESPONSES_JSONL)
        sys.exit(1)

    meta_records = _load_jsonl(META_JSONL)
    response_records = _load_jsonl(RESPONSES_JSONL)
    responses_by_id = {r["id"]: r for r in response_records}

    if limit > 0:
        meta_records = meta_records[:limit]

    results = []
    for i, record in enumerate(meta_records):
        qid = record["id"]
        question = record.get("question", "")
        logger.info("[%d/%d] %s: %s", i + 1, len(meta_records), qid, question[:40])

        # 回答テキスト取得
        resp_record = responses_by_id.get(qid)
        if not resp_record:
            logger.warning("%s: 回答データなし、スキップ", qid)
            continue
        response_text = resp_record.get("response", "")

        # 手動メタで監査
        hand_meta = _build_hand_meta(record)
        hand_result = _run_audit(qid, response_text, hand_meta)
        hand_metrics = _extract_key_metrics(hand_result)

        # LLM メタ生成
        llm_meta = generate_meta(question)
        llm_result = _run_audit(qid, response_text, llm_meta)
        llm_metrics = _extract_key_metrics(llm_result)

        result_row = {
            "question_id": qid,
            "question": question,
            "hand_verdict": hand_metrics["verdict"],
            "llm_verdict": llm_metrics["verdict"],
            "verdict_match": hand_metrics["verdict"] == llm_metrics["verdict"],
            "hand_delta_e": hand_metrics["delta_e"],
            "llm_delta_e": llm_metrics["delta_e"],
            "hand_C": hand_metrics["C"],
            "llm_C": llm_metrics["C"],
            "hand_S": hand_metrics["S"],
            "llm_S": llm_metrics["S"],
            "hand_hit_rate": (
                f"{hand_metrics['propositions_hit']}/{hand_metrics['propositions_total']}"
            ),
            "llm_hit_rate": (
                f"{llm_metrics['propositions_hit']}/{llm_metrics['propositions_total']}"
            ),
            "llm_degraded": llm_metrics["verdict"] == "degraded",
            "trap_type_hand": hand_meta.get("trap_type", ""),
            "trap_type_llm": llm_meta.get("trap_type", ""),
            "trap_type_match": hand_meta.get("trap_type", "") == llm_meta.get("trap_type", ""),
            "llm_core_propositions": llm_meta.get("core_propositions", []),
        }
        results.append(result_row)

    # サマリー計算
    n = len(results)
    if n == 0:
        logger.warning("検証対象なし")
        return {"n": 0}

    n_degraded = sum(1 for r in results if r["llm_degraded"])
    n_verdict_match = sum(1 for r in results if r["verdict_match"])
    n_trap_match = sum(1 for r in results if r["trap_type_match"])

    # ΔE / C 相関 (scipy が利用可能な場合)
    delta_e_corr = None
    c_corr = None
    try:
        from scipy.stats import spearmanr
        hand_de = [r["hand_delta_e"] for r in results
                   if r["hand_delta_e"] is not None and r["llm_delta_e"] is not None]
        llm_de = [r["llm_delta_e"] for r in results
                  if r["hand_delta_e"] is not None and r["llm_delta_e"] is not None]
        if len(hand_de) >= 5:
            rho, p = spearmanr(hand_de, llm_de)
            delta_e_corr = {"rho": round(rho, 4), "p": round(p, 6), "n": len(hand_de)}

        hand_c = [r["hand_C"] for r in results
                  if r["hand_C"] is not None and r["llm_C"] is not None]
        llm_c = [r["llm_C"] for r in results
                 if r["hand_C"] is not None and r["llm_C"] is not None]
        if len(hand_c) >= 5:
            rho, p = spearmanr(hand_c, llm_c)
            c_corr = {"rho": round(rho, 4), "p": round(p, 6), "n": len(hand_c)}
    except ImportError:
        logger.info("scipy 未インストール: 相関計算をスキップ")

    summary = {
        "n": n,
        "degraded_count": n_degraded,
        "degraded_rate": round(n_degraded / n, 4),
        "verdict_match_count": n_verdict_match,
        "verdict_match_rate": round(n_verdict_match / n, 4),
        "trap_type_match_count": n_trap_match,
        "trap_type_match_rate": round(n_trap_match / n, 4),
        "delta_e_correlation": delta_e_corr,
        "c_correlation": c_corr,
    }

    # ログ出力
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    detail_file = OUTPUT_DIR / "validate_102_detail.jsonl"
    with open(detail_file, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    logger.info("詳細ログ: %s", detail_file)

    summary_file = OUTPUT_DIR / "validate_102_summary.json"
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    logger.info("サマリー: %s", summary_file)

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="102問比較検証")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="検証する問数 (0=全件)",
    )
    args = parser.parse_args()

    summary = run_validation(limit=args.limit)

    # 結果表示
    print("\n=== 102問検証サマリー ===")
    print(f"検証数: {summary['n']}")
    if summary["n"] == 0:
        print("検証対象なし")
        return
    print(f"degraded 数: {summary.get('degraded_count', 0)} ({summary.get('degraded_rate', 0):.1%})")
    print(
        f"verdict 一致率: {summary.get('verdict_match_count', 0)}"
        f" ({summary.get('verdict_match_rate', 0):.1%})"
    )
    print(
        f"trap_type 一致率: {summary.get('trap_type_match_count', 0)}"
        f" ({summary.get('trap_type_match_rate', 0):.1%})"
    )
    if summary.get("delta_e_correlation"):
        corr = summary["delta_e_correlation"]
        print(f"ΔE 相関: ρ={corr['rho']}, p={corr['p']}, n={corr['n']}")
    if summary.get("c_correlation"):
        corr = summary["c_correlation"]
        print(f"C 相関: ρ={corr['rho']}, p={corr['p']}, n={corr['n']}")

    # PoC 成功基準チェック
    print("\n=== PoC 成功基準チェック ===")
    degraded_ok = summary.get("degraded_rate", 1.0) == 0.0
    verdict_ok = summary.get("verdict_match_rate", 0) >= 0.6
    de_ok = (
        summary.get("delta_e_correlation", {}).get("rho", 0) >= 0.4
        if summary.get("delta_e_correlation")
        else False
    )
    print(f"  degraded 排除 (100%): {'PASS' if degraded_ok else 'FAIL'}")
    print(f"  verdict 一致 (>=60%): {'PASS' if verdict_ok else 'FAIL'}")
    print(f"  ΔE 相関 (>=0.4): {'PASS' if de_ok else 'FAIL/SKIP'}")


if __name__ == "__main__":
    main()

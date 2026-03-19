"""
scripts/rescore_phase_c.py

Phase C v0 の生回答を v1 スコアラーで再採点する。
生回答ファイルは変更しない。結果は別ファイルに出力する。

使い方:
  python scripts/rescore_phase_c.py \
    --raw phase_c_raw.jsonl \
    --questions qa_set.jsonl \
    --output phase_c_v1_results.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

# source checkout から直接実行できるよう repo root を sys.path に追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from ugh_audit.scorer import UGHScorer  # noqa: E402


def _get_grv_top(grv: dict) -> str:
    """grv辞書から最大重みのキーを返す"""
    if not grv:
        return ""
    return max(grv, key=grv.get)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase C v0 生回答を v1 スコアラーで再採点"
    )
    parser.add_argument("--raw", required=True, help="生回答JSONL")
    parser.add_argument("--questions", required=True, help="質問セットJSONL（reference/reference_core含む）")
    parser.add_argument("--output", required=True, help="出力CSV")
    args = parser.parse_args()

    # 質問セット読み込み（reference と reference_core を取得）
    questions: dict = {}
    with open(args.questions, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            q = json.loads(line)
            questions[q["id"]] = q

    scorer = UGHScorer()
    print(f"Backend: {scorer.backend}")

    results = []
    with open(args.raw, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            qid = record["id"]
            q = questions.get(qid)
            if not q:
                print(f"Warning: {qid} not found in questions, skipping")
                continue

            result = scorer.score(
                question=record["question"],
                response=record["response"],
                reference=q.get("reference", ""),
                reference_core=q.get("reference_core", ""),
            )

            results.append({
                "id": qid,
                "category": q.get("category", ""),
                "role": q.get("role", ""),
                "difficulty": q.get("difficulty", ""),
                "temperature": record.get("temperature", ""),
                "question": record["question"][:50],
                "por": round(result.por, 4),
                "por_fired": result.por_fired,
                "delta_e": round(result.delta_e, 4),
                "delta_e_core": round(result.delta_e_core, 4),
                "delta_e_full": round(result.delta_e_full, 4),
                "delta_e_summary": round(result.delta_e_summary, 4),
                "grv_top": _get_grv_top(result.grv),
                "backend": scorer.backend,
            })

    # CSV出力
    if results:
        with open(args.output, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=results[0].keys())
            writer.writeheader()
            writer.writerows(results)
        print(f"Done: {len(results)} records -> {args.output}")

        # サマリー出力
        de_core = [r["delta_e_core"] for r in results]
        de_full = [r["delta_e_full"] for r in results]
        de_summary = [r["delta_e_summary"] for r in results]
        print("\n--- ΔE Summary ---")
        print(f"delta_e_core    avg={sum(de_core)/len(de_core):.4f}")
        print(f"delta_e_full    avg={sum(de_full)/len(de_full):.4f}")
        print(f"delta_e_summary avg={sum(de_summary)/len(de_summary):.4f}")
    else:
        print("No results to output")


if __name__ == "__main__":
    main()

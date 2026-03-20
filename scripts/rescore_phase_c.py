"""
scripts/rescore_phase_c.py

Phase C v0 の生回答を v1 スコアラーで再採点する。
生回答ファイルは変更しない。結果は別ファイルに出力する。

使い方:
  python scripts/rescore_phase_c.py \
    --raw phase_c_raw.jsonl \
    --output phase_c_v1_results.csv \
    --questions qa_set.jsonl        # オプション: raw にreference未格納の場合のみ必要
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
    """grv辞書から最大重みの語彙キーを返す（内部キーを除外）"""
    if not grv:
        return ""
    filtered = {k: v for k, v in grv.items() if not k.startswith("_")}
    if not filtered:
        return ""
    return max(filtered, key=filtered.get)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase C v0 生回答を v1 スコアラーで再採点"
    )
    parser.add_argument("--raw", required=True, help="生回答JSONL")
    parser.add_argument(
        "--questions", default=None,
        help="質問セットJSONL（raw record に reference が無い場合のフォールバック）",
    )
    parser.add_argument("--output", required=True, help="出力CSV")
    args = parser.parse_args()

    # 質問セット読み込み（フォールバック用）
    questions: dict = {}
    if args.questions:
        with open(args.questions, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                q = json.loads(line)
                questions[q["id"]] = q

    scorer = UGHScorer()
    print(f"Backend: {scorer.backend}")

    # minimal backend では再採点の意味がないため早期終了
    if scorer.backend == "minimal":
        print(
            "ERROR: minimal backend が検出されました。"
            "sentence-transformers または ugh3-metrics-lib をインストールしてください。",
            file=sys.stderr,
        )
        sys.exit(1)

    results = []
    with open(args.raw, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            qid = record["id"]

            # reference/reference_core: raw record を優先、無ければ questions からフォールバック
            q = questions.get(qid, {})
            reference = record.get("reference") or q.get("reference") or None
            reference_core = record.get("reference_core") or q.get("reference_core") or None

            if not reference and not reference_core:
                print(f"Warning: {qid} に reference が無いためスキップ")
                continue
            if not reference:
                print(f"Warning: {qid} に reference 全文が無いためスキップ"
                      "（core-only では delta_e_full が不正確になる）")
                continue

            result = scorer.score(
                question=record["question"],
                response=record["response"],
                reference=reference,
                reference_core=reference_core,
            )

            results.append({
                "id": qid,
                "category": record.get("category") or q.get("category", ""),
                "role": record.get("role") or q.get("role", ""),
                "difficulty": record.get("difficulty") or q.get("difficulty", ""),
                "temperature": record.get("temperature", ""),
                "question": record["question"][:50],
                "por": round(result.por, 4),
                "por_fired": result.por_fired,
                "delta_e": round(result.delta_e, 4),
                "delta_e_core": round(result.delta_e_core, 4),
                "delta_e_full": round(result.delta_e_full, 4),
                "delta_e_summary": round(result.delta_e_summary, 4),
                "grv_top": _get_grv_top(result.grv),
                "backend": scorer.last_backend,
            })

    # CSV出力
    if results:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
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

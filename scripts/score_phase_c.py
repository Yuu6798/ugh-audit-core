#!/usr/bin/env python3
"""
score_phase_c.py — Phase C UGH指標スコアリングスクリプト

Usage:
    python3 scripts/score_phase_c.py \\
        --input ~/.ugh_audit/phase_c_v0/phase_c_raw.jsonl \\
        --output ~/.ugh_audit/phase_c_v0/phase_c_scored.jsonl \\
        --reference-field reference_core

reference-field の選択肢:
    reference_core  : 核心文1文（デフォルト、v0採点基準）
    reference       : reference全文（v1で比較検証用）
"""

import argparse
import json
import sys
from pathlib import Path


def score_records(
    records: list[dict],
    output_path: Path,
    reference_field: str,
) -> list[dict]:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from ugh_audit import UGHScorer

    scorer = UGHScorer()
    print(f"backend: {scorer.backend}", flush=True)
    print(f"スコアリング開始: {len(records)}件 (reference_field={reference_field})", flush=True)

    scored_records = []
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as out:
        for i, r in enumerate(records):
            ref = r.get(reference_field) or r.get("reference", "")
            result = scorer.score(
                question=r["question"],
                response=r["response"],
                reference=ref,
            )
            scored = {
                **r,
                "scoring_meta": {
                    "reference_field": reference_field,
                    "backend": scorer.backend,
                },
                "scores": {
                    "por": round(result.por, 4),
                    "delta_e": round(result.delta_e, 4),
                    "por_fired": result.por_fired,
                    "meaning_drift": result.meaning_drift,
                    "dominant_gravity": result.dominant_gravity,
                    "grv": result.grv,
                },
            }
            out.write(json.dumps(scored, ensure_ascii=False) + "\n")
            scored_records.append(scored)

            if (i + 1) % 50 == 0:
                print(f"  [{i+1}/{len(records)}] 完了", flush=True)

    print(f"\n完了: {output_path}")
    return scored_records


def print_summary(records: list[dict]) -> None:
    r0 = [r for r in records if r["temperature"] == 0.0]
    if not r0:
        return

    pors = [r["scores"]["por"] for r in r0]
    des = [r["scores"]["delta_e"] for r in r0]
    fired = sum(1 for r in r0 if r["scores"]["por_fired"])

    print(f"\n=== サマリー（temp=0.0, {len(r0)}件）===")
    print(f"PoR 平均: {sum(pors)/len(pors):.3f}")
    print(f"PoR 発火率: {fired}/{len(r0)} = {fired/len(r0)*100:.1f}%")
    print(f"ΔE  平均: {sum(des)/len(des):.3f}")

    from collections import Counter
    drift = Counter(r["scores"]["meaning_drift"] for r in r0)
    print("ΔE ラベル分布:")
    for label, cnt in drift.most_common():
        print(f"  {label}: {cnt}件 ({cnt/len(r0)*100:.1f}%)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase C UGH指標スコアリング")
    parser.add_argument("--input", required=True, help="入力JSONLファイルパス")
    parser.add_argument("--output", required=True, help="出力JSONLファイルパス")
    parser.add_argument(
        "--reference-field",
        default="reference_core",
        choices=["reference_core", "reference"],
        help="ΔE計算に使うreferenceフィールド",
    )
    args = parser.parse_args()

    records = []
    with open(args.input) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    print(f"入力: {len(records)}件")
    scored = score_records(records, Path(args.output), args.reference_field)
    print_summary(scored)


if __name__ == "__main__":
    main()

"""analysis/annotation_blind_check.py — ブラインド混入 |Δ| 集計

annotation_accept40.csv 内の blind_check カラムに元 HA48 id が記録された
行について、新規 O と元 O の差分を集計する。

docs/annotation_protocol.md §3.5 の合格基準:
    |Δ| 平均 ≤ 0.25 (1-5 Likert スケールで等価)
    bias |μ_Δ| ≤ 0.5

使い方:
    python analysis/annotation_blind_check.py
    python analysis/annotation_blind_check.py --acc40 path/to/annotation.csv
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
HA48_PATH = ROOT / "data" / "human_annotation_48" / "annotation_48_merged.csv"
ACC40_DEFAULT = (
    ROOT / "data" / "human_annotation_accept40" / "annotation_accept40.csv"
)

# 合格基準 (1-5 Likert スケール、設計 §3.5)
MEAN_ABS_DELTA_MAX = 1.0  # 0.25 (normalized 0-1) == 1.0 (Likert 1-5)
BIAS_ABS_MAX = 0.5


def _parse_o(raw: str) -> Optional[int]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        v = int(round(float(s)))
    except ValueError:
        return None
    if v < 1 or v > 5:
        return None
    return v


def load_ha48_o_map(path: Path = HA48_PATH) -> dict:
    result = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            o = _parse_o(row.get("O", ""))
            if o is not None:
                result[row["id"]] = o
    return result


def collect_blind_pairs(
    acc40_path: Path, ha48_o: dict
) -> List[Tuple[str, int, int]]:
    """blind_check カラムから (orig_id, orig_O, new_O) のリストを返す."""
    pairs: List[Tuple[str, int, int]] = []
    if not acc40_path.exists():
        return pairs
    with open(acc40_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            orig_id = (row.get("blind_check") or "").strip()
            if not orig_id:
                continue
            new_o = _parse_o(row.get("O", ""))
            if new_o is None:
                continue
            orig_o = ha48_o.get(orig_id)
            if orig_o is None:
                continue
            pairs.append((orig_id, orig_o, new_o))
    return pairs


def summarize(
    pairs: List[Tuple[str, int, int]]
) -> dict:
    if not pairs:
        return {
            "n": 0,
            "mean_abs_delta": None,
            "bias": None,
            "pass": None,
        }
    deltas = [new - orig for _, orig, new in pairs]
    abs_deltas = [abs(d) for d in deltas]
    mean_abs = statistics.fmean(abs_deltas)
    bias = statistics.fmean(deltas)
    passed = (
        mean_abs <= MEAN_ABS_DELTA_MAX and abs(bias) <= BIAS_ABS_MAX
    )
    return {
        "n": len(pairs),
        "mean_abs_delta": mean_abs,
        "bias": bias,
        "pass": passed,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--acc40", type=Path, default=ACC40_DEFAULT)
    args = parser.parse_args(argv)

    ha48_o = load_ha48_o_map()
    pairs = collect_blind_pairs(args.acc40, ha48_o)
    summary = summarize(pairs)

    print(f"ブラインド混入: {summary['n']} 件")
    if summary["n"] == 0:
        print("(データなし)")
        return 0
    print(f"  |Δ| 平均:   {summary['mean_abs_delta']:.3f}  (基準 ≤ {MEAN_ABS_DELTA_MAX})")
    print(f"  bias μ_Δ:   {summary['bias']:+.3f}  (基準 |μ| ≤ {BIAS_ABS_MAX})")
    print(f"  判定:        {'PASS' if summary['pass'] else 'FAIL'}")
    print()
    print("詳細:")
    for orig_id, orig_o, new_o in pairs:
        mark = " " if abs(new_o - orig_o) <= 1 else "*"
        print(f"  {mark} {orig_id}: 元 O={orig_o} → 新 O={new_o}  (Δ={new_o - orig_o:+d})")
    return 0 if (summary["pass"] is True) else 1


if __name__ == "__main__":
    sys.exit(main())

"""scripts/run_cascade_poc.py — cascade Tier 2 PoC 実行

dev_cascade_20.csv を読み込み、Tier 2 を全件実行。
θ_sbert × δ_gap の grid search で最適パラメータを探索する。
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cascade_matcher import load_model, tier2_candidate


def load_dev_cascade(path: str = "data/eval/dev_cascade_20.csv") -> list[dict]:
    """dev_cascade_20.csv を読み込む。"""
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def run_tier2_all(rows: list[dict], model) -> list[dict]:
    """全件に Tier 2 を実行し、結果を付与して返す。"""
    results = []
    for row in rows:
        result = tier2_candidate(
            proposition=row["core_proposition"],
            response=row["response"],
            model=model,
        )
        results.append({**row, **result})
    return results


def print_results(results: list[dict]) -> None:
    """全件のスコアを表示。"""
    print(f"{'id':12s} {'category':16s} {'sel_cat':18s} "
          f"{'top1':>6s} {'top2':>6s} {'gap':>6s} {'pass':>5s} top1_sentence[:50]")
    print("-" * 130)
    for r in results:
        print(f"{r['id']:12s} {r['selection_category']:18s} {r['expected_result']:14s} "
              f"{r['top1_score']:6.4f} {r['top2_score']:6.4f} {r['gap']:6.4f} "
              f"{'YES' if r['pass_tier2'] else 'no':>5s} "
              f"{r['top1_sentence'][:50]}")


def print_group_stats(results: list[dict]) -> None:
    """selection_category 別のスコア分布を表示。"""
    groups = {}
    for r in results:
        cat = r["selection_category"]
        if cat not in groups:
            groups[cat] = []
        groups[cat].append(r["top1_score"])

    print(f"\n{'category':20s} {'n':>3s} {'mean':>6s} {'std':>6s} {'min':>6s} {'max':>6s}")
    print("-" * 55)
    for cat in ["concept_absent", "hard_negative", "ovl_insufficient"]:
        scores = groups.get(cat, [])
        if scores:
            arr = np.array(scores)
            print(f"{cat:20s} {len(arr):3d} {arr.mean():6.4f} {arr.std():6.4f} "
                  f"{arr.min():6.4f} {arr.max():6.4f}")


def grid_search(results: list[dict]) -> None:
    """θ_sbert × δ_gap の grid search。"""
    thetas = np.arange(0.45, 0.66, 0.05)
    deltas = np.arange(0.03, 0.11, 0.01)

    print(f"\n=== Grid Search: θ_sbert × δ_gap ===")
    print(f"{'θ':>6s} {'δ':>6s} {'rescue':>7s} {'false_r':>8s} {'prec':>6s} "
          f"{'recall':>7s} {'F1':>6s}")
    print("-" * 55)

    best_f1 = 0.0
    best_params = (0.0, 0.0)

    for theta in thetas:
        for delta in deltas:
            rescue = 0
            false_rescue = 0
            total_positive = 0

            for r in results:
                pass_t2 = (r["top1_score"] >= theta) and (r["gap"] >= delta)
                if r["expected_result"] in ("should_rescue", "may_rescue"):
                    total_positive += 1
                    if pass_t2:
                        rescue += 1
                elif r["expected_result"] == "must_reject":
                    if pass_t2:
                        false_rescue += 1

            prec = rescue / (rescue + false_rescue) if (rescue + false_rescue) > 0 else 0.0
            recall = rescue / total_positive if total_positive > 0 else 0.0
            f1 = 2 * prec * recall / (prec + recall) if (prec + recall) > 0 else 0.0

            if f1 > best_f1:
                best_f1 = f1
                best_params = (float(theta), float(delta))

            # Only print notable rows
            if rescue > 0 or false_rescue > 0:
                print(f"{theta:6.2f} {delta:6.2f} {rescue:7d} {false_rescue:8d} "
                      f"{prec:6.3f} {recall:7.3f} {f1:6.3f}")

    print(f"\nBest F1 = {best_f1:.3f} at θ={best_params[0]:.2f}, δ={best_params[1]:.2f}")


def main():
    print("Loading model...")
    model = load_model()

    print("Loading dev_cascade_20.csv...")
    rows = load_dev_cascade()
    print(f"Loaded {len(rows)} rows.\n")

    print("=== Tier 2 Results ===\n")
    results = run_tier2_all(rows, model)
    print_results(results)
    print_group_stats(results)
    grid_search(results)


if __name__ == "__main__":
    main()

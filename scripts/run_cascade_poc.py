"""scripts/run_cascade_poc.py — cascade Tier 2 + Tier 3 PoC 実行

dev_cascade_20.csv を読み込み、Tier 2 → Tier 3 フルパイプラインを実行。
θ_sbert × δ_gap の grid search + PoC 受理基準の自動判定。
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cascade_matcher import (
    load_model,
    tier2_candidate,
    run_cascade_full,
)


def load_tier1_hits(
    path: str = "data/eval/audit_102_main_baseline_v5.csv",
) -> dict[str, set[int]]:
    """ベースラインから Tier 1 hit 済みの proposition index を読み込む。

    Returns:
        {question_id: {hit_prop_idx, ...}}
    """
    hit_map: dict[str, set[int]] = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            qid = row["id"]
            hit_ids_str = row.get("hit_ids", "[]")
            # Parse "[0, 2]" style
            hit_ids_str = hit_ids_str.strip("[] ")
            if hit_ids_str:
                hit_map[qid] = {int(x.strip()) for x in hit_ids_str.split(",")}
            else:
                hit_map[qid] = set()
    return hit_map


def load_dev_cascade(path: str = "data/eval/dev_cascade_20.csv") -> list[dict]:
    """dev_cascade_20.csv を読み込む。"""
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_f4_flags(
    path: str = "data/gate_results/structural_gate_summary.csv",
    temperature: float = 0.0,
) -> dict[str, float]:
    """structural_gate_summary.csv から f4_flag を読み込む。

    Returns:
        {question_id: f4_flag} (temperature=0.0 のみ)
    """
    f4_map: dict[str, float] = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if float(row["temperature"]) == temperature:
                f4_map[row["id"]] = float(row["f4_flag"])
    return f4_map


def load_synonym_dict() -> dict[str, list[str]]:
    """detector.py の _SYNONYM_MAP を読み込む。"""
    try:
        from detector import _SYNONYM_MAP
        return _SYNONYM_MAP
    except ImportError:
        return {}


# ============================================================
# Tier 2 only (legacy)
# ============================================================

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


# ============================================================
# Tier 2 + Tier 3 フルパイプライン
# ============================================================

def run_full_pipeline(
    rows: list[dict],
    model,
    f4_map: dict[str, float],
    synonym_dict: dict[str, list[str]],
    tier1_hit_map: dict[str, set[int]] | None = None,
) -> list[dict]:
    """全件に Tier 2 → Tier 3 フルパイプラインを実行。"""
    hit_map = tier1_hit_map or {}
    results = []
    for row in rows:
        qid = row["question_id"]
        prop_idx = int(row["prop_idx"])
        atomic_units = json.loads(row["atomic_units"])

        # f4: missing は 1.0 (fail-closed) として扱う
        if qid not in f4_map:
            print(f"  WARNING: {qid} not in f4_map, treating as f4=1.0 (fail-closed)")
            f4 = 1.0
        else:
            f4 = f4_map[qid]

        # Tier 1 hit をベースラインから導出
        tier1_hit = prop_idx in hit_map.get(qid, set())

        full_result = run_cascade_full(
            proposition=row["core_proposition"],
            response=row["response"],
            model=model,
            tier1_hit=tier1_hit,
            f4_flag=f4,
            atomic_units=atomic_units,
            synonym_dict=synonym_dict,
        )

        results.append({
            **row,
            "f4_flag": f4,
            "verdict": full_result["verdict"],
            "conditions": full_result["conditions"],
            "fail_reason": full_result.get("fail_reason"),
            "tier2": full_result.get("details", {}).get("tier2", {}),
        })
    return results


# ============================================================
# 表示
# ============================================================

def print_tier2_results(results: list[dict]) -> None:
    """Tier 2 スコア表示。"""
    print(f"{'id':12s} {'sel_cat':18s} {'expected':14s} "
          f"{'top1':>6s} {'top2':>6s} {'gap':>6s} {'t2':>4s} top1_sentence[:50]")
    print("-" * 130)
    for r in results:
        t2 = r.get("tier2", r)
        print(f"{r['id']:12s} {r['selection_category']:18s} {r['expected_result']:14s} "
              f"{t2.get('top1_score', 0):6.4f} {t2.get('top2_score', 0):6.4f} "
              f"{t2.get('gap', 0):6.4f} "
              f"{'YES' if t2.get('pass_tier2') else 'no':>4s} "
              f"{t2.get('top1_sentence', '')[:50]}")


def print_full_results(results: list[dict]) -> None:
    """Tier 3 判定結果表示。"""
    print(f"\n{'id':12s} {'sel_cat':18s} {'expected':14s} {'verdict':12s} "
          f"{'c1':>3s} {'c2':>3s} {'c3':>3s} {'c4':>3s} {'c5':>3s} fail_reason")
    print("-" * 120)
    for r in results:
        c = r["conditions"]
        def yn(key): return "OK" if c.get(key, False) else "NG"
        print(f"{r['id']:12s} {r['selection_category']:18s} {r['expected_result']:14s} "
              f"{r['verdict']:12s} "
              f"{yn('c1_tfidf_miss'):>3s} {yn('c2_embedding'):>3s} {yn('c3_gap'):>3s} "
              f"{yn('c4_f4_clear'):>3s} {yn('c5_atomic'):>3s} "
              f"{r.get('fail_reason') or ''}")


def print_group_stats(results: list[dict]) -> None:
    """selection_category 別のスコア分布。"""
    groups: dict[str, list[float]] = {}
    for r in results:
        cat = r["selection_category"]
        t2 = r.get("tier2", r)
        score = t2.get("top1_score", 0.0)
        groups.setdefault(cat, []).append(score)

    print(f"\n{'category':20s} {'n':>3s} {'mean':>6s} {'std':>6s} {'min':>6s} {'max':>6s}")
    print("-" * 55)
    for cat in ["concept_absent", "hard_negative", "ovl_insufficient"]:
        scores = groups.get(cat, [])
        if scores:
            arr = np.array(scores)
            print(f"{cat:20s} {len(arr):3d} {arr.mean():6.4f} {arr.std():6.4f} "
                  f"{arr.min():6.4f} {arr.max():6.4f}")


# ============================================================
# Grid Search (Tier 2 only, for reference)
# ============================================================

def grid_search(results: list[dict]) -> None:
    """θ_sbert × δ_gap の grid search (Tier 2 のみ)。"""
    thetas = np.arange(0.45, 0.66, 0.05)
    deltas = np.arange(0.03, 0.11, 0.01)

    print("\n=== Grid Search: θ_sbert × δ_gap (Tier 2 only) ===")
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
                # Tier 1 hit 行は grid search から除外（Tier 2 未評価のため）
                if r.get("verdict") == "hit_tier1":
                    continue
                t2 = r.get("tier2", r)
                n_segments = len(t2.get("all_scores", []))
                gap_valid = n_segments > 1
                pass_t2 = (
                    (t2.get("top1_score", 0) >= theta)
                    and gap_valid
                    and (t2.get("gap", 0) >= delta)
                )
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

            if rescue > 0 or false_rescue > 0:
                print(f"{theta:6.2f} {delta:6.2f} {rescue:7d} {false_rescue:8d} "
                      f"{prec:6.3f} {recall:7.3f} {f1:6.3f}")

    print(f"\nBest F1 = {best_f1:.3f} at θ={best_params[0]:.2f}, δ={best_params[1]:.2f}")


# ============================================================
# PoC 受理基準
# ============================================================

def evaluate_poc(results: list[dict]) -> dict:
    """dev_cascade_20 の結果を PoC 受理基準に照らして判定する。

    受理基準:
    | 指標                    | 閾値    | 判定      |
    |------------------------|---------|----------|
    | concept_absent 回収数   | >= 3    | PASS/FAIL |
    | hard_negative 誤救済数  | == 0    | PASS/FAIL |
    | ovl_insufficient 回収数 | >= 2    | PASS/FAIL |
    | Tier 1 回帰            | == 0    | PASS/FAIL |
    | 総合                    | 全 PASS | GO/NO-GO |
    """
    ca_rescued = 0
    hn_false = 0
    oi_rescued = 0
    tier1_regression = 0

    for r in results:
        cat = r["selection_category"]
        verdict = r["verdict"]

        if cat == "concept_absent" and verdict == "Z_RESCUED":
            ca_rescued += 1
        elif cat == "hard_negative" and verdict == "Z_RESCUED":
            hn_false += 1
        elif cat == "ovl_insufficient" and verdict == "Z_RESCUED":
            oi_rescued += 1

        # Tier 1 回帰: hit_tier1 は dev_cascade_20 では発生しないはず
        if verdict == "hit_tier1":
            tier1_regression += 1

    criteria = {
        "c1_concept_absent": {
            "value": ca_rescued,
            "threshold": ">=3",
            "result": "PASS" if ca_rescued >= 3 else "FAIL",
        },
        "c2_hard_negative": {
            "value": hn_false,
            "threshold": "==0",
            "result": "PASS" if hn_false == 0 else "FAIL",
        },
        "c3_ovl_insufficient": {
            "value": oi_rescued,
            "threshold": ">=2",
            "result": "PASS" if oi_rescued >= 2 else "FAIL",
        },
        "c4_tier1_regression": {
            "value": tier1_regression,
            "threshold": "==0",
            "result": "PASS" if tier1_regression == 0 else "FAIL",
        },
    }

    overall = "GO" if all(c["result"] == "PASS" for c in criteria.values()) else "NO-GO"

    return {
        "concept_absent_rescued": ca_rescued,
        "hard_negative_false_rescue": hn_false,
        "ovl_insufficient_rescued": oi_rescued,
        "tier1_regression": tier1_regression,
        "criteria": criteria,
        "overall": overall,
    }


def print_poc_evaluation(eval_result: dict) -> None:
    """PoC 受理基準の判定結果を表示。"""
    print("\n" + "=" * 60)
    print("  PoC 受理基準判定")
    print("=" * 60)

    for key, c in eval_result["criteria"].items():
        status = "PASS" if c["result"] == "PASS" else "FAIL"
        print(f"  [{status}] {key}: {c['value']} (threshold: {c['threshold']})")

    overall = eval_result["overall"]
    print(f"\n  >>> 総合判定: {overall} <<<")

    # 撤退条件チェック
    if eval_result["hard_negative_false_rescue"] >= 2:
        print("  [ABORT] hard_negative 誤救済 >= 2 → cascade 不採用推奨")
    if eval_result["concept_absent_rescued"] == 0:
        print("  [ABORT] concept_absent 回収 == 0 → SBert 弁別力不足")

    print("=" * 60)


# ============================================================
# main
# ============================================================

def main():
    print("Loading model...")
    model = load_model()

    print("Loading dev_cascade_20.csv...")
    rows = load_dev_cascade()
    print(f"Loaded {len(rows)} rows.")

    print("Loading f4 flags...")
    f4_map = load_f4_flags()
    print(f"Loaded f4 flags for {len(f4_map)} questions.")

    print("Loading Tier 1 hit map...")
    tier1_hit_map = load_tier1_hits()
    print(f"Loaded Tier 1 hits for {len(tier1_hit_map)} questions.")

    print("Loading synonym dict...")
    synonym_dict = load_synonym_dict()
    print(f"Loaded {len(synonym_dict)} synonym entries.\n")

    # --- Tier 2 + Tier 3 フルパイプライン ---
    print("=== Full Pipeline (Tier 2 + Tier 3) ===\n")
    results = run_full_pipeline(rows, model, f4_map, synonym_dict, tier1_hit_map)

    print_tier2_results(results)
    print_full_results(results)
    print_group_stats(results)

    # --- Grid Search (Tier 2 のみ、参考) ---
    grid_search(results)

    # --- PoC 受理基準 ---
    eval_result = evaluate_poc(results)
    print_poc_evaluation(eval_result)


if __name__ == "__main__":
    main()

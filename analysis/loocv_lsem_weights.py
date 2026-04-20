"""analysis/loocv_lsem_weights.py — L_sem Phase 5 重みの LOO-CV 検証

Leave-One-Out Cross-Validation で重みの過学習リスクを検証する。
各 fold で 47 件で最適重みを求め、残り 1 件の L_sem を計算。
LOO 予測値と human O の Spearman ρ を full-sample ρ と比較。

使い方:
    python analysis/loocv_lsem_weights.py
"""
from __future__ import annotations

import csv
import itertools
import math
import statistics
import sys
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scipy.stats import ConstantInputWarning, spearmanr  # type: ignore[import-untyped]  # noqa: E402

warnings.filterwarnings("ignore", category=ConstantInputWarning)

# --- データパス ---
HA48_PATH = ROOT / "data" / "human_annotation_48" / "annotation_48_merged.csv"
V5_PATH = ROOT / "data" / "eval" / "audit_102_main_baseline_v5.csv"
Q_META_PATH = ROOT / "data" / "question_sets" / "ugh-audit-100q-v3-1.jsonl"
RESPONSES_PATH = ROOT / "data" / "phase_c_scored_v1_t0_only.jsonl"
CALIBRATION_CSV = ROOT / "analysis" / "grv_lsem_calibration_result.csv"


def load_calibration_data() -> List[Tuple[str, Dict[str, Optional[float]], float]]:
    """校正結果 CSV から (id, components, O) を読み込む"""
    data = []
    with open(CALIBRATION_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            components = {}
            for key in ["L_P", "L_Q", "L_R", "L_A", "L_G", "L_F", "L_X"]:
                val = row.get(key, "")
                components[key] = float(val) if val else None
            data.append((row["id"], components, float(row["O"])))
    return data


def weighted_sum(
    components: Dict[str, Optional[float]],
    weights: Dict[str, float],
) -> Optional[float]:
    available = {k: v for k, v in components.items() if v is not None}
    if not available:
        return None
    w_sum = sum(weights.get(k, 0.0) for k in available)
    if w_sum == 0.0:
        return None
    return sum(weights.get(k, 0.0) / w_sum * available[k] for k in available)


def grid_search_3(
    data: List[Tuple[Dict[str, Optional[float]], float]],
    step: float = 0.025,
) -> Tuple[Dict[str, float], float]:
    """コア3項 (L_P, L_F, L_G) のグリッドサーチ"""
    keys = ["L_P", "L_F", "L_G"]
    levels = [round(v * step, 4) for v in range(int(1.0 / step) + 1)]

    best_w = {"L_P": 0.25, "L_F": 0.15, "L_G": 0.50}
    best_rho = float("inf")  # 最も負の ρ を探索するため +inf で初期化

    for combo in itertools.product(levels, repeat=3):
        if sum(combo) == 0.0:
            continue
        weights = dict(zip(keys, combo))

        l_sem_values = []
        o_values = []
        for components, o in data:
            val = weighted_sum(components, weights)
            if val is not None:
                l_sem_values.append(val)
                o_values.append(o)

        if len(l_sem_values) < 10 or len(set(l_sem_values)) < 2:
            continue

        rho, _ = spearmanr(l_sem_values, o_values)
        if math.isnan(rho):
            continue
        if rho < best_rho:
            best_rho = rho
            best_w = weights

    return best_w, best_rho


def main() -> None:
    print("=" * 60)
    print("LOO-CV: L_sem Phase 5 重み安定性検証")
    print("=" * 60)

    data = load_calibration_data()
    n = len(data)
    print(f"データ: n={n}")

    # --- Full sample baseline ---
    print("\n--- Full sample baseline ---")
    full_data = [(c, o) for _, c, o in data]
    full_w, full_rho = grid_search_3(full_data, step=0.025)
    print(f"  Full ρ = {full_rho:.4f}  weights = {full_w}")

    # --- LOO-CV ---
    print(f"\n--- LOO-CV (n={n} folds) ---")
    loo_predictions = []
    fold_weights = []

    for i in range(n):
        # train on n-1
        train = [(c, o) for j, (_, c, o) in enumerate(data) if j != i]
        test_id, test_comp, test_o = data[i]

        # find optimal weights on training set (same step as full-sample to avoid quantization bias)
        w_opt, train_rho = grid_search_3(train, step=0.025)

        # predict on held-out item
        pred = weighted_sum(test_comp, w_opt)
        if pred is not None:
            loo_predictions.append((test_id, pred, test_o))
        fold_weights.append(w_opt)

        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{n} folds complete")

    print(f"  LOO 予測成功: {len(loo_predictions)}/{n}")

    # --- LOO ρ ---
    preds = [p[1] for p in loo_predictions]
    actuals = [p[2] for p in loo_predictions]
    loo_rho, loo_p = spearmanr(preds, actuals)

    print("\n--- Results ---")
    print(f"  Full-sample ρ:  {full_rho:.4f}")
    print(f"  LOO-CV ρ:       {loo_rho:.4f} (p={loo_p:.4f})")
    print(f"  差分 (shrinkage): {abs(full_rho) - abs(loo_rho):.4f}")

    # --- Weight stability ---
    print("\n--- Weight stability across folds ---")
    for key in ["L_P", "L_F", "L_G"]:
        vals = [w[key] for w in fold_weights]
        m = statistics.mean(vals)
        s = statistics.stdev(vals) if len(vals) > 1 else 0
        print(f"  {key}: mean={m:.3f}  std={s:.3f}  "
              f"min={min(vals):.3f}  max={max(vals):.3f}")

    # --- Verdict ---
    shrinkage = abs(full_rho) - abs(loo_rho)
    if shrinkage < 0.05:
        print(f"\n  ✓ Shrinkage {shrinkage:.4f} < 0.05: 重みは安定")
    elif shrinkage < 0.10:
        print(f"\n  △ Shrinkage {shrinkage:.4f} < 0.10: 軽度の過学習リスク")
    else:
        print(f"\n  ✗ Shrinkage {shrinkage:.4f} >= 0.10: 過学習の可能性")


if __name__ == "__main__":
    main()

"""analysis/optimize_semantic_loss_weights.py — Phase 4: 重み最適化

HA48 アノテーション (human O score) と v5 ベースライン (f1-f4, S, C) を結合し、
L_sem の重みを Spearman ρ 最大化で最適化する。

使い方:
    pip install -e ".[analysis]"
    python analysis/optimize_semantic_loss_weights.py

出力:
    - 最適重みと Spearman ρ
    - f2 配置候補の比較
    - 結果 CSV: analysis/semantic_loss_optimization_result.csv
"""
from __future__ import annotations

import csv
import itertools
import math
import sys
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scipy.stats import ConstantInputWarning, spearmanr  # type: ignore[import-untyped]  # noqa: E402

warnings.filterwarnings("ignore", category=ConstantInputWarning)

# --- データ読み込み ---

HA48_PATH = ROOT / "data" / "human_annotation_48" / "annotation_48_merged.csv"
V5_PATH = ROOT / "data" / "eval" / "audit_102_main_baseline_v5.csv"


def load_ha48() -> Dict[str, float]:
    """HA48 の id → O (human score) マップを返す"""
    result = {}
    with open(HA48_PATH) as f:
        for row in csv.DictReader(f):
            result[row["id"]] = float(row["O"])
    return result


def load_v5() -> Dict[str, Dict[str, float]]:
    """v5 ベースラインの id → {f1, f2, f3, f4, S, C, hits, total} マップを返す"""
    result = {}
    with open(V5_PATH) as f:
        for row in csv.DictReader(f):
            result[row["id"]] = {
                "f1": float(row["f1"]),
                "f2": float(row["f2"]),
                "f3": float(row["f3"]),
                "f4": float(row["f4"]),
                "S": float(row["S"]),
                "C": float(row["C"]),
                "hits": int(row["hits"]),
                "total": int(row["total"]),
            }
    return result


def compute_components(v5: Dict[str, float]) -> Dict[str, Optional[float]]:
    """v5 の f-flag から L_sem 各項を算出"""
    C = v5["C"]
    L_P = 1.0 - C if v5["total"] > 0 else None
    L_Q = v5["f3"]
    L_R = v5["f4"]
    L_A = v5["f1"]
    return {"L_P": L_P, "L_Q": L_Q, "L_R": L_R, "L_A": L_A}


# --- 重み付き合計 ---

def weighted_sum(
    components: Dict[str, Optional[float]],
    weights: Dict[str, float],
) -> Optional[float]:
    """非 None 項の重み付き合計を返す"""
    available = {k: v for k, v in components.items() if v is not None}
    if not available:
        return None
    w_sum = sum(weights.get(k, 0.0) for k in available)
    if w_sum == 0.0:
        return None
    return sum(weights.get(k, 0.0) / w_sum * available[k] for k in available)


# --- Grid Search ---

def grid_search(
    data: List[Tuple[Dict[str, Optional[float]], float]],
    component_keys: List[str],
    step: float = 0.05,
) -> List[Tuple[Dict[str, float], float, float]]:
    """全重み組合せで Spearman ρ を計算

    Args:
        data: [(components, human_O), ...]
        component_keys: 重みを最適化する項の名前
        step: grid の刻み幅

    Returns:
        [(weights, rho, p_value), ...] を ρ 降順でソート
    """
    # grid の候補値
    levels = [round(v * step, 4) for v in range(int(1.0 / step) + 1)]
    n_keys = len(component_keys)

    results = []
    for combo in itertools.product(levels, repeat=n_keys):
        total_w = sum(combo)
        if total_w == 0.0:
            continue

        weights = dict(zip(component_keys, combo))

        # L_sem を計算
        l_sem_values = []
        o_values = []
        for components, o in data:
            val = weighted_sum(components, weights)
            if val is not None:
                l_sem_values.append(val)
                o_values.append(o)

        if len(l_sem_values) < 10:
            continue

        # 定数配列の場合は Spearman ρ が定義されない
        if len(set(l_sem_values)) < 2:
            continue

        # Spearman ρ: L_sem vs -O (高い L_sem = 悪い、高い O = 良い → 負の相関を期待)
        rho, p = spearmanr(l_sem_values, o_values)
        if math.isnan(rho):
            continue
        results.append((weights, rho, p))

    # ρ が最も負の方が良い (L_sem↑ ↔ O↓)
    results.sort(key=lambda x: x[1])
    return results


# --- f2 統合テスト ---

def test_f2_integration(
    ha48: Dict[str, float],
    v5: Dict[str, Dict[str, float]],
) -> Dict[str, Tuple[float, float]]:
    """f2 の配置候補を比較

    A: f2 を L_P に加算 (L_P_aug = (1-C) + f2) / 2
    B: f2 を独立項 L_F として追加
    C: f2 なし (ベースライン)
    """
    results = {}

    # 共通データ準備
    ids = sorted(ha48.keys() & v5.keys())
    base_data = []
    for qid in ids:
        comp = compute_components(v5[qid])
        base_data.append((qid, comp, v5[qid]["f2"], ha48[qid]))

    # --- C: ベースライン (f2 なし) ---
    data_c = [(comp, o) for _, comp, _, o in base_data]
    best_c = grid_search(data_c, ["L_P", "L_Q", "L_R", "L_A"], step=0.10)
    if best_c:
        results["C_baseline"] = (best_c[0][1], best_c[0][2])

    # --- A: f2 → L_P に統合 ---
    data_a = []
    for qid, comp, f2, o in base_data:
        comp_a = dict(comp)
        if comp_a["L_P"] is not None:
            comp_a["L_P"] = min(1.0, (comp_a["L_P"] + f2) / 2.0)
        data_a.append((comp_a, o))
    best_a = grid_search(data_a, ["L_P", "L_Q", "L_R", "L_A"], step=0.10)
    if best_a:
        results["A_f2_in_LP"] = (best_a[0][1], best_a[0][2])

    # --- B: f2 → 独立項 L_F ---
    data_b = []
    for qid, comp, f2, o in base_data:
        comp_b = dict(comp)
        comp_b["L_F"] = f2
        data_b.append((comp_b, o))
    best_b = grid_search(data_b, ["L_P", "L_Q", "L_R", "L_A", "L_F"], step=0.10)
    if best_b:
        results["B_f2_as_LF"] = (best_b[0][1], best_b[0][2])

    return results


# --- メイン ---

def main() -> None:
    print("=" * 60)
    print("Phase 4: L_sem 重み最適化 (HA48, Spearman ρ 最大化)")
    print("=" * 60)

    ha48 = load_ha48()
    v5 = load_v5()
    ids = sorted(ha48.keys() & v5.keys())
    print(f"\nデータ: HA48={len(ha48)}, v5={len(v5)}, overlap={len(ids)}")

    # --- Step 1: 各項の単独相関 ---
    print("\n--- Step 1: 各項の単独 Spearman ρ (vs human O) ---")
    o_vals = [ha48[qid] for qid in ids]
    for key in ["L_P", "L_Q", "L_R", "L_A"]:
        vals = []
        for qid in ids:
            comp = compute_components(v5[qid])
            vals.append(comp[key] if comp[key] is not None else 0.0)
        rho, p = spearmanr(vals, o_vals)
        print(f"  {key}: ρ={rho:.4f} (p={p:.4f})")

    # f2 単独
    f2_vals = [v5[qid]["f2"] for qid in ids]
    rho_f2, p_f2 = spearmanr(f2_vals, o_vals)
    print(f"  f2:  ρ={rho_f2:.4f} (p={p_f2:.4f})")

    # 現行 ΔE (再計算)
    de_vals = []
    for qid in ids:
        d = v5[qid]
        s = d["S"]
        c = d["C"]
        de = (2.0 * (1.0 - s) ** 2 + 1.0 * (1.0 - c) ** 2) / 3.0
        de_vals.append(de)
    rho_de, p_de = spearmanr(de_vals, o_vals)
    print(f"  ΔE:  ρ={rho_de:.4f} (p={p_de:.4f})")

    # --- Step 2: Grid Search (f2 なし) ---
    print("\n--- Step 2: Grid Search (L_P, L_Q, L_R, L_A) ---")
    data = [(compute_components(v5[qid]), ha48[qid]) for qid in ids]
    results = grid_search(data, ["L_P", "L_Q", "L_R", "L_A"], step=0.10)

    print(f"  探索した組合せ: {len(results)}")
    print("\n  Top 5:")
    for i, (w, rho, p) in enumerate(results[:5]):
        w_str = " ".join(f"{k}={v:.2f}" for k, v in w.items())
        print(f"    #{i + 1}: ρ={rho:.4f} (p={p:.4f})  {w_str}")

    best_w, best_rho, best_p = results[0]
    print(f"\n  最良: ρ={best_rho:.4f} (p={best_p:.4f})")

    # --- Step 3: f2 統合テスト ---
    print("\n--- Step 3: f2 統合テスト ---")
    f2_results = test_f2_integration(ha48, v5)
    for label, (rho, p) in sorted(f2_results.items()):
        print(f"  {label}: ρ={rho:.4f} (p={p:.4f})")

    # --- Step 4: Grid Search (f2 込み) ---
    # L_A は f1 が全 0 で信号がないため除外し、L_P, L_Q, L_R, L_F で探索
    print("\n--- Step 4: Fine Grid (L_P, L_Q, L_R, L_F) step=0.05 ---")
    data_with_f2 = []
    for qid in ids:
        comp = compute_components(v5[qid])
        comp["L_F"] = v5[qid]["f2"]
        data_with_f2.append((comp, ha48[qid]))
    results_fine = grid_search(
        data_with_f2, ["L_P", "L_Q", "L_R", "L_F"], step=0.05
    )
    print(f"  探索した組合せ: {len(results_fine)}")
    print("\n  Top 5:")
    for i, (w, rho, p) in enumerate(results_fine[:5]):
        w_str = " ".join(f"{k}={v:.2f}" for k, v in w.items())
        print(f"    #{i + 1}: ρ={rho:.4f} (p={p:.4f})  {w_str}")

    best_fine_w, best_fine_rho, best_fine_p = results_fine[0]

    # --- Summary ---
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"  現行 ΔE:          ρ={rho_de:.4f}")
    print(f"  L_sem (f2 なし):   ρ={best_rho:.4f}  {best_w}")
    print(f"  L_sem (f2 込み):   ρ={best_fine_rho:.4f}  {best_fine_w}")

    # 結果をCSVに保存
    out_path = ROOT / "analysis" / "semantic_loss_optimization_result.csv"
    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "id", "O", "L_P", "L_Q", "L_R", "L_A", "L_F_f2",
            "L_sem_best", "delta_e",
        ])
        for qid in ids:
            comp = compute_components(v5[qid])
            w_best = best_fine_w
            comp_f2 = dict(comp)
            comp_f2["L_F"] = v5[qid]["f2"]
            l_sem = weighted_sum(comp_f2, w_best)
            s, c = v5[qid]["S"], v5[qid]["C"]
            de = (2.0 * (1.0 - s) ** 2 + 1.0 * (1.0 - c) ** 2) / 3.0
            writer.writerow([
                qid, ha48[qid],
                f"{comp['L_P']:.4f}" if comp["L_P"] is not None else "",
                f"{comp['L_Q']:.4f}",
                f"{comp['L_R']:.4f}",
                f"{comp['L_A']:.4f}",
                f"{v5[qid]['f2']:.4f}",
                f"{l_sem:.4f}" if l_sem is not None else "",
                f"{de:.4f}",
            ])
    print(f"\n  結果 CSV: {out_path}")


if __name__ == "__main__":
    main()

"""analysis/calibrate_grv_lsem.py — grv/L_sem 統合校正 (HA48)

HA48 全48件に grv を計算し、L_sem 7項フルで Spearman ρ 最大化グリッドサーチを行う。
grv タグ閾値の校正データも出力する。

使い方:
    pip install -e ".[dev]"
    pip install sentence-transformers scipy
    python analysis/calibrate_grv_lsem.py

出力:
    - grv 単体の HA48 ρ
    - L_sem 7項フル最適重み + ρ
    - grv タグ閾値の分布分析
    - 結果 CSV: analysis/grv_lsem_calibration_result.csv
"""
from __future__ import annotations

import ast
import csv
import itertools
import json
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
Q_META_PATH = ROOT / "data" / "question_sets" / "ugh-audit-100q-v3-1.json.txtl.txt"
RESPONSES_PATH = ROOT / "data" / "phase_c_scored_v1_t0_only.jsonl"

# --- データ読み込み ---


def load_ha48() -> Dict[str, dict]:
    result = {}
    with open(HA48_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            result[row["id"]] = row
    return result


def load_v5() -> Dict[str, dict]:
    result = {}
    with open(V5_PATH, encoding="utf-8") as f:
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
                "miss_ids": row.get("miss_ids", "[]"),
            }
    return result


def load_q_meta(ids: set) -> Dict[str, dict]:
    result = {}
    with open(Q_META_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec["id"] in ids:
                result[rec["id"]] = rec
    return result


def load_responses(ids: set) -> Dict[str, dict]:
    result = {}
    with open(RESPONSES_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            qid = rec.get("question_id") or rec.get("id")
            if qid in ids:
                result[qid] = rec
    return result


# --- L_X 計算 (polarity-bearing miss rate) ---

def compute_L_X(propositions: List[str], miss_ids_str: str) -> Optional[float]:
    """polarity-bearing 命題の miss 率を計算"""
    try:
        from detector import detect_operator, OPERATOR_CATALOG
    except ModuleNotFoundError as _err:
        if _err.name == "detector":
            return None
        raise

    if not propositions:
        return None

    _NEG_DEONTIC_TOKENS = (
        "べきではない", "すべきではない", "べきでない",
        "すべきでない", "べきじゃない", "すべきじゃない",
    )

    polarity_indices = set()
    for idx, p in enumerate(propositions):
        op = detect_operator(p)
        if op is not None and OPERATOR_CATALOG[op.family]["effect"] == "polarity_flip":
            polarity_indices.add(idx)
        elif any(tok in p for tok in _NEG_DEONTIC_TOKENS):
            polarity_indices.add(idx)

    if not polarity_indices:
        return None

    try:
        miss_ids = set(ast.literal_eval(miss_ids_str))
    except (ValueError, SyntaxError):
        miss_ids = set()

    polarity_misses = len(polarity_indices & miss_ids)
    return max(0.0, min(1.0, polarity_misses / len(polarity_indices)))


# --- 重み付き合計 ---

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


# --- Grid Search ---

def grid_search(
    data: List[Tuple[Dict[str, Optional[float]], float]],
    component_keys: List[str],
    step: float = 0.05,
) -> List[Tuple[Dict[str, float], float, float]]:
    levels = [round(v * step, 4) for v in range(int(1.0 / step) + 1)]
    n_keys = len(component_keys)

    results = []
    for combo in itertools.product(levels, repeat=n_keys):
        total_w = sum(combo)
        if total_w == 0.0:
            continue

        weights = dict(zip(component_keys, combo))

        l_sem_values = []
        o_values = []
        for components, o in data:
            val = weighted_sum(components, weights)
            if val is not None:
                l_sem_values.append(val)
                o_values.append(o)

        if len(l_sem_values) < 10:
            continue
        if len(set(l_sem_values)) < 2:
            continue

        rho, p = spearmanr(l_sem_values, o_values)
        if math.isnan(rho):
            continue
        results.append((weights, rho, p))

    results.sort(key=lambda x: x[1])
    return results


# --- メイン ---

def main() -> None:
    print("=" * 60)
    print("grv / L_sem 統合校正 (HA48, n=48)")
    print("=" * 60)

    # --- Step 0: データ読み込み ---
    ha48 = load_ha48()
    v5 = load_v5()
    ids = sorted(ha48.keys() & v5.keys())
    q_meta = load_q_meta(set(ids))
    responses = load_responses(set(ids))

    print(f"\nデータ: HA48={len(ha48)}, v5={len(v5)}, q_meta={len(q_meta)}, "
          f"responses={len(responses)}, overlap={len(ids)}")

    missing_meta = [qid for qid in ids if qid not in q_meta]
    missing_resp = [qid for qid in ids if qid not in responses]
    if missing_meta:
        print(f"  WARNING: q_meta 欠落: {missing_meta}")
    if missing_resp:
        print(f"  WARNING: responses 欠落: {missing_resp}")

    # --- Step 1: HA48 全件 grv 計算 ---
    print("\n--- Step 1: HA48 全件 grv 計算 ---")
    from grv_calculator import compute_grv

    grv_results = {}
    grv_fail = []
    for qid in ids:
        if qid not in q_meta or qid not in responses:
            grv_fail.append(qid)
            continue
        meta = q_meta[qid]
        resp = responses[qid]
        r = compute_grv(
            question=meta["question"],
            response_text=resp.get("response", ""),
            question_meta=meta,
            metadata_source="inline",
        )
        if r is None:
            grv_fail.append(qid)
        else:
            grv_results[qid] = r

    print(f"  grv 計算成功: {len(grv_results)}/{len(ids)}")
    if grv_fail:
        print(f"  grv 計算失敗: {grv_fail}")

    # --- Step 2: grv 単体相関 ---
    print("\n--- Step 2: grv 単体 HA48 相関 ---")
    paired_grv = []
    for qid in ids:
        if qid in grv_results:
            o = float(ha48[qid]["O"])
            paired_grv.append((grv_results[qid].grv, o))

    grvs = [p[0] for p in paired_grv]
    os_grv = [p[1] for p in paired_grv]
    rho_grv, p_grv = spearmanr(grvs, os_grv)
    sigma_grv = statistics.stdev(grvs) if len(grvs) > 1 else 0.0

    print(f"  ρ(grv, O) = {rho_grv:.4f}  (p={p_grv:.4f})")
    print(f"  σ(grv) = {sigma_grv:.4f}")
    print(f"  mean(grv) = {statistics.mean(grvs):.4f}")
    print(f"  min/max = {min(grvs):.4f} / {max(grvs):.4f}")

    # grv 成分別相関
    for comp_name in ["drift", "dispersion", "collapse_v2", "cover_soft", "wash_index"]:
        vals = [getattr(grv_results[qid], comp_name) for qid in ids if qid in grv_results]
        rho_c, _ = spearmanr(vals, os_grv)
        print(f"  ρ({comp_name}, O) = {rho_c:.4f}")

    # --- Step 3: L_X 計算 ---
    print("\n--- Step 3: L_X (polarity-bearing miss rate) 計算 ---")
    l_x_values = {}
    for qid in ids:
        if qid not in q_meta:
            continue
        props = q_meta[qid].get("core_propositions", [])
        miss_str = v5[qid]["miss_ids"]
        l_x = compute_L_X(props, miss_str)
        l_x_values[qid] = l_x

    n_l_x_valid = sum(1 for v in l_x_values.values() if v is not None)
    print(f"  L_X 算出: {n_l_x_valid}/{len(ids)} (polarity-bearing 命題あり)")

    if n_l_x_valid >= 10:
        lx_paired = [(l_x_values[qid], float(ha48[qid]["O"]))
                     for qid in ids if l_x_values.get(qid) is not None]
        rho_lx, _ = spearmanr([p[0] for p in lx_paired], [p[1] for p in lx_paired])
        print(f"  ρ(L_X, O) = {rho_lx:.4f}")

    # --- Step 4: 7項フル L_sem データ準備 ---
    print("\n--- Step 4: L_sem 7項フル データ準備 ---")
    full_data = []
    for qid in ids:
        d = v5[qid]
        L_P = 1.0 - d["C"] if d["total"] > 0 else None
        L_Q = d["f3"]
        L_R = d["f4"]
        L_A = d["f1"]
        L_F = d["f2"]
        L_G = grv_results[qid].grv if qid in grv_results else None
        L_X = l_x_values.get(qid)

        components = {
            "L_P": L_P, "L_Q": L_Q, "L_R": L_R,
            "L_A": L_A, "L_G": L_G, "L_F": L_F, "L_X": L_X,
        }
        full_data.append((qid, components, float(ha48[qid]["O"])))

    n_with_grv = sum(1 for _, c, _ in full_data if c["L_G"] is not None)
    n_with_lx = sum(1 for _, c, _ in full_data if c["L_X"] is not None)
    print(f"  L_G (grv) あり: {n_with_grv}/{len(ids)}")
    print(f"  L_X (polarity) あり: {n_with_lx}/{len(ids)}")

    # --- Step 5: 各項の単独相関 ---
    print("\n--- Step 5: 各項の単独 Spearman ρ (vs human O) ---")
    o_vals = [float(ha48[qid]["O"]) for qid in ids]
    for key in ["L_P", "L_Q", "L_R", "L_A", "L_G", "L_F", "L_X"]:
        vals_and_o = [(c[key], o) for _, c, o in full_data if c[key] is not None]
        if len(vals_and_o) < 10:
            print(f"  {key}: データ不足 ({len(vals_and_o)} 件)")
            continue
        rho, p = spearmanr([v[0] for v in vals_and_o], [v[1] for v in vals_and_o])
        print(f"  {key}: ρ={rho:.4f} (p={p:.4f}, n={len(vals_and_o)})")

    # ΔE ベースライン
    de_vals = []
    for qid in ids:
        d = v5[qid]
        de = (2.0 * (1.0 - d["S"]) ** 2 + 1.0 * (1.0 - d["C"]) ** 2) / 3.0
        de_vals.append(de)
    rho_de, p_de = spearmanr(de_vals, o_vals)
    print(f"  ΔE:  ρ={rho_de:.4f} (p={p_de:.4f})")

    # --- Step 6: グリッドサーチ (戦略的) ---
    # L_A=全零, L_Q/L_R/L_X=弱信号 → コア3項 (L_P, L_F, L_G) を軸に探索
    data_all = [(c, o) for _, c, o in full_data]

    # 6a: コア3項 (L_P, L_F, L_G) — step=0.025 (高精度)
    print("\n--- Step 6a: Grid Search (コア3項: L_P, L_F, L_G) step=0.025 ---")
    results_3 = grid_search(data_all, ["L_P", "L_F", "L_G"], step=0.025)
    if results_3:
        print(f"  探索: {len(results_3)} 組合せ")
        print("  Top 5:")
        for i, (w, rho, p) in enumerate(results_3[:5]):
            w_str = " ".join(f"{k}={v:.3f}" for k, v in w.items())
            print(f"    #{i+1}: ρ={rho:.4f} (p={p:.4f})  {w_str}")

    # 6b: 旧ベースライン再現 (L_P, L_F) — L_G なし
    print("\n--- Step 6b: Grid Search (旧2項: L_P, L_F) step=0.025 ---")
    results_2 = grid_search(data_all, ["L_P", "L_F"], step=0.025)
    if results_2:
        print(f"  探索: {len(results_2)} 組合せ")
        print("  Top 3:")
        for i, (w, rho, p) in enumerate(results_2[:3]):
            w_str = " ".join(f"{k}={v:.3f}" for k, v in w.items())
            print(f"    #{i+1}: ρ={rho:.4f} (p={p:.4f})  {w_str}")

    # L_G 増分寄与
    if results_2 and results_3:
        delta = results_2[0][1] - results_3[0][1]
        print(f"\n  L_G 増分寄与: Δρ = {delta:+.4f} ({'改善' if delta > 0 else '悪化' if delta < 0 else '変化なし'})")

    # 6c: +L_R (4項: L_P, L_F, L_G, L_R) step=0.05
    print("\n--- Step 6c: Grid Search (+L_R: L_P, L_F, L_G, L_R) step=0.05 ---")
    results_4r = grid_search(data_all, ["L_P", "L_F", "L_G", "L_R"], step=0.05)
    if results_4r:
        print(f"  探索: {len(results_4r)} 組合せ")
        print("  Top 3:")
        for i, (w, rho, p) in enumerate(results_4r[:3]):
            w_str = " ".join(f"{k}={v:.2f}" for k, v in w.items())
            print(f"    #{i+1}: ρ={rho:.4f} (p={p:.4f})  {w_str}")
        if results_3:
            delta = results_3[0][1] - results_4r[0][1]
            print(f"  L_R 増分寄与: Δρ = {delta:+.4f}")

    # 6d: +L_Q (4項: L_P, L_F, L_G, L_Q) step=0.05
    print("\n--- Step 6d: Grid Search (+L_Q: L_P, L_F, L_G, L_Q) step=0.05 ---")
    results_4q = grid_search(data_all, ["L_P", "L_F", "L_G", "L_Q"], step=0.05)
    if results_4q:
        print(f"  探索: {len(results_4q)} 組合せ")
        print("  Top 3:")
        for i, (w, rho, p) in enumerate(results_4q[:3]):
            w_str = " ".join(f"{k}={v:.2f}" for k, v in w.items())
            print(f"    #{i+1}: ρ={rho:.4f} (p={p:.4f})  {w_str}")
        if results_3:
            delta = results_3[0][1] - results_4q[0][1]
            print(f"  L_Q 増分寄与: Δρ = {delta:+.4f}")

    # 6e: フル5項 (L_P, L_F, L_G, L_R, L_Q) step=0.10
    print("\n--- Step 6e: Grid Search (5項: L_P, L_F, L_G, L_R, L_Q) step=0.10 ---")
    results_5 = grid_search(data_all, ["L_P", "L_F", "L_G", "L_R", "L_Q"], step=0.10)
    if results_5:
        print(f"  探索: {len(results_5)} 組合せ")
        print("  Top 3:")
        for i, (w, rho, p) in enumerate(results_5[:3]):
            w_str = " ".join(f"{k}={v:.2f}" for k, v in w.items())
            print(f"    #{i+1}: ρ={rho:.4f} (p={p:.4f})  {w_str}")

    # --- Step 7: grv タグ閾値分析 ---
    print("\n--- Step 7: grv タグ閾値の校正データ ---")
    grv_o_pairs = sorted(
        [(grv_results[qid].grv, float(ha48[qid]["O"]), qid)
         for qid in ids if qid in grv_results],
        key=lambda x: x[0],
    )
    print(f"\n  grv 分布 (n={len(grv_o_pairs)}):")
    # 四分位
    grv_sorted = [p[0] for p in grv_o_pairs]
    q25 = sorted(grv_sorted)[len(grv_sorted) // 4]
    q50 = sorted(grv_sorted)[len(grv_sorted) // 2]
    q75 = sorted(grv_sorted)[3 * len(grv_sorted) // 4]
    print(f"  Q25={q25:.4f}  Q50(median)={q50:.4f}  Q75={q75:.4f}")

    # 閾値候補ごとの verdict 精度
    print("\n  閾値候補ごとの区分:")
    for t_mid, t_high in [(0.20, 0.30), (0.25, 0.50), (0.30, 0.60), (0.33, 0.66), (0.35, 0.70)]:
        low = [o for g, o, _ in grv_o_pairs if g < t_mid]
        mid = [o for g, o, _ in grv_o_pairs if t_mid <= g < t_high]
        high = [o for g, o, _ in grv_o_pairs if g >= t_high]
        low_m = statistics.mean(low) if low else 0
        mid_m = statistics.mean(mid) if mid else 0
        high_m = statistics.mean(high) if high else 0
        monotone = low_m >= mid_m >= high_m if low and mid and high else False
        print(f"  [{t_mid:.2f}/{t_high:.2f}]  "
              f"low: n={len(low)} mean_O={low_m:.2f} | "
              f"mid: n={len(mid)} mean_O={mid_m:.2f} | "
              f"high: n={len(high)} mean_O={high_m:.2f}  "
              f"{'✓ monotone' if monotone else '✗ non-monotone'}")

    # --- Step 8: Summary ---
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"  ΔE baseline:       ρ={rho_de:.4f}")
    if results_2:
        print(f"  L_sem 2項 (P,F):   ρ={results_2[0][1]:.4f}  {results_2[0][0]}")
    if results_3:
        print(f"  L_sem 3項 (P,F,G): ρ={results_3[0][1]:.4f}  {results_3[0][0]}")
    if results_4r:
        print(f"  L_sem +L_R:        ρ={results_4r[0][1]:.4f}  {results_4r[0][0]}")
    if results_4q:
        print(f"  L_sem +L_Q:        ρ={results_4q[0][1]:.4f}  {results_4q[0][0]}")
    if results_5:
        print(f"  L_sem 5項:         ρ={results_5[0][1]:.4f}  {results_5[0][0]}")
    print(f"  grv 単体:          ρ={rho_grv:.4f}")

    # --- CSV 出力 ---
    out_path = ROOT / "analysis" / "grv_lsem_calibration_result.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "id", "O", "L_P", "L_Q", "L_R", "L_A", "L_G", "L_F", "L_X",
            "grv", "drift", "dispersion", "collapse_v2", "cover_soft", "wash_index",
            "delta_e",
        ])
        for qid, components, o in full_data:
            d = v5[qid]
            de = (2.0 * (1.0 - d["S"]) ** 2 + 1.0 * (1.0 - d["C"]) ** 2) / 3.0
            grv_r = grv_results.get(qid)
            writer.writerow([
                qid, o,
                f"{components['L_P']:.4f}" if components["L_P"] is not None else "",
                f"{components['L_Q']:.4f}",
                f"{components['L_R']:.4f}",
                f"{components['L_A']:.4f}",
                f"{components['L_G']:.4f}" if components["L_G"] is not None else "",
                f"{components['L_F']:.4f}",
                f"{components['L_X']:.4f}" if components["L_X"] is not None else "",
                f"{grv_r.grv:.4f}" if grv_r else "",
                f"{grv_r.drift:.4f}" if grv_r else "",
                f"{grv_r.dispersion:.4f}" if grv_r else "",
                f"{grv_r.collapse_v2:.4f}" if grv_r else "",
                f"{grv_r.cover_soft:.4f}" if grv_r else "",
                f"{grv_r.wash_index:.4f}" if grv_r else "",
                f"{de:.4f}",
            ])
    print(f"\n  結果 CSV: {out_path}")


if __name__ == "__main__":
    main()

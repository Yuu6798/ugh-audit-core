"""semantic_loss_verification.py — 意味損失関数テスト検証

HA20 (n=20) を用いて、意味損失関数の各モデルを比較検証する。
"""
from __future__ import annotations

import csv
import json
import itertools
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
from scipy.stats import spearmanr
from scipy.optimize import minimize

ROOT = Path(__file__).resolve().parent.parent.parent

# --- データロード ---

def load_ha20() -> List[Dict]:
    path = ROOT / "data/human_annotation_20/human_annotation_20_completed.csv"
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            hits_str, total_str = r["propositions_hit"].split("/")
            rows.append({
                "id": r["id"],
                "category": r["category"],
                "human_score": float(r["human_score"]),
                "por": float(r["por"]),
                "delta_e_full": float(r["delta_e_full"]),
                "human_hits": int(hits_str),
                "human_total": int(total_str),
                "human_hit_rate": int(hits_str) / int(total_str) if int(total_str) > 0 else 0,
            })
    return rows


def load_structural_gate() -> Dict[str, Dict]:
    path = ROOT / "data/gate_results/structural_gate_summary.csv"
    data = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("temperature") == "0.0":
                data[r["id"]] = {
                    "verdict": r["verdict"],
                    "fail_max": float(r["fail_max"]),
                    "f1_flag": float(r["f1_flag"]),
                    "f2_flag": float(r["f2_flag"]),
                    "f3_flag": float(r["f3_flag"]),
                    "f4_flag": float(r["f4_flag"]),
                }
    return data


def load_system_baseline() -> Dict[str, Dict]:
    path = ROOT / "data/eval/audit_102_main_baseline_cascade.csv"
    if not path.exists():
        path = ROOT / "data/eval/audit_102_main_baseline_round4.csv"
    data = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            hits = int(r["hits"])
            total = int(r["total"])
            data[r["id"]] = {
                "hits": hits,
                "total": total,
                "hit_rate": hits / total if total > 0 else 0,
                "f1": float(r["f1"]),
                "f2": float(r["f2"]),
                "f3": float(r["f3"]),
                "f4": float(r["f4"]),
                "dE": float(r["dE"]),
                "decision": r["decision"],
                "hit_sources": r.get("hit_sources", "{}"),
            }
    return data


# --- 損失関数計算 ---

def compute_losses(ha20: List[Dict], sg: Dict, sys_bl: Dict, use_system: bool = False):
    """各ケースの損失値を計算する。

    use_system=False: reference (human propositions_hit) ベース
    use_system=True:  system propositions_hit_rate ベース
    """
    results = []
    for row in ha20:
        qid = row["id"]
        sg_row = sg.get(qid, {})
        sys_row = sys_bl.get(qid, {})

        if use_system:
            hit_rate = sys_row.get("hit_rate", 0)
        else:
            hit_rate = row["human_hit_rate"]

        L_P = 1.0 - hit_rate
        L_Q = max(sg_row.get("f3_flag", 0), sg_row.get("f4_flag", 0))
        L_R = row["delta_e_full"]
        L_struct = sg_row.get("fail_max", 0)

        results.append({
            "id": qid,
            "human_score": row["human_score"],
            "hit_rate": hit_rate,
            "L_P": L_P,
            "L_Q": L_Q,
            "L_R": L_R,
            "L_struct": L_struct,
            "por": row["por"],
            "f3_flag": sg_row.get("f3_flag", 0),
            "f4_flag": sg_row.get("f4_flag", 0),
            "sys_hit_rate": sys_row.get("hit_rate", 0),
            "human_hit_rate": row["human_hit_rate"],
        })
    return results


# --- モデル評価 ---

def eval_model_a(data: List[Dict]) -> Tuple[float, float]:
    """Model A: human_score ~ L_P"""
    hs = [d["human_score"] for d in data]
    lp = [d["L_P"] for d in data]
    rho, p = spearmanr(hs, [-x for x in lp])  # higher score = lower loss
    return rho, p


def eval_model_b(data: List[Dict], use_struct: bool = False) -> Tuple[float, float, dict]:
    """Model B/B': 線形結合の最適化。グリッドサーチ。"""
    hs = np.array([d["human_score"] for d in data])
    lp = np.array([d["L_P"] for d in data])
    lq = np.array([d["L_struct" if use_struct else "L_Q"] for d in data])
    lr = np.array([d["L_R"] for d in data])

    best_rho = -1.0
    best_params = {}
    for a, b, g in itertools.product(
        np.arange(0.1, 1.01, 0.1),
        np.arange(0.0, 0.51, 0.1),
        np.arange(0.0, 0.51, 0.1),
    ):
        total = a + b + g
        if total < 0.01:
            continue
        score = -(a * lp + b * lq + g * lr) / total  # negate: lower loss = higher score
        rho, p = spearmanr(hs, score)
        if rho > best_rho:
            best_rho = rho
            best_p = p
            best_params = {"alpha": round(a, 2), "beta": round(b, 2), "gamma": round(g, 2)}

    return best_rho, best_p, best_params


def eval_model_c(data: List[Dict], use_struct: bool = False) -> Tuple[float, float, dict]:
    """Model C/C': max(L_gate, linear) — ボトルネック型。"""
    hs = np.array([d["human_score"] for d in data])
    lp = np.array([d["L_P"] for d in data])
    lq = np.array([d["L_struct" if use_struct else "L_Q"] for d in data])
    lr = np.array([d["L_R"] for d in data])

    best_rho = -1.0
    best_params = {}
    for a, b, g in itertools.product(
        np.arange(0.1, 1.01, 0.1),
        np.arange(0.0, 0.51, 0.1),
        np.arange(0.0, 0.51, 0.1),
    ):
        total = a + b + g
        if total < 0.01:
            continue
        linear = (a * lp + b * lq + g * lr) / total
        bottleneck = np.maximum(lq, linear)
        score = -bottleneck
        rho, p = spearmanr(hs, score)
        if rho > best_rho:
            best_rho = rho
            best_p = p
            best_params = {"alpha": round(a, 2), "beta": round(b, 2), "gamma": round(g, 2)}

    return best_rho, best_p, best_params


def eval_model_d(data: List[Dict]) -> Tuple[float, float, dict]:
    """Model D: 相乗型 S = (1-L_P)^a * (1-L_Q)^b * (1-L_R)^g"""
    hs = np.array([d["human_score"] for d in data])
    lp = np.array([d["L_P"] for d in data])
    lq = np.array([d["L_Q"] for d in data])
    lr = np.array([d["L_R"] for d in data])

    # Clamp to avoid 0^x issues
    hp = np.clip(1.0 - lp, 1e-6, 1.0)
    hq = np.clip(1.0 - lq, 1e-6, 1.0)
    hr = np.clip(1.0 - lr, 1e-6, 1.0)

    best_rho = -1.0
    best_params = {}
    for a, b, g in itertools.product(
        np.arange(0.5, 3.01, 0.5),
        np.arange(0.0, 1.51, 0.5),
        np.arange(0.0, 1.51, 0.5),
    ):
        score = (hp ** a) * (hq ** b) * (hr ** g)
        rho, p = spearmanr(hs, score)
        if rho > best_rho:
            best_rho = rho
            best_p = p
            best_params = {"alpha": round(a, 2), "beta": round(b, 2), "gamma": round(g, 2)}

    return best_rho, best_p, best_params


def eval_single_indicator(data: List[Dict], key: str, negate: bool = True) -> Tuple[float, float]:
    """単独指標の Spearman ρ。negate=True なら値が小さいほどスコアが高いと仮定。"""
    hs = [d["human_score"] for d in data]
    vals = [d[key] for d in data]
    if negate:
        vals = [-v for v in vals]
    rho, p = spearmanr(hs, vals)
    return rho, p


# --- メイン ---

def main():
    ha20 = load_ha20()
    sg = load_structural_gate()
    sys_bl = load_system_baseline()

    print("=" * 70)
    print("意味損失関数テスト検証")
    print("=" * 70)

    # ============ Task 1: 予備分析再現 (reference ρ) ============
    print("\n## Task 1: 予備分析再現 (reference ρ)")
    ref_data = compute_losses(ha20, sg, sys_bl, use_system=False)

    rho_a, p_a = eval_model_a(ref_data)
    rho_b, p_b, params_b = eval_model_b(ref_data, use_struct=False)
    rho_bp, p_bp, params_bp = eval_model_b(ref_data, use_struct=True)
    rho_c, p_c, params_c = eval_model_c(ref_data, use_struct=False)
    rho_cp, p_cp, params_cp = eval_model_c(ref_data, use_struct=True)
    rho_d, p_d, params_d = eval_model_d(ref_data)

    print(f"\n{'Model':<20} {'ρ':>8} {'p-value':>10} {'params'}")
    print("-" * 60)
    print(f"{'A: L_P only':<20} {rho_a:>8.4f} {p_a:>10.6f}")
    print(f"{'B: αL_P+βL_Q+γL_R':<20} {rho_b:>8.4f} {p_b:>10.6f}  {params_b}")
    print(f"{'B: αL_P+βL_st+γL_R':<20} {rho_bp:>8.4f} {p_bp:>10.6f}  {params_bp}")
    print(f"{'C: max(L_Q, lin)':<20} {rho_c:>8.4f} {p_c:>10.6f}  {params_c}")
    print(f"{'C: max(L_st, lin)':<20} {rho_cp:>8.4f} {p_cp:>10.6f}  {params_cp}")
    print(f"{'D: multiplicative':<20} {rho_d:>8.4f} {p_d:>10.6f}  {params_d}")

    # ============ Task 2: system ρ 算出 ============
    print("\n\n## Task 2: reference ρ vs system ρ")
    sys_data = compute_losses(ha20, sg, sys_bl, use_system=True)

    rho_ref, p_ref = spearmanr(
        [d["human_score"] for d in ref_data],
        [d["human_hit_rate"] for d in ref_data],
    )
    rho_sys, p_sys = spearmanr(
        [d["human_score"] for d in sys_data],
        [d["sys_hit_rate"] for d in sys_data],
    )

    print(f"\n{'Metric':<35} {'ρ':>8} {'p-value':>10}")
    print("-" * 55)
    print(f"{'reference: human_hit vs human_score':<35} {rho_ref:>8.4f} {p_ref:>10.6f}")
    print(f"{'system: sys_hit vs human_score':<35} {rho_sys:>8.4f} {p_sys:>10.6f}")
    print(f"{'差分 (system - reference)':<35} {rho_sys - rho_ref:>8.4f}")

    # ケース別比較
    print(f"\n{'qid':<6} {'human':>6} {'h_hr':>6} {'s_hr':>6} {'gap':>6}")
    print("-" * 36)
    for rd, sd in zip(ref_data, sys_data):
        gap = sd["sys_hit_rate"] - rd["human_hit_rate"]
        marker = " ←" if abs(gap) > 0.2 else ""
        print(f"{rd['id']:<6} {rd['human_score']:>6.1f} {rd['human_hit_rate']:>6.3f} "
              f"{sd['sys_hit_rate']:>6.3f} {gap:>+6.3f}{marker}")

    # ============ Task 3: system L_P ベース多変量分析 ============
    print("\n\n## Task 3: system L_P ベース多変量分析")

    rho_sa, p_sa = eval_model_a(sys_data)
    rho_sb, p_sb, params_sb = eval_model_b(sys_data, use_struct=False)
    rho_sbp, p_sbp, params_sbp = eval_model_b(sys_data, use_struct=True)
    rho_sc, p_sc, params_sc = eval_model_c(sys_data, use_struct=False)
    rho_scp, p_scp, params_scp = eval_model_c(sys_data, use_struct=True)
    rho_sd, p_sd, params_sd = eval_model_d(sys_data)

    print(f"\n{'Model':<20} {'ref ρ':>8} {'sys ρ':>8} {'p-value':>10} {'params'}")
    print("-" * 70)
    print(f"{'A: L_P only':<20} {rho_a:>8.4f} {rho_sa:>8.4f} {p_sa:>10.6f}")
    print(f"{'B: αL_P+βL_Q+γL_R':<20} {rho_b:>8.4f} {rho_sb:>8.4f} {p_sb:>10.6f}  {params_sb}")
    print(f"{'B: αL_P+βL_st+γL_R':<20} {rho_bp:>8.4f} {rho_sbp:>8.4f} {p_sbp:>10.6f}  {params_sbp}")
    print(f"{'C: max(L_Q, lin)':<20} {rho_c:>8.4f} {rho_sc:>8.4f} {p_sc:>10.6f}  {params_sc}")
    print(f"{'C: max(L_st, lin)':<20} {rho_cp:>8.4f} {rho_scp:>8.4f} {p_scp:>10.6f}  {params_scp}")
    print(f"{'D: multiplicative':<20} {rho_d:>8.4f} {rho_sd:>8.4f} {p_sd:>10.6f}  {params_sd}")

    # ============ Task 4: ボトルネック条件の実用性 ============
    print("\n\n## Task 4: ボトルネック条件の実用性検証")

    print(f"\n{'qid':<6} {'human':>6} {'s_hr':>6} {'L_st':>6} {'hit_pred':>9} {'bn_pred':>9} {'改善?'}")
    print("-" * 60)
    for d in sys_data:
        hit_pred = d["hit_rate"]  # Model A prediction proxy
        bn_loss = max(d["L_struct"], d["L_P"])
        bn_pred = 1.0 - bn_loss
        hit_err = abs(d["human_score"] / 5.0 - hit_pred)
        bn_err = abs(d["human_score"] / 5.0 - bn_pred)
        improved = "↑" if bn_err < hit_err - 0.01 else ("↓" if bn_err > hit_err + 0.01 else "=")
        marker = " ★" if d["L_struct"] > 0 else ""
        print(f"{d['id']:<6} {d['human_score']:>6.1f} {d['hit_rate']:>6.3f} "
              f"{d['L_struct']:>6.2f} {hit_pred:>9.3f} {bn_pred:>9.3f}  {improved}{marker}")

    # f4 発火ケースの詳細
    f4_cases = ["q024", "q095", "q100", "q025"]
    print(f"\n### f4 発火ケース詳細")
    print(f"{'qid':<6} {'human':>6} {'f3':>5} {'f4':>5} {'L_Q':>5} {'L_st':>5} {'sys_hr':>6} {'decision'}")
    print("-" * 55)
    for d in sys_data:
        if d["id"] in f4_cases:
            sg_row = sg.get(d["id"], {})
            sys_row = sys_bl.get(d["id"], {})
            print(f"{d['id']:<6} {d['human_score']:>6.1f} {d['f3_flag']:>5.1f} {d['f4_flag']:>5.1f} "
                  f"{d['L_Q']:>5.2f} {d['L_struct']:>5.2f} {d['sys_hit_rate']:>6.3f} "
                  f"{sys_row.get('decision', '?')}")

    # ============ Task 5: 各指標単独の診断力 ============
    print("\n\n## Task 5: 各指標単独の診断力")

    indicators = [
        ("human_hit_rate (reference)", "human_hit_rate", ref_data, False),
        ("sys_hit_rate (system)", "sys_hit_rate", sys_data, False),
        ("L_Q = max(f3, f4)", "L_Q", ref_data, True),
        ("L_R = delta_e_full", "L_R", ref_data, True),
        ("L_struct = fail_max", "L_struct", ref_data, True),
        ("PoR", "por", ref_data, False),
    ]

    print(f"\n{'Indicator':<30} {'ρ':>8} {'p-value':>10} {'有意?'}")
    print("-" * 58)
    for name, key, data, neg in indicators:
        rho, p = eval_single_indicator(data, key, negate=neg)
        sig = "YES" if p < 0.05 else "NO"
        print(f"{name:<30} {rho:>8.4f} {p:>10.6f}  {sig}")

    # ============ Task 6: 結論 ============
    print("\n\n## Task 6: 結論と推奨")
    print()
    print("### 1. 多変量化は必要か？")
    delta_b = rho_sb - rho_sa
    print(f"   system L_P 単独: ρ = {rho_sa:.4f}")
    print(f"   最良線形結合:    ρ = {rho_sb:.4f} (Δ = {delta_b:+.4f})")
    if delta_b > 0.05:
        print("   → L_Q/L_R の追加で改善あり。ただし n=20 では過学習リスクあり")
    else:
        print("   → L_Q/L_R を足しても有意な改善なし")

    print("\n### 2. ボトルネック条件は採用すべきか？")
    delta_cp = rho_scp - rho_sa
    print(f"   Model A:  ρ = {rho_sa:.4f}")
    print(f"   Model C': ρ = {rho_scp:.4f} (Δ = {delta_cp:+.4f})")

    print("\n### 3. reference ρ vs system ρ のギャップ")
    print(f"   reference ρ = {rho_ref:.4f} (人間の内部一貫性)")
    print(f"   system ρ   = {rho_sys:.4f} (自動検出の性能)")
    print(f"   ギャップ    = {rho_ref - rho_sys:.4f}")
    print("   → L_P の検出精度改善が最優先課題")

    # CSV 出力
    csv_path = ROOT / "analysis/semantic_loss/model_comparison.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["model", "ref_rho", "sys_rho", "sys_p_value", "params"])
        w.writerow(["A: L_P only", f"{rho_a:.4f}", f"{rho_sa:.4f}", f"{p_sa:.6f}", ""])
        w.writerow(["B: αL_P+βL_Q+γL_R", f"{rho_b:.4f}", f"{rho_sb:.4f}", f"{p_sb:.6f}", json.dumps(params_sb)])
        w.writerow(["B': αL_P+βL_st+γL_R", f"{rho_bp:.4f}", f"{rho_sbp:.4f}", f"{p_sbp:.6f}", json.dumps(params_sbp)])
        w.writerow(["C: max(L_Q, lin)", f"{rho_c:.4f}", f"{rho_sc:.4f}", f"{p_sc:.6f}", json.dumps(params_sc)])
        w.writerow(["C': max(L_st, lin)", f"{rho_cp:.4f}", f"{rho_scp:.4f}", f"{p_scp:.6f}", json.dumps(params_scp)])
        w.writerow(["D: multiplicative", f"{rho_d:.4f}", f"{rho_sd:.4f}", f"{p_sd:.6f}", json.dumps(params_sd)])
    print(f"\nCSV written: {csv_path}")


if __name__ == "__main__":
    main()

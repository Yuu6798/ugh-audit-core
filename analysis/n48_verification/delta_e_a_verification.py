"""ΔE_A n=48 検証スクリプト

ΔE_A（パラメータフリー理論式）の品質予測力を n=48 で検証する。
出力: ha48_merged.csv, subgroup_analysis.csv, model_comparison_n48.csv, results_summary.md
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr

# --- Paths ---
BASE = Path(__file__).resolve().parent.parent.parent
DATA = BASE / "data"
OUT = Path(__file__).resolve().parent

HA20_CSV = DATA / "human_annotation_20" / "human_annotation_20_completed.csv"
HA28_CSV = DATA / "human_annotation_28" / "annotation_28_results.csv"
HA48_CSV = DATA / "human_annotation_48" / "annotation_48_merged.csv"
GATE_CSV = DATA / "gate_results" / "structural_gate_summary.csv"
CASCADE_CSV = DATA / "eval" / "audit_102_main_baseline_cascade.csv"
QUESTIONS_JSONL = DATA / "question_sets" / "ugh-audit-100q-v3-1.json.txtl.txt"


# --- 確定済み重み (ugh_calculator.py / engine/models.py) ---
W_F1 = 5
W_F2 = 25
W_F3 = 5
W_F4 = 5
W_SUM = W_F1 + W_F2 + W_F3 + W_F4  # = 40
WEIGHT_S = 2
WEIGHT_C = 1

# Model C' パラメータ (n=20 暫定値)
QUALITY_ALPHA = 0.4
QUALITY_BETA = 0.0
QUALITY_GAMMA = 0.8

# HA20 の ID リスト
HA20_IDS = set()


def load_ha20_ids() -> set:
    """HA20 の ID を取得"""
    ids = set()
    with open(HA20_CSV) as f:
        for row in csv.DictReader(f):
            ids.add(row["id"])
    return ids


def load_structural_gate_t0() -> dict:
    """structural_gate_summary.csv から t=0.0 の行を取得"""
    gate = {}
    with open(GATE_CSV) as f:
        for row in csv.DictReader(f):
            if row["temperature"] == "0.0":
                gate[row["id"]] = {
                    "f1_flag": float(row["f1_flag"]),
                    "f2_flag": float(row["f2_flag"]),
                    "f3_flag": float(row["f3_flag"]),
                    "f4_flag": float(row["f4_flag"]),
                    "fail_max": float(row["fail_max"]),
                    "verdict": row["verdict"],
                }
    return gate


def load_cascade_baseline() -> dict:
    """cascade baseline から system_hit_rate と delta_e_cosine を取得"""
    baseline = {}
    with open(CASCADE_CSV) as f:
        for row in csv.DictReader(f):
            baseline[row["id"]] = {
                "system_hit_rate": float(row["C"]),
                "delta_e_cosine": float(row["dE"]),
                "system_hits": int(row["hits"]),
                "system_total": int(row["total"]),
            }
    return baseline


def load_ha20_human_hit_rates() -> dict:
    """HA20 の human_hit_rate を propositions_hit フィールドから算出"""
    rates = {}
    with open(HA20_CSV) as f:
        for row in csv.DictReader(f):
            ph = row["propositions_hit"]  # e.g. "2/3"
            if "/" in ph:
                num, den = ph.split("/")
                rates[row["id"]] = float(num) / float(den)
            else:
                rates[row["id"]] = 0.0
    return rates


def load_core_propositions_count() -> dict:
    """各質問の core_propositions 数を取得"""
    counts = {}
    with open(QUESTIONS_JSONL) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            counts[d["id"]] = len(d.get("core_propositions", []))
    return counts


def compute_human_hit_rate_ha28(c_annotation: int, n_props: int) -> float:
    """HA28 の C アノテーション (1-3) から human_hit_rate を推定

    HA20 実績:
      C=1 → mean hit_rate ≈ 0.24 (0/3 or 1/3)
      C=2 → mean hit_rate ≈ 0.52 (1/3 or 2/3)
      C=3 → mean hit_rate = 1.00 (3/3)

    (C-1)/2 マッピングは HA20 実績と良好に一致:
      C=1→0.0, C=2→0.5, C=3→1.0
    """
    return (c_annotation - 1) / 2.0


def calc_s_score(f1: float, f2: float, f3: float, f4: float) -> float:
    """S = 1 - (w1*f1 + w2*f2 + w3*f3 + w4*f4) / 40"""
    s = 1.0 - (W_F1 * f1 + W_F2 * f2 + W_F3 * f3 + W_F4 * f4) / W_SUM
    return max(0.0, min(1.0, s))


def calc_delta_e_a(s_score: float, c: float) -> float:
    """ΔE_A = (2*(1-S)^2 + 1*(1-C)^2) / 3"""
    de = (WEIGHT_S * (1 - s_score) ** 2 + WEIGHT_C * (1 - c) ** 2) / (WEIGHT_S + WEIGHT_C)
    return max(0.0, min(1.0, de))


def calc_model_c_prime(system_hit_rate: float, fail_max: float, delta_e_cosine: float) -> float:
    """Model C' ボトルネック型"""
    l_p = 1.0 - system_hit_rate
    l_struct = fail_max
    l_r = delta_e_cosine
    l_linear = QUALITY_ALPHA * l_p + QUALITY_BETA * l_struct + QUALITY_GAMMA * l_r
    l_op = max(l_struct, l_linear)
    return max(1.0, min(5.0, 5.0 - 4.0 * l_op))


def determine_group(qid: str, verdict_t0: str, ha20_ids: set) -> str:
    """グループ分類"""
    if qid in ha20_ids:
        return "HA20"
    if verdict_t0 == "fail":
        return "A_fail"
    elif verdict_t0 == "warn":
        return "B_warn"
    else:
        return "CD_pass"


def build_merged_table() -> list[dict]:
    """48件の統合テーブルを構築"""
    ha20_ids = load_ha20_ids()
    gate = load_structural_gate_t0()
    baseline = load_cascade_baseline()
    ha20_hr = load_ha20_human_hit_rates()
    n_props = load_core_propositions_count()

    # HA48 merged を読む
    rows = []
    with open(HA48_CSV) as f:
        for row in csv.DictReader(f):
            qid = row["id"]
            s_ann = int(row["S"])
            c_ann = int(row["C"])
            o_raw = row["O"]
            o = float(o_raw)
            category = row["category"]
            notes = row.get("notes", "")

            # 構造ゲート (t=0.0)
            g = gate.get(qid, {"f1_flag": 0, "f2_flag": 0, "f3_flag": 0, "f4_flag": 0,
                                "fail_max": 0, "verdict": "pass"})

            # system hit rate
            bl = baseline.get(qid, {"system_hit_rate": 0, "delta_e_cosine": 0})

            # human hit rate
            # v1 方法論: HA20 は propositions_hit から、HA28 は (C-1)/2 粗視化で統一
            # (v2 の命題単位復元は delta_e_a_recalc.py で実施)
            if qid in ha20_ids and qid in ha20_hr:
                human_hr = ha20_hr[qid]
            else:
                human_hr = compute_human_hit_rate_ha28(c_ann, n_props.get(qid, 3))

            # グループ
            group = determine_group(qid, g["verdict"], ha20_ids)

            # S_score 算出
            s_score = calc_s_score(g["f1_flag"], g["f2_flag"], g["f3_flag"], g["f4_flag"])

            # ΔE_A 算出
            de_a_ref = calc_delta_e_a(s_score, human_hr)
            de_a_sys = calc_delta_e_a(s_score, bl["system_hit_rate"])

            # Model C' 算出
            mc_pred = calc_model_c_prime(bl["system_hit_rate"], g["fail_max"], bl["delta_e_cosine"])

            rows.append({
                "id": qid,
                "group": group,
                "category": category,
                "S_ann": s_ann,
                "C_ann": c_ann,
                "O": o,
                "human_hit_rate": round(human_hr, 4),
                "system_hit_rate": round(bl["system_hit_rate"], 4),
                "f1_flag": g["f1_flag"],
                "f2_flag": g["f2_flag"],
                "f3_flag": g["f3_flag"],
                "f4_flag": g["f4_flag"],
                "fail_max": g["fail_max"],
                "S_score": round(s_score, 4),
                "delta_e_a_ref": round(de_a_ref, 4),
                "delta_e_a_sys": round(de_a_sys, 4),
                "delta_e_cosine": round(bl["delta_e_cosine"], 4),
                "model_c_prime": round(mc_pred, 4),
                "notes": notes,
            })

    return rows


def write_merged_csv(rows: list[dict]):
    """ha48_merged.csv 出力"""
    cols = [
        "id", "group", "category", "S_ann", "C_ann", "O",
        "human_hit_rate", "system_hit_rate",
        "f1_flag", "f2_flag", "f3_flag", "f4_flag", "fail_max",
        "S_score", "delta_e_a_ref", "delta_e_a_sys",
        "delta_e_cosine", "model_c_prime", "notes",
    ]
    outpath = OUT / "ha48_merged.csv"
    with open(outpath, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"[OK] {outpath}")


def run_correlations(rows: list[dict]) -> dict:
    """Task 3: Spearman ρ 算出"""
    scores_o = np.array([r["O"] for r in rows])
    de_ref = np.array([r["delta_e_a_ref"] for r in rows])
    de_sys = np.array([r["delta_e_a_sys"] for r in rows])
    sys_hr = np.array([r["system_hit_rate"] for r in rows])
    mc = np.array([r["model_c_prime"] for r in rows])

    # 予測スコア = 5 - 4 * ΔE
    pred_ref = 5 - 4 * de_ref
    pred_sys = 5 - 4 * de_sys

    rho_ref, p_ref = spearmanr(pred_ref, scores_o)
    rho_sys, p_sys = spearmanr(pred_sys, scores_o)
    rho_hr, p_hr = spearmanr(sys_hr, scores_o)
    rho_mc, p_mc = spearmanr(mc, scores_o)

    # ΔE 直接 (負の相関)
    rho_de_ref, p_de_ref = spearmanr(de_ref, scores_o)
    rho_de_sys, p_de_sys = spearmanr(de_sys, scores_o)

    results = {
        "n": len(rows),
        "rho_ref": rho_ref,
        "p_ref": p_ref,
        "rho_sys": rho_sys,
        "p_sys": p_sys,
        "rho_hr_only": rho_hr,
        "p_hr_only": p_hr,
        "rho_mc": rho_mc,
        "p_mc": p_mc,
        "rho_de_ref_direct": rho_de_ref,
        "p_de_ref_direct": p_de_ref,
        "rho_de_sys_direct": rho_de_sys,
        "p_de_sys_direct": p_de_sys,
    }
    return results


def run_loo_cv(rows: list[dict]) -> dict:
    """Task 4: LOO-CV"""
    n = len(rows)
    scores_o = np.array([r["O"] for r in rows])
    de_ref = np.array([r["delta_e_a_ref"] for r in rows])
    pred_ref = 5 - 4 * de_ref

    rho_list = []
    for i in range(n):
        idx = [j for j in range(n) if j != i]
        rho_i, _ = spearmanr(pred_ref[idx], scores_o[idx])
        rho_list.append(rho_i)

    rho_arr = np.array(rho_list)
    return {
        "mean": float(np.mean(rho_arr)),
        "std": float(np.std(rho_arr)),
        "min": float(np.min(rho_arr)),
        "max": float(np.max(rho_arr)),
    }


def run_subgroup_analysis(rows: list[dict]) -> list[dict]:
    """Task 5: サブグループ分析"""
    results = []

    def analyze(label: str, subset: list[dict]):
        if len(subset) < 3:
            results.append({
                "group": label,
                "n": len(subset),
                "O_mean": np.mean([r["O"] for r in subset]),
                "de_a_ref_mean": np.mean([r["delta_e_a_ref"] for r in subset]),
                "de_a_sys_mean": np.mean([r["delta_e_a_sys"] for r in subset]),
                "rho_ref": None,
                "p_ref": None,
                "note": "n<3, ρ算出不可",
            })
            return

        scores_o = np.array([r["O"] for r in subset])
        de_ref = np.array([r["delta_e_a_ref"] for r in subset])
        pred = 5 - 4 * de_ref
        rho, p = spearmanr(pred, scores_o)
        results.append({
            "group": label,
            "n": len(subset),
            "O_mean": round(float(np.mean(scores_o)), 3),
            "de_a_ref_mean": round(float(np.mean(de_ref)), 4),
            "de_a_sys_mean": round(float(np.mean([r["delta_e_a_sys"] for r in subset])), 4),
            "rho_ref": round(rho, 4),
            "p_ref": round(p, 6) if p >= 0.000001 else f"{p:.2e}",
            "note": "",
        })

    # グループ別
    for g in ["HA20", "A_fail", "B_warn", "CD_pass"]:
        sub = [r for r in rows if r["group"] == g]
        analyze(f"group:{g}", sub)

    # HA20 vs HA28
    ha28 = [r for r in rows if r["group"] != "HA20"]
    analyze("group:HA28_all", ha28)

    # カテゴリ別
    cats = sorted(set(r["category"] for r in rows))
    for c in cats:
        sub = [r for r in rows if r["category"] == c]
        analyze(f"cat:{c}", sub)

    # O値別
    low = [r for r in rows if r["O"] <= 2]
    mid = [r for r in rows if 2 < r["O"] <= 3]
    high = [r for r in rows if r["O"] >= 4]
    analyze("O<=2", low)
    analyze("O=3", mid)
    analyze("O>=4", high)

    return results


def run_sensitivity_analysis(rows: list[dict]) -> dict:
    """Task 7: 設計疑義ケース感度分析"""
    suspect_ids = {"q003", "q041", "q053"}

    clean = [r for r in rows if r["id"] not in suspect_ids]
    scores_o = np.array([r["O"] for r in clean])
    de_ref = np.array([r["delta_e_a_ref"] for r in clean])
    de_sys = np.array([r["delta_e_a_sys"] for r in clean])

    pred_ref = 5 - 4 * de_ref
    pred_sys = 5 - 4 * de_sys

    rho_ref, p_ref = spearmanr(pred_ref, scores_o)
    rho_sys, p_sys = spearmanr(pred_sys, scores_o)

    return {
        "n_clean": len(clean),
        "rho_ref_clean": rho_ref,
        "p_ref_clean": p_ref,
        "rho_sys_clean": rho_sys,
        "p_sys_clean": p_sys,
        "excluded": list(suspect_ids),
    }


def write_subgroup_csv(sg_results: list[dict]):
    """subgroup_analysis.csv 出力"""
    outpath = OUT / "subgroup_analysis.csv"
    cols = ["group", "n", "O_mean", "de_a_ref_mean", "de_a_sys_mean", "rho_ref", "p_ref", "note"]
    with open(outpath, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in sg_results:
            w.writerow(r)
    print(f"[OK] {outpath}")


def write_model_comparison(corr: dict, sensitivity: dict):
    """model_comparison_n48.csv 出力"""
    outpath = OUT / "model_comparison_n48.csv"
    with open(outpath, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["model", "n20_rho", "n48_rho", "n48_p", "note"])
        w.writerow(["ΔE_A (reference)", "0.928", f"{corr['rho_ref']:.4f}",
                     f"{corr['p_ref']:.2e}", "human hit_rate使用"])
        w.writerow(["ΔE_A (system)", "—", f"{corr['rho_sys']:.4f}",
                     f"{corr['p_sys']:.2e}", "system hit_rate使用"])
        w.writerow(["Model C'", "0.80", f"{corr['rho_mc']:.4f}",
                     f"{corr['p_mc']:.2e}", "n=20暫定パラメータ"])
        w.writerow(["hit_rate only", "0.40", f"{corr['rho_hr_only']:.4f}",
                     f"{corr['p_hr_only']:.2e}", "system hit_rate単独"])
        w.writerow(["ΔE_A ref (n=45 clean)", "—", f"{sensitivity['rho_ref_clean']:.4f}",
                     f"{sensitivity['p_ref_clean']:.2e}", "疑義3件除外"])
    print(f"[OK] {outpath}")


def write_results_summary(corr: dict, loo: dict, sg_results: list[dict], sensitivity: dict):
    """results_summary.md 出力"""
    outpath = OUT / "results_summary.md"

    # 受理基準の判定
    rho_ref = corr["rho_ref"]
    rho_sys = corr["rho_sys"]
    degradation = 0.928 - rho_ref
    loo_std = loo["std"]

    # サブグループ序列チェック
    o_groups = {}
    for sg in sg_results:
        if sg["group"] in ("O<=2", "O=3", "O>=4"):
            o_groups[sg["group"]] = sg["de_a_ref_mean"]

    ordinal_ok = (
        o_groups.get("O<=2", 0) > o_groups.get("O=3", 0) > o_groups.get("O>=4", 0)
    )

    criteria = {
        "ref_rho >= 0.85": rho_ref >= 0.85,
        "degradation <= 0.08": degradation <= 0.08,
        "sys_rho >= 0.50": rho_sys >= 0.50,
        "loo_std <= 0.05": loo_std <= 0.05,
        "ordinal_consistency": ordinal_ok,
    }
    all_pass = all(criteria.values())
    verdict = "GO" if all_pass else ("CONDITIONAL" if sum(criteria.values()) >= 3 else "NO-GO")

    lines = [
        "# ΔE_A n=48 検証結果サマリー",
        "",
        f"## 判定: **{verdict}**",
        "",
        "## 受理基準",
        "",
        "| 基準 | 閾値 | 実測値 | 判定 |",
        "|------|------|--------|------|",
    ]
    for name, passed in criteria.items():
        if name == "ref_rho >= 0.85":
            val = f"{rho_ref:.4f}"
        elif name == "degradation <= 0.08":
            val = f"{degradation:.4f}"
        elif name == "sys_rho >= 0.50":
            val = f"{rho_sys:.4f}"
        elif name == "loo_std <= 0.05":
            val = f"{loo_std:.4f}"
        else:
            val = str(ordinal_ok)
        lines.append(f"| {name} | — | {val} | {'PASS' if passed else 'FAIL'} |")

    lines += [
        "",
        "## 相関係数",
        "",
        "| Model | n=20 ρ | n=48 ρ | p-value |",
        "|-------|--------|--------|---------|",
        f"| ΔE_A (reference) | 0.928 | {rho_ref:.4f} | {corr['p_ref']:.2e} |",
        f"| ΔE_A (system) | — | {rho_sys:.4f} | {corr['p_sys']:.2e} |",
        f"| Model C' | 0.80 | {corr['rho_mc']:.4f} | {corr['p_mc']:.2e} |",
        f"| hit_rate only | 0.40 | {corr['rho_hr_only']:.4f} | {corr['p_hr_only']:.2e} |",
        "",
        "## LOO-CV",
        "",
        f"- mean: {loo['mean']:.4f}",
        f"- std: {loo['std']:.4f}",
        f"- min: {loo['min']:.4f}",
        f"- max: {loo['max']:.4f}",
        "",
        "## サブグループ序列 (ΔE_A reference 平均)",
        "",
        "| グループ | n | O平均 | ΔE_A平均 |",
        "|---------|---|-------|----------|",
    ]
    for sg in sg_results:
        if sg["group"] in ("O<=2", "O=3", "O>=4"):
            lines.append(
                f"| {sg['group']} | {sg['n']} | {sg['O_mean']} | {sg['de_a_ref_mean']} |"
            )

    lines += [
        "",
        "## 感度分析 (疑義3件除外, n=45)",
        "",
        f"- reference ρ: {sensitivity['rho_ref_clean']:.4f} (p={sensitivity['p_ref_clean']:.2e})",
        f"- system ρ: {sensitivity['rho_sys_clean']:.4f} (p={sensitivity['p_sys_clean']:.2e})",
        f"- 除外: {', '.join(sensitivity['excluded'])}",
        "",
        "## 制約事項",
        "",
        "- HA20 (20件) の O は t=0.7 の回答に対するスコア。構造ゲートは t=0.0 で統一。",
        "  t 不一致は既知の制約。",
        "- HA28 (28件) の human_hit_rate は C アノテーション (1-3) から (C-1)/2 で推定。",
        "  HA20 実績と良好に一致するが、正確な命題単位カウントではない。",
        f"- S の重み: f1={W_F1}, f2={W_F2}, f3={W_F3}, f4={W_F4} (確定値、ugh_calculator.py)",
        f"- ΔE: WEIGHT_S={WEIGHT_S}, WEIGHT_C={WEIGHT_C} (確定値)",
        "",
    ]

    with open(outpath, "w") as f:
        f.write("\n".join(lines))
    print(f"[OK] {outpath}")


def main():
    print("=" * 60)
    print("ΔE_A n=48 検証")
    print("=" * 60)

    # Task 1-2: データ統合 + ΔE_A 算出
    print("\n--- Task 1-2: データ統合 + ΔE_A 算出 ---")
    rows = build_merged_table()
    print(f"統合件数: {len(rows)}")
    write_merged_csv(rows)

    # Task 3: 相関算出
    print("\n--- Task 3: Spearman ρ 算出 ---")
    corr = run_correlations(rows)
    print(f"ΔE_A reference ρ = {corr['rho_ref']:.4f} (p={corr['p_ref']:.2e})")
    print(f"ΔE_A system    ρ = {corr['rho_sys']:.4f} (p={corr['p_sys']:.2e})")
    print(f"Model C'       ρ = {corr['rho_mc']:.4f} (p={corr['p_mc']:.2e})")
    print(f"hit_rate only  ρ = {corr['rho_hr_only']:.4f} (p={corr['p_hr_only']:.2e})")

    # Task 4: LOO-CV
    print("\n--- Task 4: LOO-CV ---")
    loo = run_loo_cv(rows)
    print(f"mean={loo['mean']:.4f}, std={loo['std']:.4f}, "
          f"min={loo['min']:.4f}, max={loo['max']:.4f}")

    # Task 5: サブグループ分析
    print("\n--- Task 5: サブグループ分析 ---")
    sg = run_subgroup_analysis(rows)
    write_subgroup_csv(sg)
    for s in sg:
        rho_str = f"ρ={s['rho_ref']:.3f}" if s["rho_ref"] is not None else "ρ=N/A"
        print(f"  {s['group']:25s} n={s['n']:2d}  O={s['O_mean']:.2f}  "
              f"ΔE_A={s['de_a_ref_mean']:.4f}  {rho_str}")

    # Task 6: Model 比較
    print("\n--- Task 6: Model 比較 ---")

    # Task 7: 感度分析
    print("\n--- Task 7: 感度分析 ---")
    sensitivity = run_sensitivity_analysis(rows)
    print(f"n=45 reference ρ = {sensitivity['rho_ref_clean']:.4f} "
          f"(p={sensitivity['p_ref_clean']:.2e})")
    print(f"n=45 system    ρ = {sensitivity['rho_sys_clean']:.4f} "
          f"(p={sensitivity['p_sys_clean']:.2e})")

    # 出力
    write_model_comparison(corr, sensitivity)
    write_results_summary(corr, loo, sg, sensitivity)

    # 判定 (write_results_summary と同一の5基準)
    print("\n" + "=" * 60)
    degradation = 0.928 - corr["rho_ref"]
    print(f"reference ρ: {corr['rho_ref']:.4f} (n=20: 0.928, 劣化: {degradation:.4f})")
    print(f"system ρ:    {corr['rho_sys']:.4f}")
    print(f"LOO-CV std:  {loo['std']:.4f}")

    # サブグループ序列チェック
    o_groups = {}
    for s in sg:
        if s["group"] in ("O<=2", "O=3", "O>=4"):
            o_groups[s["group"]] = s["de_a_ref_mean"]
    ordinal_ok = (
        o_groups.get("O<=2", 0) > o_groups.get("O=3", 0) > o_groups.get("O>=4", 0)
    )
    print(f"序列一貫性:  {ordinal_ok}")

    criteria_pass = sum([
        corr["rho_ref"] >= 0.85,
        degradation <= 0.08,
        corr["rho_sys"] >= 0.50,
        loo["std"] <= 0.05,
        ordinal_ok,
    ])
    if criteria_pass == 5:
        verdict = "GO"
    elif criteria_pass >= 3:
        verdict = "CONDITIONAL"
    else:
        verdict = "NO-GO"
    print(f"\n判定: {verdict} ({criteria_pass}/5 PASS)")
    print("=" * 60)


if __name__ == "__main__":
    main()

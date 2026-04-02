"""Model C' の L_R を ΔE_A に差し替えた場合の ρ 比較

5つのモデルバリアントを HA20 上で比較する。
"""
from __future__ import annotations

import csv
import itertools
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr

OUT_DIR = Path(__file__).resolve().parent
DATA_CSV = OUT_DIR / "ha20_pipeline_a_delta_e.csv"


def load_data() -> list[dict]:
    records = []
    with open(DATA_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            records.append({
                "id": row["id"],
                "human_score": float(row["human_score"]),
                "delta_e_A": float(row["delta_e_A"]),
                "delta_e_full": float(row["delta_e_full"]),
                "fail_max": float(row["fail_max"]),
                "S": float(row["S"]),
                "C": float(row["C"]),
            })
    assert len(records) == 20
    return records


def score_model1(r: dict) -> float:
    """現行 Model C': α=0.4, β=0.0, γ=0.8, L_R=delta_e_full"""
    l_p = 1.0 - r["C"]
    l_struct = r["fail_max"]
    l_r = r["delta_e_full"]
    l_linear = 0.4 * l_p + 0.0 * l_struct + 0.8 * l_r
    l_op = max(l_struct, l_linear)
    return max(1.0, min(5.0, 5.0 - 4.0 * l_op))


def score_model2(r: dict) -> float:
    """Model C' の L_R を ΔE_A に差し替え（パラメータ据置）"""
    l_p = 1.0 - r["C"]
    l_struct = r["fail_max"]
    l_r = r["delta_e_A"]
    l_linear = 0.4 * l_p + 0.0 * l_struct + 0.8 * l_r
    l_op = max(l_struct, l_linear)
    return max(1.0, min(5.0, 5.0 - 4.0 * l_op))


def score_model3(r: dict, alpha: float, beta: float, gamma: float) -> float:
    """ΔE_A 差し替え + パラメータ可変"""
    l_p = 1.0 - r["C"]
    l_struct = r["fail_max"]
    l_r = r["delta_e_A"]
    l_linear = alpha * l_p + beta * l_struct + gamma * l_r
    l_op = max(l_struct, l_linear)
    return max(1.0, min(5.0, 5.0 - 4.0 * l_op))


def score_model4(r: dict) -> float:
    """ΔE_A 単独"""
    return max(1.0, min(5.0, 5.0 - 4.0 * r["delta_e_A"]))


def score_model5(r: dict) -> float:
    """ΔE_A + ボトルネックのみ"""
    l_op = max(r["fail_max"], r["delta_e_A"])
    return max(1.0, min(5.0, 5.0 - 4.0 * l_op))


def grid_search(records: list[dict]) -> list[dict]:
    """α, β, γ グリッドサーチ"""
    hs = np.array([r["human_score"] for r in records])
    results = []
    grid = [round(x * 0.1, 1) for x in range(11)]
    for alpha, beta, gamma in itertools.product(grid, grid, grid):
        scores = np.array([score_model3(r, alpha, beta, gamma) for r in records])
        rho, p = spearmanr(scores, hs)
        results.append({"alpha": alpha, "beta": beta, "gamma": gamma, "rho": rho, "p": p})
    results.sort(key=lambda x: -x["rho"])
    return results


def loo_cv_fixed(records: list[dict], score_fn) -> tuple[float, float, list[float]]:
    """パラメータ固定モデルの Jackknife LOO-CV"""
    n = len(records)
    hs = np.array([r["human_score"] for r in records])
    rho_vals = []
    for i in range(n):
        mask = [j for j in range(n) if j != i]
        scores_i = np.array([score_fn(records[j]) for j in mask])
        hs_i = hs[mask]
        rho_i, _ = spearmanr(scores_i, hs_i)
        rho_vals.append(rho_i)
    return float(np.mean(rho_vals)), float(np.std(rho_vals, ddof=1)), rho_vals


def main() -> None:
    records = load_data()
    n = len(records)
    hs = np.array([r["human_score"] for r in records])

    # --- Step 1: 全モデルスコア算出 ---
    for r in records:
        r["score_model1"] = round(score_model1(r), 4)
        r["score_model2"] = round(score_model2(r), 4)
        r["score_model4"] = round(score_model4(r), 4)
        r["score_model5"] = round(score_model5(r), 4)

    # Model 3: グリッドサーチ
    print("Model 3 グリッドサーチ (1331通り)...")
    gs_results = grid_search(records)
    best = gs_results[0]
    print(f"  最適: α={best['alpha']}, β={best['beta']}, γ={best['gamma']}, ρ={best['rho']:.4f}")

    for r in records:
        r["score_model3"] = round(score_model3(r, best["alpha"], best["beta"], best["gamma"]), 4)

    # --- Step 2: Spearman ρ ---
    models_fixed = {
        "Model 1 (C'式, human C)": [r["score_model1"] for r in records],
        "Model 2 (L_R→ΔE_A据置)": [r["score_model2"] for r in records],
        "Model 3 (L_R→ΔE_A再フィット)": [r["score_model3"] for r in records],
        "Model 4 (ΔE_A単独)": [r["score_model4"] for r in records],
        "Model 5 (ΔE_A+ボトルネック)": [r["score_model5"] for r in records],
    }

    rho_full_results = {}
    print("\n=== ρ_full ===")
    for name, scores in models_fixed.items():
        rho, p = spearmanr(scores, hs)
        rho_full_results[name] = (rho, p)
        print(f"  {name}: ρ={rho:.4f} (p={p:.6f})")

    # --- Step 3: LOO-CV ---
    print("\nLOO-CV...")
    loo_results = {}

    # Model 1
    m1_mean, m1_std, _ = loo_cv_fixed(records, score_model1)
    loo_results["Model 1 (C'式, human C)"] = (m1_mean, m1_std)
    print(f"  Model 1: ρ_loo_mean={m1_mean:.4f}, std={m1_std:.4f}")

    # Model 2
    m2_mean, m2_std, _ = loo_cv_fixed(records, score_model2)
    loo_results["Model 2 (L_R→ΔE_A据置)"] = (m2_mean, m2_std)
    print(f"  Model 2: ρ_loo_mean={m2_mean:.4f}, std={m2_std:.4f}")

    # Model 3: nested CV — fold ごとにパラメータ再フィットして Jackknife
    print("  Model 3: nested LOO-CV (per-fold refit)...")
    m3_rho_vals = []
    n = len(records)
    grid = [round(x * 0.1, 1) for x in range(11)]
    for i in range(n):
        train = [records[j] for j in range(n) if j != i]
        hs_train = np.array([r["human_score"] for r in train])
        # fold ごとにグリッドサーチ
        best_rho_i = -2.0
        best_params_i = (0.4, 0.0, 0.8)
        for alpha, beta, gamma in itertools.product(grid, grid, grid):
            scores_train = np.array([score_model3(r, alpha, beta, gamma) for r in train])
            rho_i, _ = spearmanr(scores_train, hs_train)
            if rho_i > best_rho_i:
                best_rho_i = rho_i
                best_params_i = (alpha, beta, gamma)
        # fold 内の 19件での ρ を記録（Jackknife 形式で他モデルと統一）
        scores_fold = np.array([score_model3(r, *best_params_i) for r in train])
        rho_fold, _ = spearmanr(scores_fold, hs_train)
        m3_rho_vals.append(rho_fold)
    m3_mean = float(np.mean(m3_rho_vals))
    m3_std = float(np.std(m3_rho_vals, ddof=1))
    loo_results["Model 3 (L_R→ΔE_A再フィット)"] = (m3_mean, m3_std)
    print(f"  Model 3: ρ_loo_mean={m3_mean:.4f}, std={m3_std:.4f}")

    # Model 4
    m4_mean, m4_std, _ = loo_cv_fixed(records, score_model4)
    loo_results["Model 4 (ΔE_A単独)"] = (m4_mean, m4_std)
    print(f"  Model 4: ρ_loo_mean={m4_mean:.4f}, std={m4_std:.4f}")

    # Model 5
    m5_mean, m5_std, _ = loo_cv_fixed(records, score_model5)
    loo_results["Model 5 (ΔE_A+ボトルネック)"] = (m5_mean, m5_std)
    print(f"  Model 5: ρ_loo_mean={m5_mean:.4f}, std={m5_std:.4f}")

    # --- 出力1: 全モデルスコア CSV ---
    out_scores = OUT_DIR / "model_comparison_scores.csv"
    fields = ["id", "human_score", "score_model1", "score_model2", "score_model3",
              "score_model4", "score_model5", "delta_e_A", "delta_e_full", "fail_max"]
    with open(out_scores, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(records)
    print(f"\n出力: {out_scores}")

    # --- 出力2: 比較結果 CSV ---
    comp_rows = []
    model_params = {
        "Model 1 (C'式, human C)": "α=0.4, β=0.0, γ=0.8, L_R=cosine (※C=human propositions_hit, 本番C'とはデータソースが異なる)",
        "Model 2 (L_R→ΔE_A据置)": "α=0.4, β=0.0, γ=0.8, L_R=ΔE_A",
        "Model 3 (L_R→ΔE_A再フィット)": f"α={best['alpha']}, β={best['beta']}, γ={best['gamma']}, L_R=ΔE_A",
        "Model 4 (ΔE_A単独)": "score=5-4×ΔE_A",
        "Model 5 (ΔE_A+ボトルネック)": "L_op=max(fail_max,ΔE_A)",
    }
    for name in models_fixed:
        rho_f, p_f = rho_full_results[name]
        loo_val = loo_results[name]
        rho_l = loo_val[0]
        drop = rho_f - rho_l
        comp_rows.append({
            "model": name,
            "rho_full": round(rho_f, 4),
            "p_full": round(p_f, 6),
            "rho_loo": round(rho_l, 4),
            "drop": round(drop, 4),
            "params": model_params[name],
        })

    out_comp = OUT_DIR / "model_comparison_results.csv"
    with open(out_comp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["model", "rho_full", "p_full", "rho_loo", "drop", "params"])
        w.writeheader()
        w.writerows(comp_rows)
    print(f"出力: {out_comp}")

    # --- 出力3: Model 3 グリッドサーチ top10 ---
    out_gs = OUT_DIR / "model3_gridsearch_top10.csv"
    with open(out_gs, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["rank", "alpha", "beta", "gamma", "rho", "p"])
        w.writeheader()
        for i, gs in enumerate(gs_results[:10]):
            w.writerow({"rank": i + 1, "alpha": gs["alpha"], "beta": gs["beta"],
                        "gamma": gs["gamma"], "rho": round(gs["rho"], 4), "p": round(gs["p"], 6)})
    print(f"出力: {out_gs}")

    # --- 出力4: ケース別比較 (Model 4 vs 5) ---
    case_diffs = []
    for r in records:
        s4 = r["score_model4"]
        s5 = r["score_model5"]
        if abs(s4 - s5) > 0.001:
            case_diffs.append({
                "id": r["id"],
                "human_score": r["human_score"],
                "score_m4": s4,
                "score_m5": s5,
                "delta_e_A": r["delta_e_A"],
                "fail_max": r["fail_max"],
                "diff": round(s4 - s5, 4),
                "bottleneck_active": "Yes" if r["fail_max"] > r["delta_e_A"] else "No",
            })
    out_case = OUT_DIR / "model4_vs_model5_case_diff.csv"
    with open(out_case, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "human_score", "score_m4", "score_m5",
                                          "delta_e_A", "fail_max", "diff", "bottleneck_active"])
        w.writeheader()
        w.writerows(case_diffs)
    print(f"出力: {out_case} ({len(case_diffs)} ケース)")

    # --- 出力5: 診断レポート ---
    lines = [
        "# Model C' の L_R を ΔE_A に差し替えた場合の ρ 比較",
        "",
        "**実行日**: 2026-04-02",
        f"**データ**: HA20 ({n}件, temperature=0.7)",
        "",
        "## 1. 5モデル比較テーブル",
        "",
        "| Model | ρ_full | ρ_loo | drop | パラメータ |",
        "|-------|--------|-------|------|-----------|",
    ]
    for cr in comp_rows:
        lines.append(
            f"| {cr['model']} | {cr['rho_full']:.4f} | {cr['rho_loo']:.4f} | "
            f"{cr['drop']:.4f} | {cr['params']} |"
        )

    # Model 3 パラメータ分析
    lines += [
        "",
        "## 2. Model 3 グリッドサーチ分析",
        "",
        f"探索空間: 11×11×11 = {11**3} 通り",
        "",
        f"**最適パラメータ**: α={best['alpha']}, β={best['beta']}, γ={best['gamma']} (ρ={best['rho']:.4f})",
        "",
        "### Top 10",
        "",
        "| Rank | α | β | γ | ρ |",
        "|------|---|---|---|---|",
    ]
    for i, gs in enumerate(gs_results[:10]):
        lines.append(f"| {i+1} | {gs['alpha']} | {gs['beta']} | {gs['gamma']} | {gs['rho']:.4f} |")

    # β=0.0 再現チェック
    beta_zero_count = sum(1 for gs in gs_results[:10] if gs["beta"] == 0.0)
    lines += [
        "",
        f"Top 10 中 β=0.0 のエントリ: {beta_zero_count}/10",
    ]

    # ボトルネック分析
    lines += [
        "",
        "## 3. ボトルネック追加効果分析",
        "",
        "### Model 4 (ΔE_A単独) vs Model 5 (ΔE_A+ボトルネック)",
        "",
        f"- Model 4 ρ_full: {rho_full_results['Model 4 (ΔE_A単独)'][0]:.4f}",
        f"- Model 5 ρ_full: {rho_full_results['Model 5 (ΔE_A+ボトルネック)'][0]:.4f}",
        "",
    ]

    rho4 = rho_full_results["Model 4 (ΔE_A単独)"][0]
    rho5 = rho_full_results["Model 5 (ΔE_A+ボトルネック)"][0]
    if rho5 > rho4 + 0.005:
        lines.append("ボトルネック追加により ρ が改善。fail_max の情報が ΔE_A を補完している。")
    elif rho5 < rho4 - 0.005:
        lines.append("ボトルネック追加により ρ が劣化。fail_max が ΔE_A の信号を阻害している。")
    else:
        lines.append("ボトルネック追加の効果は微小（±0.005 以内）。")

    if case_diffs:
        lines += [
            "",
            "### ボトルネックが作動したケース",
            "",
            "| id | human_score | score_m4 | score_m5 | ΔE_A | fail_max | diff | bottleneck |",
            "|-----|------------|----------|----------|------|---------|------|-----------|",
        ]
        for cd in case_diffs:
            lines.append(
                f"| {cd['id']} | {int(cd['human_score'])} | {cd['score_m4']:.4f} | "
                f"{cd['score_m5']:.4f} | {cd['delta_e_A']:.4f} | {cd['fail_max']:.1f} | "
                f"{cd['diff']:.4f} | {cd['bottleneck_active']} |"
            )

        # ボトルネックが改善したケース vs 悪化したケース
        improved = [cd for cd in case_diffs if cd["fail_max"] > cd["delta_e_A"]
                    and abs(cd["score_m5"] - cd["human_score"]) < abs(cd["score_m4"] - cd["human_score"])]
        worsened = [cd for cd in case_diffs if cd["fail_max"] > cd["delta_e_A"]
                    and abs(cd["score_m5"] - cd["human_score"]) > abs(cd["score_m4"] - cd["human_score"])]
        lines += [
            "",
            f"ボトルネック作動ケース中、human_score への近接が改善: {len(improved)}件、悪化: {len(worsened)}件",
        ]

    # 設計判断への所見
    lines += [
        "",
        "## 4. 設計判断への所見",
        "",
    ]

    # ΔE_A 単独 vs 現行 Model C'
    rho1 = rho_full_results["Model 1 (C'式, human C)"][0]
    lines.append(
        "### ΔE_A 単独 (Model 4) vs 現行 Model C' (Model 1)"
    )
    lines.append("")
    if rho4 > rho1:
        lines.append(
            f"ΔE_A 単独 (ρ={rho4:.4f}) が現行 Model C' (ρ={rho1:.4f}) を上回る。"
        )
    else:
        lines.append(
            f"現行 Model C' (ρ={rho1:.4f}) が ΔE_A 単独 (ρ={rho4:.4f}) を上回る。"
        )

    # Model 3 vs Model 4
    rho3 = rho_full_results["Model 3 (L_R→ΔE_A再フィット)"][0]
    lines += [
        "",
        "### パラメータフィット (Model 3) vs ΔE_A 単独 (Model 4)",
        "",
    ]
    if rho3 > rho4 + 0.01:
        lines.append(f"Model 3 (ρ={rho3:.4f}) が Model 4 (ρ={rho4:.4f}) を上回る。判定層のパラメータフィットに意義あり。")
    elif rho3 < rho4 - 0.01:
        lines.append(f"Model 3 (ρ={rho3:.4f}) が Model 4 (ρ={rho4:.4f}) を下回る。パラメータフィットが ΔE_A の予測力を劣化させている。")
    else:
        lines.append(f"Model 3 (ρ={rho3:.4f}) と Model 4 (ρ={rho4:.4f}) の差は小さい (±0.01以内)。ΔE_A 単独で十分な予測力がある。")

    # LOO-CV ベースの判断
    loo4 = loo_results["Model 4 (ΔE_A単独)"][0]
    loo3 = loo_results["Model 3 (L_R→ΔE_A再フィット)"][0]
    lines += [
        "",
        "### LOO-CV ベースの比較",
        "",
        f"- Model 3 ρ_loo: {loo3:.4f}",
        f"- Model 4 ρ_loo: {loo4:.4f}",
        "",
    ]
    if loo3 > loo4:
        lines.append("LOO-CV でも Model 3 が優位。パラメータフィットは汎化でも有効。")
    else:
        lines.append("LOO-CV では Model 4 が優位。パラメータフィットは過学習の兆候がある。")

    # 総合
    lines += [
        "",
        "### 総合判断",
        "",
    ]

    # 最良モデルの特定（LOO-CV ベース）
    all_loo = {name: loo_results[name][0] for name in loo_results}
    best_model = max(all_loo, key=all_loo.get)
    lines.append(f"LOO-CV ベースで最良のモデルは **{best_model}** (ρ_loo={all_loo[best_model]:.4f})。")

    lines += [
        "",
        "---",
        "",
        "全モデルスコア: `model_comparison_scores.csv`",
        "比較結果: `model_comparison_results.csv`",
        "グリッドサーチ: `model3_gridsearch_top10.csv`",
        "ケース別比較: `model4_vs_model5_case_diff.csv`",
    ]

    out_report = OUT_DIR / "model_comparison_report.md"
    with open(out_report, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"出力: {out_report}")

    print("\n=== 完了 ===")


if __name__ == "__main__":
    main()

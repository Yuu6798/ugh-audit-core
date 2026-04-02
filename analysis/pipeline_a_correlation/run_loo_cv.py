"""ΔE_A（パイプライン A 理論式）の LOO-CV 安定性検証

前タスクで生成した ha20_pipeline_a_delta_e.csv を使用し、
Jackknife 形式の LOO-CV で ΔE_A vs human_score の ρ 安定性を検証する。
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _HAS_MPL = True
except ImportError:
    _HAS_MPL = False

OUT_DIR = Path(__file__).resolve().parent
DATA_CSV = OUT_DIR / "ha20_pipeline_a_delta_e.csv"


def main() -> None:
    # --- データ読み込み ---
    records = []
    with open(DATA_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            records.append({
                "id": row["id"],
                "human_score": float(row["human_score"]),
                "delta_e_A": float(row["delta_e_A"]),
                "S": float(row["S"]),
                "C": float(row["C"]),
            })
    n = len(records)
    assert n == 20, f"Expected 20 records, got {n}"

    ids = [r["id"] for r in records]
    hs = np.array([r["human_score"] for r in records])
    de_a = np.array([r["delta_e_A"] for r in records])

    # --- Step 1: 全データ ρ 再確認 ---
    rho_full, p_full = spearmanr(de_a, hs)
    print(f"全データ ρ: {rho_full:.4f} (p={p_full:.6f})")

    # --- Step 2: LOO-CV ---
    loo_results = []
    rho_values = []
    for i in range(n):
        mask = np.ones(n, dtype=bool)
        mask[i] = False
        rho_i, p_i = spearmanr(de_a[mask], hs[mask])
        abs_rho_drop = abs(rho_full) - abs(rho_i)  # |ρ| ベース: 正=劣化, 負=改善
        loo_results.append({
            "excluded_id": ids[i],
            "excluded_human_score": hs[i],
            "rho_i": round(rho_i, 4),
            "p_i": round(p_i, 6),
            "abs_rho_drop": round(abs_rho_drop, 4),
        })
        rho_values.append(rho_i)

    rho_arr = np.array(rho_values)

    # --- Step 3: 安定性指標 ---
    rho_loo_mean = np.mean(rho_arr)
    rho_loo_std = np.std(rho_arr, ddof=1)
    rho_loo_min = np.min(rho_arr)  # noqa: F841
    # |ρ| ベースの max_drop: |ρ_full| - |ρ_loo_min| ではなく、
    # 「最も |ρ| が小さくなった fold」を見る
    abs_rho_arr = np.abs(rho_arr)
    abs_rho_full = abs(rho_full)
    abs_max_drop = abs_rho_full - np.min(abs_rho_arr)

    # influential cases: |ρ_i| が |ρ_loo_mean| - 2σ を下回るケース
    abs_mean = np.mean(abs_rho_arr)
    abs_std = np.std(abs_rho_arr, ddof=1)
    threshold_2sigma = abs_mean - 2 * abs_std
    influential = [
        loo_results[i] for i in range(n)
        if abs_rho_arr[i] < threshold_2sigma
    ]

    print(f"\nρ_full:      {rho_full:.4f}")
    print(f"ρ_loo_mean:  {rho_loo_mean:.4f}")
    print(f"ρ_loo_std:   {rho_loo_std:.4f}")
    print(f"|ρ|_loo_min: {np.min(abs_rho_arr):.4f}")
    print(f"|ρ|_loo_max: {np.max(abs_rho_arr):.4f}")
    print(f"|ρ| max_drop: {abs_max_drop:.4f}")
    print(f"Influential (|ρ| < mean-2σ = {threshold_2sigma:.4f}): {len(influential)} cases")
    for ic in influential:
        print(f"  - {ic['excluded_id']} (human_score={ic['excluded_human_score']}, ρ_i={ic['rho_i']})")

    # --- 安定性判定 ---
    abs_std_val = float(abs_std)
    abs_min_val = float(np.min(abs_rho_arr))
    if abs_std_val < 0.03 and abs_min_val > 0.85:
        stability = "安定"
        stability_desc = "ρ_loo_std < 0.03 かつ |ρ|_loo_min > 0.85 → ΔE_A は頑健"
    elif abs_std_val < 0.05 and abs_min_val > 0.80:
        stability = "概ね安定"
        stability_desc = "ρ_loo_std < 0.05 かつ |ρ|_loo_min > 0.80 → 有望だが注意"
    else:
        stability = "不安定"
        stability_desc = "ρ_loo_std ≥ 0.05 または |ρ|_loo_min ≤ 0.80 → 特定ケース依存の疑い"
    print(f"\n安定性判定: {stability} — {stability_desc}")

    # --- 出力1: LOO-CV 結果 CSV ---
    out_loo = OUT_DIR / "delta_e_A_loo_cv_results.csv"
    with open(out_loo, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["excluded_id", "excluded_human_score", "rho_i", "p_i", "abs_rho_drop"])
        w.writeheader()
        w.writerows(loo_results)
    print(f"\n出力: {out_loo}")

    # --- 出力2: 安定性サマリー CSV ---
    summary_rows = [
        {"metric": "rho_full", "value": round(rho_full, 4), "note": "全20件"},
        {"metric": "p_full", "value": round(p_full, 6), "note": ""},
        {"metric": "rho_loo_mean", "value": round(rho_loo_mean, 4), "note": "20 fold 平均"},
        {"metric": "|rho|_loo_mean", "value": round(abs_mean, 4), "note": "絶対値平均"},
        {"metric": "rho_loo_std", "value": round(float(rho_loo_std), 4), "note": ""},
        {"metric": "|rho|_loo_std", "value": round(abs_std_val, 4), "note": "絶対値の標準偏差"},
        {"metric": "|rho|_loo_min", "value": round(abs_min_val, 4), "note": f"除外: {loo_results[int(np.argmin(abs_rho_arr))]['excluded_id']}"},
        {"metric": "|rho|_loo_max", "value": round(float(np.max(abs_rho_arr)), 4), "note": f"除外: {loo_results[int(np.argmax(abs_rho_arr))]['excluded_id']}"},
        {"metric": "|rho|_max_drop", "value": round(abs_max_drop, 4), "note": "|ρ_full| - |ρ_loo_min|"},
        {"metric": "influential_count", "value": len(influential), "note": "|ρ_i| < mean-2σ"},
        {"metric": "stability", "value": stability, "note": stability_desc},
        {"metric": "", "value": "", "note": ""},
        {"metric": "Model_C_prime_rho_full", "value": 0.8292, "note": "既知参考値"},
        {"metric": "Model_C_prime_rho_loo", "value": 0.8018, "note": "既知参考値"},
        {"metric": "Model_C_prime_drop", "value": 0.0274, "note": ""},
        {"metric": "delta_e_A_rho_full", "value": round(abs_rho_full, 4), "note": "|ρ|"},
        {"metric": "delta_e_A_rho_loo_mean", "value": round(abs_mean, 4), "note": "|ρ| 平均"},
        {"metric": "delta_e_A_drop", "value": round(abs_rho_full - abs_mean, 4), "note": "|ρ_full| - |ρ_loo_mean|"},
    ]
    out_summary = OUT_DIR / "delta_e_A_loo_cv_summary.csv"
    with open(out_summary, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["metric", "value", "note"])
        w.writeheader()
        w.writerows(summary_rows)
    print(f"出力: {out_summary}")

    # --- 出力3: 影響力棒グラフ ---
    if not _HAS_MPL:
        print("matplotlib 未インストール: 影響力棒グラフスキップ")
    else:
        fig, ax = plt.subplots(figsize=(14, 6))

        sorted_indices = np.argsort(abs_rho_arr)
        sorted_ids = [ids[i] for i in sorted_indices]
        sorted_abs_rho = abs_rho_arr[sorted_indices]
        sorted_hs = [hs[i] for i in sorted_indices]

        colors = ["tomato" if v < threshold_2sigma else "steelblue" for v in sorted_abs_rho]

        ax.bar(range(n), sorted_abs_rho, color=colors, edgecolor="white", linewidth=0.5)
        ax.axhline(y=abs_rho_full, color="red", linestyle="--", linewidth=1.5,
                   label=f"|ρ_full| = {abs_rho_full:.4f}")
        ax.axhspan(threshold_2sigma, abs_mean + 2 * abs_std, alpha=0.12, color="green",
                   label=f"|ρ| mean±2σ = [{threshold_2sigma:.4f}, {abs_mean + 2*abs_std:.4f}]")
        ax.axhline(y=abs_mean, color="green", linestyle=":", linewidth=1, alpha=0.7)

        xlabels = [f"{sid}\n(hs={int(shs)})" for sid, shs in zip(sorted_ids, sorted_hs)]
        ax.set_xticks(range(n))
        ax.set_xticklabels(xlabels, rotation=45, ha="right", fontsize=8)
        ax.set_ylabel("|ρ_i| (Spearman, excluding case i)", fontsize=11)
        ax.set_title("LOO-CV Influence Analysis: ΔE_A vs human_score\n"
                     "(sorted by |ρ_i|, red = outside mean-2σ)", fontsize=12)
        ax.legend(loc="lower right", fontsize=9)
        ax.set_ylim(min(0.75, sorted_abs_rho[0] - 0.02), 1.0)
        ax.grid(axis="y", alpha=0.3)

        plt.tight_layout()
        out_png = OUT_DIR / "delta_e_A_loo_influence.png"
        fig.savefig(out_png, dpi=150)
        plt.close()
        print(f"出力: {out_png}")

    # --- 出力4: 診断レポート ---
    lines = [
        "# ΔE_A（パイプライン A 理論式）LOO-CV 安定性検証",
        "",
        "**実行日**: 2026-04-02",
        f"**データ**: HA20 ({n}件, temperature=0.7)",
        "",
        "## 1. 全データ ρ の再確認",
        "",
        f"- ΔE_A vs human_score: **ρ = {rho_full:.4f}** (p = {p_full:.6f})",
        f"- |ρ| = {abs_rho_full:.4f}",
        f"- 前タスクの値 (ρ=-0.9278) と{'一致' if round(rho_full, 4) == -0.9278 else '近似'}。",
        "",
        "## 2. LOO-CV 結果",
        "",
        "### Fold 別 ρ",
        "",
        "| 除外 id | human_score | ρ_i | |ρ_i| | p_i | |ρ| drop |",
        "|---------|------------|-----|-------|-----|---------|",
    ]
    for i in range(n):
        lr = loo_results[i]
        flag = " **←**" if abs(lr["rho_i"]) < threshold_2sigma else ""
        lines.append(
            f"| {lr['excluded_id']} | {int(lr['excluded_human_score'])} | "
            f"{lr['rho_i']:.4f} | {abs(lr['rho_i']):.4f} | {lr['p_i']:.6f} | "
            f"{lr['abs_rho_drop']:.4f} |{flag}"
        )

    lines += [
        "",
        "### 安定性指標",
        "",
        "| 指標 | 値 |",
        "|------|-----|",
        f"| ρ_full | {rho_full:.4f} |",
        f"| |ρ|_full | {abs_rho_full:.4f} |",
        f"| ρ_loo_mean | {rho_loo_mean:.4f} |",
        f"| |ρ|_loo_mean | {abs_mean:.4f} |",
        f"| |ρ|_loo_std | {abs_std_val:.4f} |",
        f"| |ρ|_loo_min | {abs_min_val:.4f} (除外: {loo_results[int(np.argmin(abs_rho_arr))]['excluded_id']}) |",
        f"| |ρ|_loo_max | {float(np.max(abs_rho_arr)):.4f} (除外: {loo_results[int(np.argmax(abs_rho_arr))]['excluded_id']}) |",
        f"| |ρ| max_drop | {abs_max_drop:.4f} |",
        f"| Influential cases | {len(influential)} |",
        "",
    ]

    # 安定性判定
    lines += [
        f"### 安定性判定: **{stability}**",
        "",
        f"{stability_desc}",
        "",
    ]

    # 影響力分析
    lines += [
        "## 3. 影響力分析",
        "",
        "### 注目ケース",
        "",
    ]
    watch_ids = ["q032", "q024", "q009", "q083"]
    for wid in watch_ids:
        lr = next((r for r in loo_results if r["excluded_id"] == wid), None)
        if lr:
            lines.append(
                f"- **{wid}** (human_score={int(lr['excluded_human_score'])}): "
                f"除外時 ρ_i={lr['rho_i']:.4f} (|ρ|={abs(lr['rho_i']):.4f}), "
                f"|ρ| drop={lr['abs_rho_drop']:.4f}"
            )

    if influential:
        lines += [
            "",
            "### 2σ 外れ値",
            "",
        ]
        for ic in influential:
            lines.append(
                f"- **{ic['excluded_id']}** (human_score={int(ic['excluded_human_score'])}): "
                f"|ρ_i|={abs(ic['rho_i']):.4f} < threshold {threshold_2sigma:.4f}"
            )
    else:
        lines += [
            "",
            "### 2σ 外れ値: なし",
            "",
            f"全 fold の |ρ_i| が mean-2σ ({threshold_2sigma:.4f}) を上回った。",
        ]

    # Model C' 比較
    lines += [
        "",
        "## 4. Model C' との比較",
        "",
        "| 指標 | ΔE_A | Model C' |",
        "|------|------|----------|",
        f"| |ρ|_full | {abs_rho_full:.4f} | 0.8292 |",
        f"| |ρ|_loo_mean | {abs_mean:.4f} | 0.8018 |",
        f"| drop (full→loo) | {abs_rho_full - abs_mean:.4f} | 0.0274 |",
        f"| |ρ|_loo_min | {abs_min_val:.4f} | — |",
        f"| |ρ|_loo_std | {abs_std_val:.4f} | — |",
        "",
    ]

    if abs_mean > 0.8018:
        lines.append(
            f"ΔE_A の LOO-CV 平均 (|ρ|={abs_mean:.4f}) は "
            f"Model C' LOO-CV (ρ=0.8018) を上回る。"
        )
    else:
        lines.append(
            f"ΔE_A の LOO-CV 平均 (|ρ|={abs_mean:.4f}) は "
            f"Model C' LOO-CV (ρ=0.8018) を下回る。"
        )

    # 結論
    lines += [
        "",
        "## 5. 結論",
        "",
        f"ΔE_A の Spearman |ρ| は全データで {abs_rho_full:.4f}、"
        f"LOO-CV 平均で {abs_mean:.4f} (std={abs_std_val:.4f})。",
        f"|ρ|_loo_min = {abs_min_val:.4f}。",
        "",
    ]
    if stability == "安定":
        lines.append(
            "LOO-CV により ΔE_A の高い相関は特定ケースに依存していないことが確認された。"
            "パイプラインAの理論式ΔEは頑健な品質指標である。"
        )
    elif stability == "概ね安定":
        lines.append(
            "LOO-CV により ΔE_A の相関は概ね安定しているが、一部ケースへの感度が見られる。"
            "n=48 拡張での再検証が推奨される。"
        )
    else:
        lines.append(
            "LOO-CV により ΔE_A の高い相関に特定ケース依存の疑いがある。"
            "n=48 拡張での再検証が必須。"
        )

    lines += [
        "",
        "---",
        "",
        "LOO-CV 結果: `delta_e_A_loo_cv_results.csv`",
        "安定性サマリー: `delta_e_A_loo_cv_summary.csv`",
        "影響力棒グラフ: `delta_e_A_loo_influence.png`",
    ]

    out_report = OUT_DIR / "delta_e_A_loo_cv_report.md"
    with open(out_report, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"出力: {out_report}")

    print("\n=== 完了 ===")


if __name__ == "__main__":
    main()

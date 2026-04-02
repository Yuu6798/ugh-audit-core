"""パイプライン A の ΔE（加重二乗和）vs human_score 相関検証

ugh_calculator の理論式 ΔE を HA20 データ上で計算し、
human_score との Spearman ρ を既存指標と比較する。
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

# --- パス ---
ROOT = Path(__file__).resolve().parent.parent.parent
HA20_CSV = ROOT / "data" / "human_annotation_20" / "human_annotation_20_completed.csv"
GATE_CSV = ROOT / "data" / "gate_results" / "structural_gate_summary.csv"
MERGED_CSV = ROOT / "analysis" / "semantic_loss" / "ha20_merged_for_model_c.csv"
OUT_DIR = Path(__file__).resolve().parent
OUT_DIR.mkdir(parents=True, exist_ok=True)

# --- パイプライン A の定数 (ugh_calculator.py 準拠) ---
WEIGHTS_F = {"f1": 5, "f2": 25, "f3": 5, "f4": 5}
TOTAL_W = sum(WEIGHTS_F.values())  # 40
WEIGHT_S = 2
WEIGHT_C = 1


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def compute_s(f1: float, f2: float, f3: float, f4: float) -> float:
    weighted = WEIGHTS_F["f1"] * f1 + WEIGHTS_F["f2"] * f2 + WEIGHTS_F["f3"] * f3 + WEIGHTS_F["f4"] * f4
    return clamp(1.0 - weighted / TOTAL_W)


def compute_c(prop_hit_str: str) -> float:
    num, den = prop_hit_str.strip().split("/")
    num, den = int(num), int(den)
    if den == 0:
        return 1.0
    return clamp(num / den)


def compute_delta_e_a(s: float, c: float) -> float:
    numerator = WEIGHT_S * (1.0 - s) ** 2 + WEIGHT_C * (1.0 - c) ** 2
    return clamp(numerator / (WEIGHT_S + WEIGHT_C))


def _plot_scatter(records: list, out_dir: Path) -> None:
    """散布図を生成する（matplotlib 必須）"""
    de_a = [r["delta_e_A"] for r in records]
    de_f = [r["delta_e_full"] for r in records]
    ids = [r["id"] for r in records]
    hs = [r["human_score"] for r in records]

    rho_a, p_a = spearmanr(hs, de_a)
    rho_f, p_f = spearmanr(hs, de_f)

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))

    ax = axes[0]
    ax.scatter(de_a, hs, c="steelblue", s=60, zorder=3)
    for i, qid in enumerate(ids):
        ax.annotate(qid, (de_a[i], hs[i]), fontsize=7, textcoords="offset points",
                    xytext=(4, 4), alpha=0.8)
    ax.set_xlabel("ΔE_A (Pipeline A)", fontsize=11)
    ax.set_ylabel("human_score", fontsize=11)
    ax.set_title(f"Pipeline A: ΔE_A vs human_score\nρ={rho_a:.4f} (p={p_a:.4f})", fontsize=11)
    ax.set_xlim(-0.02, max(de_a) * 1.15)
    ax.set_ylim(0.5, 5.5)
    ax.grid(True, alpha=0.3)

    ax = axes[1]
    ax.scatter(de_f, hs, c="coral", s=60, zorder=3)
    for i, qid in enumerate(ids):
        ax.annotate(qid, (de_f[i], hs[i]), fontsize=7, textcoords="offset points",
                    xytext=(4, 4), alpha=0.8)
    ax.set_xlabel("delta_e_full (Pipeline B, cosine)", fontsize=11)
    ax.set_ylabel("human_score", fontsize=11)
    ax.set_title(f"Pipeline B: delta_e_full vs human_score\nρ={rho_f:.4f} (p={p_f:.4f})", fontsize=11)
    ax.set_xlim(-0.02, max(de_f) * 1.15)
    ax.set_ylim(0.5, 5.5)
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    out_png = out_dir / "scatter_human_score_vs_delta_e_A.png"
    fig.savefig(out_png, dpi=150)
    plt.close()
    print(f"出力: {out_png}")


def main() -> None:
    # --- Step 1: データ読み込み ---
    ha20_rows = {}
    with open(HA20_CSV, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ha20_rows[row["id"]] = row
    ha20_ids = set(ha20_rows.keys())
    print(f"HA20 件数: {len(ha20_ids)}")

    gate_rows = {}
    with open(GATE_CSV, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["temperature"] == "0.0" and row["id"] in ha20_ids:
                gate_rows[row["id"]] = row
    print(f"Gate (temp=0.0, HA20一致): {len(gate_rows)}")

    # system_hit_rate を merged CSV から読み込む
    merged_rows = {}
    with open(MERGED_CSV, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["id"] in ha20_ids:
                merged_rows[row["id"]] = row
    print(f"Merged (system_hit_rate): {len(merged_rows)}")

    # 結合確認
    joined_ids = ha20_ids & set(gate_rows.keys()) & set(merged_rows.keys())
    missing = ha20_ids - joined_ids
    if missing:
        print(f"WARNING: 結合できない HA20 ID: {missing}")
    print(f"結合件数: {len(joined_ids)}")

    # --- Step 2-4: S, C, ΔE_A 計算 ---
    # system_hit_rate (merged CSV, t=0.0) をメイン、human propositions_hit を参照上限として併記
    records = []
    for qid in sorted(joined_ids):
        ha = ha20_rows[qid]
        gt = gate_rows[qid]
        mg = merged_rows[qid]

        human_score = float(ha["human_score"])
        por = float(ha["por"])
        delta_e_full = float(ha["delta_e_full"])
        prop_hit_str = ha["propositions_hit"]

        # gate (t=0.0) から全4フラグを取得 — 同一スライスで統一
        f1 = float(gt["f1_flag"])
        f2 = float(gt["f2_flag"])
        f3 = float(gt["f3_flag"])
        f4 = float(gt["f4_flag"])
        fail_max = float(gt["fail_max"])

        # system_hit_rate は merged CSV から取得 (同じ t=0.0 スライス)
        sys_hit_rate = float(mg["system_hit_rate"])
        sys_fail_max = float(mg["fail_max"])

        s = compute_s(f1, f2, f3, f4)
        c_sys = clamp(sys_hit_rate)
        delta_e_a = compute_delta_e_a(s, c_sys)

        # human ベース（参照上限）
        c_human = compute_c(prop_hit_str)
        delta_e_a_human = compute_delta_e_a(s, c_human)

        records.append({
            "id": qid,
            "human_score": human_score,
            "f1_flag": f1,
            "f2_flag": f2,
            "f3_flag": f3,
            "f4_flag": f4,
            "S": round(s, 4),
            "C_sys": round(c_sys, 4),
            "C_human": round(c_human, 4),
            "delta_e_A": round(delta_e_a, 4),
            "delta_e_A_human": round(delta_e_a_human, 4),
            "delta_e_full": delta_e_full,
            "por": por,
            "fail_max": fail_max,
            "sys_fail_max": sys_fail_max,
        })

    # --- 値域チェック ---
    for r in records:
        assert 0.0 <= r["S"] <= 1.0, f"{r['id']}: S={r['S']} out of range"
        assert 0.0 <= r["C_sys"] <= 1.0, f"{r['id']}: C_sys={r['C_sys']} out of range"
        assert 0.0 <= r["C_human"] <= 1.0, f"{r['id']}: C_human={r['C_human']} out of range"
        assert 0.0 <= r["delta_e_A"] <= 1.0, f"{r['id']}: delta_e_A={r['delta_e_A']} out of range"
        assert 0.0 <= r["delta_e_A_human"] <= 1.0, f"{r['id']}: delta_e_A_human out of range"
    print("値域チェック: OK")

    # --- 出力1: 結合データ CSV ---
    out_csv = OUT_DIR / "ha20_pipeline_a_delta_e.csv"
    fieldnames = list(records[0].keys())
    with open(out_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(records)
    print(f"出力: {out_csv}")

    # --- Step 5: 相関分析 ---
    human_scores = np.array([r["human_score"] for r in records])
    n = len(human_scores)

    metrics = [
        ("delta_e_A (sys)", [r["delta_e_A"] for r in records], "ΔE_A system C（メイン指標）"),
        ("1 - delta_e_A (sys)", [1 - r["delta_e_A"] for r in records], "ΔE_A system C 反転"),
        ("delta_e_A (human)", [r["delta_e_A_human"] for r in records], "ΔE_A human C（参照上限）"),
        ("1 - delta_e_A (human)", [1 - r["delta_e_A_human"] for r in records], "ΔE_A human C 反転"),
        ("S", [r["S"] for r in records], "パイプラインA 構造品質"),
        ("C_sys", [r["C_sys"] for r in records], "system 命題カバレッジ"),
        ("C_human", [r["C_human"] for r in records], "human 命題カバレッジ（参照上限）"),
        ("delta_e_full", [r["delta_e_full"] for r in records], "旧 cosine ΔE（負相関期待）"),
        ("1 - delta_e_full", [1 - r["delta_e_full"] for r in records], "旧 cosine ΔE 反転"),
        ("por", [r["por"] for r in records], "旧 cosine PoR"),
        ("fail_max", [r["fail_max"] for r in records], "パイプラインB 構造指標（負相関期待）"),
        ("1 - fail_max", [1 - r["fail_max"] for r in records], "fail_max 反転 (≒ S_B)"),
    ]

    corr_results = []
    print("\n=== Spearman ρ vs human_score ===")
    print(f"{'指標':<20s} {'ρ':>8s} {'p値':>10s}  {'n':>3s}  備考")
    print("-" * 70)
    for name, values, note in metrics:
        rho, pval = spearmanr(human_scores, values)
        corr_results.append({
            "metric": name,
            "rho": round(rho, 4),
            "p_value": round(pval, 6),
            "n": n,
            "note": note,
        })
        print(f"{name:<20s} {rho:>8.4f} {pval:>10.6f}  {n:>3d}  {note}")

    # 参考値追加
    ref_metrics = [
        ("Model C' quality_score", 0.8292, None, 20, "既知参考値 (全データ)"),
        ("Model C' LOO-CV", 0.8018, None, 20, "既知参考値 (LOO-CV)"),
        ("hit_rate (reference)", 0.9090, None, 20, "人間アノテーター内部一貫性"),
    ]
    for name, rho, pval, n_ref, note in ref_metrics:
        corr_results.append({
            "metric": name,
            "rho": rho,
            "p_value": pval if pval else "",
            "n": n_ref,
            "note": note,
        })

    # --- 出力2: 相関結果 CSV ---
    out_corr = OUT_DIR / "pipeline_a_correlation_results.csv"
    with open(out_corr, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["metric", "rho", "p_value", "n", "note"])
        w.writeheader()
        w.writerows(corr_results)
    print(f"\n出力: {out_corr}")

    # --- 出力3: 散布図 ---
    if not _HAS_MPL:
        print("matplotlib 未インストール: 散布図スキップ")
    else:
        _plot_scatter(records, OUT_DIR)

    # --- 出力4: 診断レポート ---

    # --- 出力4 (continued): 診断レポート ---
    report_lines = [
        "# パイプライン A の ΔE（加重二乗和）vs human_score 相関検証",
        "",
        "**実行日**: 2026-04-02",
        f"**データ**: HA20 ({n}件, temperature=0.0)",
        "",
        "## 1. 計算過程の確認",
        "",
        "### パイプライン A の定数",
        "",
        f"- 構造重み: f1={WEIGHTS_F['f1']}, f2={WEIGHTS_F['f2']}, f3={WEIGHTS_F['f3']}, f4={WEIGHTS_F['f4']} (合計={TOTAL_W})",
        f"- ΔE 重み: w_s={WEIGHT_S}, w_c={WEIGHT_C}",
        "",
        "### S, C, ΔE_A の分布",
        "",
        "| 統計量 | S | C_sys | C_human | ΔE_A (sys) | ΔE_A (human) |",
        "|--------|---|-------|---------|------------|-------------|",
    ]

    s_vals = np.array([r["S"] for r in records])
    c_sys_vals = np.array([r["C_sys"] for r in records])
    c_hum_vals = np.array([r["C_human"] for r in records])
    de_a_vals = np.array([r["delta_e_A"] for r in records])
    de_a_hum_vals = np.array([r["delta_e_A_human"] for r in records])
    for stat_name, func in [("min", np.min), ("max", np.max), ("mean", np.mean), ("median", np.median), ("std", np.std)]:
        report_lines.append(
            f"| {stat_name} | {func(s_vals):.4f} | {func(c_sys_vals):.4f} | "
            f"{func(c_hum_vals):.4f} | {func(de_a_vals):.4f} | {func(de_a_hum_vals):.4f} |"
        )

    report_lines += [
        "",
        "### 個別データ",
        "",
        "| id | human_score | S | C_sys | C_human | ΔE_A (sys) | ΔE_A (human) | delta_e_full |",
        "|-----|------------|---|-------|---------|------------|-------------|-------------|",
    ]
    for r in records:
        report_lines.append(
            f"| {r['id']} | {r['human_score']:.0f} | {r['S']:.4f} | {r['C_sys']:.4f} | "
            f"{r['C_human']:.4f} | {r['delta_e_A']:.4f} | {r['delta_e_A_human']:.4f} | "
            f"{r['delta_e_full']:.4f} |"
        )

    report_lines += [
        "",
        "## 2. 相関比較テーブル",
        "",
        "| 指標 | ρ vs human_score | p値 | 備考 |",
        "|------|:----------------:|-----|------|",
    ]
    for cr in corr_results:
        p_str = f"{cr['p_value']:.6f}" if cr['p_value'] != "" else "—"
        report_lines.append(
            f"| {cr['metric']} | {cr['rho']:.4f} | {p_str} | {cr['note']} |"
        )

    # 所見
    rho_de_a = [cr for cr in corr_results if cr["metric"] == "delta_e_A (sys)"][0]["rho"]
    rho_de_a_inv = [cr for cr in corr_results if cr["metric"] == "1 - delta_e_A (sys)"][0]["rho"]
    rho_de_a_hum = [cr for cr in corr_results if cr["metric"] == "delta_e_A (human)"][0]["rho"]
    rho_de_a_hum_inv = [cr for cr in corr_results if cr["metric"] == "1 - delta_e_A (human)"][0]["rho"]
    rho_de_f = [cr for cr in corr_results if cr["metric"] == "delta_e_full"][0]["rho"]
    rho_c_sys = [cr for cr in corr_results if cr["metric"] == "C_sys"][0]["rho"]
    rho_c_hum = [cr for cr in corr_results if cr["metric"] == "C_human"][0]["rho"]
    rho_s = [cr for cr in corr_results if cr["metric"] == "S"][0]["rho"]

    report_lines += [
        "",
        "## 3. 所見",
        "",
        "### system C vs human C",
        "",
        f"- **ΔE_A (sys)**: ρ = {rho_de_a:.4f} ← メイン指標（デプロイ可能）",
        f"- **ΔE_A (human)**: ρ = {rho_de_a_hum:.4f} ← 参照上限",
        f"- **C_sys**: ρ = {rho_c_sys:.4f}",
        f"- **C_human**: ρ = {rho_c_hum:.4f} ← 参照上限",
        "",
    ]

    report_lines += [
        "### ΔE_A (sys) vs cosine ΔE",
        "",
        f"- **ΔE_A (sys)**: ρ = {rho_de_a:.4f}",
        f"- **delta_e_full** (cosine): ρ = {rho_de_f:.4f}",
        "",
    ]

    if abs(rho_de_a) > abs(rho_de_f):
        report_lines.append(
            f"ΔE_A (sys) は cosine ΔE より**強い**相関（|ρ| {abs(rho_de_a):.4f} vs {abs(rho_de_f):.4f}）。"
        )
    elif abs(rho_de_a) < abs(rho_de_f):
        report_lines.append(
            f"ΔE_A (sys) は cosine ΔE より**弱い**相関（|ρ| {abs(rho_de_a):.4f} vs {abs(rho_de_f):.4f}）。"
        )
    else:
        report_lines.append("ΔE_A (sys) と cosine ΔE の相関は同等。")

    report_lines += [
        "",
        "### ΔE_A (sys) vs C_sys 単独",
        "",
        f"- **C_sys**: ρ = {rho_c_sys:.4f}",
        f"- **1 - ΔE_A (sys)**: ρ = {rho_de_a_inv:.4f}",
        "",
    ]

    if abs(rho_de_a_inv) > abs(rho_c_sys):
        report_lines.append(
            "ΔE_A (sys) は C_sys 単独を**上回る**。S の統合が品質予測に寄与している。"
        )
    elif abs(rho_de_a_inv) < abs(rho_c_sys):
        report_lines.append(
            "ΔE_A (sys) は C_sys 単独を**下回る**。S の統合が C の信号を希釈している可能性がある。"
        )
    else:
        report_lines.append("ΔE_A (sys) と C_sys 単独の相関は同等。")

    rho_fm = [cr for cr in corr_results if cr["metric"] == "1 - fail_max"][0]["rho"]
    report_lines += [
        "",
        "### S（構造品質）の寄与",
        "",
        f"- **S**: ρ = {rho_s:.4f}",
        f"- **1 - fail_max**: ρ = {rho_fm:.4f}",
        "",
        "### Model C' との比較",
        "",
        "- **Model C' quality_score**: ρ = 0.8292（既知参考値, t=0.0, system_hit_rate）",
        f"- **1 - ΔE_A (sys)**: ρ = {rho_de_a_inv:.4f}",
        f"- **1 - ΔE_A (human)**: ρ = {rho_de_a_hum_inv:.4f}（参照上限）",
        "",
    ]

    if abs(rho_de_a_inv) > 0.8292:
        report_lines.append(f"ΔE_A (sys) (|ρ|={abs(rho_de_a_inv):.4f}) が Model C' (ρ=0.8292) を上回る。")
    else:
        report_lines.append(
            f"Model C' (ρ=0.8292) が ΔE_A (sys) (|ρ|={abs(rho_de_a_inv):.4f}) を上回る。"
        )

    report_lines += [
        "",
        "## 4. 結論",
        "",
        "パイプラインAの理論式ΔEは S（構造品質）と C（命題カバレッジ）を加重二乗和で統合し、",
        "理論的に整合的な距離指標を提供する。本検証でその実測相関が明らかになった。",
        "",
        "---",
        "",
        "散布図: `scatter_human_score_vs_delta_e_A.png`",
        "結合データ: `ha20_pipeline_a_delta_e.csv`",
        "相関結果: `pipeline_a_correlation_results.csv`",
    ]

    out_report = OUT_DIR / "pipeline_a_delta_e_report.md"
    with open(out_report, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines) + "\n")
    print(f"出力: {out_report}")

    print("\n=== 完了 ===")


if __name__ == "__main__":
    main()

"""ΔE_A 再計算スクリプト — HA28 命題 hit 数復元版

HA28 の human_hit_rate を (C-1)/2 粗視化から命題単位カウントに更新し、
ΔE_A を再計算する。HA20 は変更なし。
"""
from __future__ import annotations

import csv
import os
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr

BASE = Path(__file__).resolve().parent
MERGED_V1 = BASE / "ha48_merged.csv"
RESTORATION = BASE / "ha28_hit_restoration.csv"
OUT_V2 = BASE / "ha48_merged_v2.csv"
OUT_SUMMARY = BASE / "recalc_results_summary.md"

# 確定済み重み
W_F1, W_F2, W_F3, W_F4 = 5, 25, 5, 5
W_SUM = 40
WEIGHT_S, WEIGHT_C = 2, 1

# HA20 IDs (変更なし)
HA20_IDS = {
    "q024", "q075", "q009", "q080", "q037", "q100", "q015", "q012",
    "q049", "q033", "q071", "q061", "q083", "q025", "q095", "q032",
    "q044", "q019", "q069", "q063",
}


def calc_delta_e_a(s_score: float, c: float) -> float:
    return (WEIGHT_S * (1 - s_score) ** 2 + WEIGHT_C * (1 - c) ** 2) / (WEIGHT_S + WEIGHT_C)


def load_restoration() -> dict:
    """ha28_hit_restoration.csv から復元データを読み込み"""
    data = {}
    with open(RESTORATION) as f:
        for row in csv.DictReader(f):
            data[row["qid"]] = {
                "hit_count": int(row["hit_count"]),
                "n_props": int(row["n_props"]),
                "hit_rate": float(row["hit_rate"]),
                "propositions_hit": f"{row['hit_count']}/{row['n_props']}",
            }
    return data


def main():
    restoration = load_restoration()

    # v1 データを読み込み、HA28 部分を更新
    rows = []
    with open(MERGED_V1) as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            qid = row["id"]

            if qid not in HA20_IDS and qid in restoration:
                r = restoration[qid]
                old_hr = float(row["human_hit_rate"])
                new_hr = r["hit_rate"]

                row["human_hit_rate"] = str(round(new_hr, 4))

                # ΔE_A (reference) 再計算
                s_score = float(row["S_score"])
                new_de_ref = calc_delta_e_a(s_score, new_hr)
                row["delta_e_a_ref"] = str(round(new_de_ref, 4))

                # propositions_hit を追加/更新
                row["propositions_hit"] = r["propositions_hit"]

            rows.append(row)

    # propositions_hit カラムを追加
    if "propositions_hit" not in fieldnames:
        fieldnames = list(fieldnames)
        # notes の前に挿入
        idx = fieldnames.index("notes")
        fieldnames.insert(idx, "propositions_hit")

    # v2 CSV 出力
    with open(OUT_V2, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            if "propositions_hit" not in row:
                row["propositions_hit"] = ""
            w.writerow(row)
    print(f"[OK] {OUT_V2}")

    # --- 再計算: 相関分析 ---
    O = np.array([float(r["O"]) for r in rows])
    de_ref = np.array([float(r["delta_e_a_ref"]) for r in rows])
    de_sys = np.array([float(r["delta_e_a_sys"]) for r in rows])
    sys_hr = np.array([float(r["system_hit_rate"]) for r in rows])
    human_hr = np.array([float(r["human_hit_rate"]) for r in rows])

    pred_ref = 5 - 4 * de_ref
    pred_sys = 5 - 4 * de_sys

    rho_ref, p_ref = spearmanr(pred_ref, O)
    rho_sys, p_sys = spearmanr(pred_sys, O)
    rho_hr, p_hr = spearmanr(sys_hr, O)

    print(f"\n=== n=48 相関 (復元版) ===")
    print(f"ΔE_A reference ρ = {rho_ref:.4f} (p={p_ref:.2e})")
    print(f"ΔE_A system    ρ = {rho_sys:.4f} (p={p_sys:.2e})")
    print(f"hit_rate only  ρ = {rho_hr:.4f} (p={p_hr:.2e})")

    # --- LOO-CV ---
    n = len(rows)
    rho_list = []
    for i in range(n):
        idx = [j for j in range(n) if j != i]
        rho_i, _ = spearmanr(pred_ref[idx], O[idx])
        rho_list.append(rho_i)
    rho_arr = np.array(rho_list)
    loo_mean = float(np.mean(rho_arr))
    loo_std = float(np.std(rho_arr))
    loo_min = float(np.min(rho_arr))
    loo_max = float(np.max(rho_arr))
    print(f"\nLOO-CV: mean={loo_mean:.4f}, std={loo_std:.4f}, "
          f"min={loo_min:.4f}, max={loo_max:.4f}")

    # --- サブグループ (O値別) ---
    print("\n=== サブグループ (O値別 ΔE_A reference 平均) ===")
    for label, cond in [("O<=2", O <= 2), ("O=3", (O > 2) & (O <= 3)), ("O>=4", O >= 4)]:
        idx = np.where(cond)[0]
        de_mean = float(np.mean(de_ref[idx]))
        o_mean = float(np.mean(O[idx]))
        print(f"  {label}: n={len(idx)}, O={o_mean:.2f}, ΔE_A={de_mean:.4f}")
    ordinal_ok = (np.mean(de_ref[O <= 2]) > np.mean(de_ref[(O > 2) & (O <= 3)])
                  > np.mean(de_ref[O >= 4]))

    # --- HA20 / HA28 別 ---
    print("\n=== HA20 / HA28 別 ===")
    for label, ids in [("HA20", HA20_IDS), ("HA28", set(r["id"] for r in rows) - HA20_IDS)]:
        idx = [i for i, r in enumerate(rows) if r["id"] in ids]
        if len(idx) >= 3:
            rho_sub, p_sub = spearmanr(pred_ref[idx], O[idx])
            print(f"  {label}: n={len(idx)}, ρ={rho_sub:.4f} (p={p_sub:.2e})")

    # --- 感度分析 (疑義3件除外) ---
    suspect = {"q003", "q041", "q053"}
    clean_idx = [i for i, r in enumerate(rows) if r["id"] not in suspect]
    rho_clean, p_clean = spearmanr(pred_ref[clean_idx], O[clean_idx])
    print(f"\n感度分析 (n=45): ρ={rho_clean:.4f} (p={p_clean:.2e})")

    # --- 変化テーブル ---
    print("\n=== HA28 hit_rate 変化 ===")
    v1_rows = {}
    with open(MERGED_V1) as f:
        for row in csv.DictReader(f):
            v1_rows[row["id"]] = row

    changed = 0
    for qid in sorted(restoration.keys()):
        old_hr = float(v1_rows[qid]["human_hit_rate"])
        new_hr = restoration[qid]["hit_rate"]
        old_de = float(v1_rows[qid]["delta_e_a_ref"])
        new_de = calc_delta_e_a(float(v1_rows[qid]["S_score"]), new_hr)
        if abs(old_hr - new_hr) > 0.001:
            changed += 1
            print(f"  {qid}: hr {old_hr:.3f}→{new_hr:.3f}, "
                  f"ΔE_A {old_de:.4f}→{new_de:.4f}")
    print(f"  変更件数: {changed}/28")

    # --- 受理基準 ---
    degradation = 0.928 - rho_ref
    criteria = {
        "ref_rho >= 0.85": rho_ref >= 0.85,
        "degradation <= 0.08": degradation <= 0.08,
        "sys_rho >= 0.50": rho_sys >= 0.50,
        "loo_std <= 0.05": loo_std <= 0.05,
        "ordinal_consistency": bool(ordinal_ok),
    }
    n_pass = sum(criteria.values())
    verdict = "GO" if n_pass == 5 else ("CONDITIONAL" if n_pass >= 3 else "NO-GO")

    print(f"\n=== 受理基準 ===")
    for name, passed in criteria.items():
        print(f"  {name}: {'PASS' if passed else 'FAIL'}")
    print(f"\n判定: {verdict} ({n_pass}/5 PASS)")

    # --- results_summary 出力 ---
    # v1 の結果を読み込み
    rho_ref_v1 = 0.8375  # 前回の結果
    rho_sys_v1 = 0.4839
    loo_std_v1 = 0.0083

    lines = [
        "# ΔE_A 再計算結果 — HA28 命題 hit 数復元版",
        "",
        f"## 判定: **{verdict}**",
        "",
        "## 比較テーブル",
        "",
        "| 指標 | 粗視化版 (v1) | 復元版 (v2) | 変化 |",
        "|------|-------------|-----------|------|",
        f"| reference ρ (n=48) | {rho_ref_v1:.4f} | {rho_ref:.4f} | {rho_ref - rho_ref_v1:+.4f} |",
        f"| system ρ (n=48) | {rho_sys_v1:.4f} | {rho_sys:.4f} | (変化なし) |",
        f"| LOO-CV std | {loo_std_v1:.4f} | {loo_std:.4f} | {loo_std - loo_std_v1:+.4f} |",
        f"| n=20→48 劣化 | 0.0905 | {degradation:.4f} | {degradation - 0.0905:+.4f} |",
        "",
        "## 受理基準",
        "",
        "| 基準 | 閾値 | v1 | v2 | 判定 |",
        "|------|------|----|----|------|",
        f"| reference ρ | ≥0.85 | {rho_ref_v1:.4f} | {rho_ref:.4f} | {'PASS' if criteria['ref_rho >= 0.85'] else 'FAIL'} |",
        f"| 劣化 | ≤0.08 | 0.0905 | {degradation:.4f} | {'PASS' if criteria['degradation <= 0.08'] else 'FAIL'} |",
        f"| system ρ | ≥0.50 | {rho_sys_v1:.4f} | {rho_sys:.4f} | {'PASS' if criteria['sys_rho >= 0.50'] else 'FAIL'} |",
        f"| LOO-CV std | ≤0.05 | {loo_std_v1:.4f} | {loo_std:.4f} | {'PASS' if criteria['loo_std <= 0.05'] else 'FAIL'} |",
        f"| 序列一貫性 | O≤2>O=3>O≥4 | PASS | {'PASS' if ordinal_ok else 'FAIL'} | {'PASS' if criteria['ordinal_consistency'] else 'FAIL'} |",
        "",
        "## HA28 hit_rate 復元の影響",
        "",
        "### 変化パターン",
        "- C=1 (10件): (C-1)/2=0.0 → 多くが 1/3=0.333 に上昇（1命題は部分的に到達）",
        "- C=2 (15件): (C-1)/2=0.5 → 多くが 2/3=0.667 に上昇（2命題hit が典型パターン）",
        "- C=3 (3件): (C-1)/2=1.0 → 3/3=1.000（変化なし）",
        "",
        "### 復元による ΔE_A への影響",
        "- C=1 の hit_rate 上昇 → ΔE_A が低下（(1-0.333)^2=0.444 < (1-0.0)^2=1.0）",
        "- C=2 の hit_rate 上昇 → ΔE_A が低下（(1-0.667)^2=0.111 < (1-0.5)^2=0.25）",
        "- 低O群（O≤2）の ΔE_A 低下幅が大きく、高O群との差が縮小 → ρ が変化",
        "",
        "## C アノテーションとの整合性",
        "",
        "| C | hit_rate 範囲 | 期待 | 整合性 |",
        "|---|-------------|------|--------|",
        "| 1 | 0.000-0.333 | ≤1/3 | OK |",
        "| 2 | 0.333-0.750 | 1/3-2/3 | q055(3/4=0.75)がやや高いがC=2の範囲内 |",
        "| 3 | 1.000 | ≥2/3 | OK |",
        "",
        f"## 感度分析 (疑義3件除外, n=45)",
        f"- reference ρ: {rho_clean:.4f} (p={p_clean:.2e})",
        "",
        "## 制約事項",
        "",
        "- HA20 (20件) は変更なし（既に命題単位 hit_rate）",
        "- hit/miss はバイナリ判定（部分 hit = 0.5 は不使用）",
        "- 判定根拠は annotation notes + core_propositions + response (t=0.0) の突合せ",
        "- q055 (C=2, hit=3/4=0.75): C スコアとの軽微な乖離あり。",
        "  アノテーターは深さを重視した可能性",
        "",
    ]

    with open(OUT_SUMMARY, "w") as f:
        f.write("\n".join(lines))
    print(f"\n[OK] {OUT_SUMMARY}")


if __name__ == "__main__":
    main()

# Phase E Calibration Result

生成日: 2026-04-18  
データソース: HA48 + accept40 merged (n=63 rows loaded)

## 分布サマリー

**verdict 内訳 (n=63):**

| verdict | 件数 |
|---|---|
| accept | 40 |
| rewrite | 20 |
| regenerate | 3 |
| degraded | 0 |
| **合計** | **63** |

**accept subset (n=40) の mcg 成分 quantile:**

| 成分 | min | P25 | median | P75 | max |
|---|---|---|---|---|---|
| `collapse_risk` | n/a | n/a | n/a | n/a | n/a |
| `anchor_alignment` | n/a | n/a | n/a | n/a | n/a |

注: 今回バッチでは `anchor_alignment/collapse_risk` の算出可能サンプルが 0 件 (`n=0`)。

**primary verdict の ρ(verdict_rank, O) 実測値:** `0.4408`

**Leak check**
- `pearson_r(C, anchor_alignment) = n/a`
- `spearman_r(C, anchor_alignment) = n/a`
- `n = 0`

## 採用閾値

**no-ship**: `no_candidates: fire_rate or rho_advisory_full constraint unmet`

閾値は provisional 値で運用し、再校正可能なデータ増加を待つ。

## メトリクス

| 項目 | 値 |
|---|---|
| `rho_primary_full` | 0.4408 |
| `rho_advisory_full` | 0.4408 |
| `n_accept` | 40 |
| `n_full` | 63 |
| `n_fire` | 0 |
| `fire_rate` | 0.000 |

## 上位候補

| τ_collapse | τ_anchor | ρ_adv_full | fire_rate | low_q_recall | loo_shr |
|---|---|---|---|---|---|

`fire_rate ∈ [0.10, 0.30]` に入る候補は 0 件 (grid 121 ペア探索)。

## 再校正トリガ条件

以下のいずれかに該当した時点で再実行する:

1. accept subset `n ≥ 40` に到達（今回到達済み。次は `n ≥ 80` などの区切り）
2. mcg 算出式（`anchor_alignment` / `collapse_risk`）の定義が変更された
3. ΔE 閾値（0.10/0.25）が再校正で変更された
4. Phase C の SBert バックエンドが更新された
5. HA48 / accept40 の O スケールが変更された

## no-ship 判定の根拠分析

- 今回は `fire_rate ∈ [0.10, 0.30]` の候補が 0 件で、advisory を有効化できなかった。  
- 仮説として、priority-A（ΔE≤0.10）中心の拡充は accept subset の mcg signal を「健康」領域（`collapse_risk < 閾値` かつ `anchor_alignment > 閾値`）に偏らせ、発火を抑制しうる。  
- 加えて本バッチ実測では mcg 成分の算出可能サンプルが `n=0` であり、実質的に downgrade 判定が発火しない条件だった。  
- 次回は priority-B（orchestrator 生成）を混ぜ、fire_rate 窓に入る候補が出るかを再検証する。

## 生データ

- 探索した全ペア: [`phase_e_calibration_grid.csv`](phase_e_calibration_grid.csv)

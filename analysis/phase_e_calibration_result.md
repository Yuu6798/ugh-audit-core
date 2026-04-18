# Phase E Calibration Result

生成日: 2026-04-18
データソース: HA48 (n=63 rows loaded)

## 採用閾値

- `τ_collapse_high = 0.28`
- `τ_anchor_low   = 0.80`

## メトリクス

| 項目 | 値 |
|---|---|
| `rho_primary_full` | 0.4408 |
| `rho_advisory_full` | 0.5225 |
| `rho_accept_subset` | 0.3875 |
| `fire_rate` | 0.225 |
| `low_quality_recall` | 0.500 |
| `single_rule_fire_ratio` | 0.333 |
| `n_accept` | 40 |
| `n_full` | 63 |
| `n_fire` | 9 |
| `loo_rho_mean` | 0.3874 |
| `loo_shrinkage` | 0.0001 |

## Leak check

- `pearson_r(C, anchor_alignment) = 0.37491628521826814`
- `spearman_r(C, anchor_alignment) = 0.4242326996087907`
- `n = 63`

解釈: `|r| < 0.50` のため leak は許容範囲。anchor_alignment は C とは独立の信号。

## 上位候補

| τ_collapse | τ_anchor | ρ_adv_full | fire_rate | low_q_recall | loo_shr |
|---|---|---|---|---|---|
| 0.28 | 0.80 | 0.522 | 0.225 | 0.500 | 0.000 |
| 0.36 | 0.80 | 0.514 | 0.200 | 0.500 | 0.000 |
| 0.38 | 0.80 | 0.514 | 0.200 | 0.500 | 0.000 |
| 0.40 | 0.80 | 0.514 | 0.200 | 0.500 | 0.000 |
| 0.32 | 0.80 | 0.514 | 0.200 | 0.500 | 0.000 |

## 採用理由

- ステータス: `ok`
- 候補中で `low_quality_recall` が最大、`fire_rate` は 10%〜30% 範囲内、`rho_advisory_full` が primary 基準 -0.02 の許容範囲内。
- HA48 (n=63) で校正。Phase E.1。

## 生データ

- 探索した全ペア: [`phase_e_calibration_grid.csv`](phase_e_calibration_grid.csv)

# Phase E Calibration Result

生成日: 2026-04-18
データソース: HA48 (n=63 rows loaded)

## 採用閾値

**no-ship**: no_candidates: fire_rate or rho_advisory_full constraint unmet

閾値をハードコードせず、provisional 値で実装する（動作確認用）。

## メトリクス

| 項目 | 値 |
|---|---|
| `rho_primary_full` (参照) | 0.4408 |

## Leak check

- `pearson_r(C, anchor_alignment) = n/a`
- `spearman_r(C, anchor_alignment) = n/a`
- `n = 0`

解釈: leak check のサンプル不足。

## 上位候補

| τ_collapse | τ_anchor | ρ_adv_full | fire_rate | low_q_recall | loo_shr |
|---|---|---|---|---|---|

## 採用理由

- ステータス: `no_candidates: fire_rate or rho_advisory_full constraint unmet`
- §4 の選択基準を満たす候補がないため、このバッチでは閾値を確定できない。provisional 値で実装し、HA96+ での再校正を待つ。

## 生データ

- 探索した全ペア: [`phase_e_calibration_grid.csv`](phase_e_calibration_grid.csv)

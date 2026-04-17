# Phase E Calibration Result

生成日: 2026-04-17
データソース: HA48 (n=48 rows loaded)

## 分布サマリー

**verdict 内訳 (HA48 n=48):**

| verdict | 件数 |
|---|---|
| accept | 13 |
| rewrite | 24 |
| regenerate | 11 |
| degraded | 0 |
| **合計** | **48** |

**accept subset (n=13) の mcg 成分 quantile:**

| 成分 | min | P25 | median | P75 | max |
|---|---|---|---|---|---|
| `collapse_risk` | 0.134 | 0.218 | 0.230 | 0.251 | 0.266 |
| `anchor_alignment` | 0.796 | 0.814 | 0.849 | 0.863 | 0.881 |

**primary verdict の ρ(verdict_rank, O) 実測値:** `0.5004`
（advisory ρ 比較の基準線。`VERDICT_QUALITY_RANK = {accept:2, rewrite:1, regenerate:0}` で順位化、`degraded` は除外）

**Leak check:** `pearson_r(C, anchor_alignment) = 0.278`（判定: **pass**, `< 0.50`）

## 採用閾値

校正結果に基づき以下の閾値を採用する:

- `τ_collapse_high = 0.26`
- `τ_anchor_low   = 0.80`

初版 grid `{0.50..0.90} × {0.10..0.50}` は accept subset 実分布
（collapse P75≈0.25, anchor P25≈0.80）から乖離しており、全ペアで
fire_rate=0 の false no-ship を招いていた（Codex review P1）。本稿の
採用値は修正 grid `{0.20..0.40} × {0.60..0.80}` (step=0.02) での再校正
結果に基づく。

## メトリクス

| 項目 | 値 |
|---|---|
| `rho_primary_full` | 0.5004 |
| `rho_advisory_full` | 0.5261 |
| `rho_accept_subset` | 0.3024 |
| `fire_rate` | 0.154 |
| `low_quality_recall` | 0.000 |
| `single_rule_fire_ratio` | 1.000 |
| `n_accept` | 13 |
| `n_full` | 48 |
| `n_fire` | 2 |
| `loo_rho_mean` | 0.3003 |
| `loo_shrinkage` | 0.0021 |

**注意: `low_quality_recall` について**

設計 §4 の定義は「`O <= 0.4` の問で downgrade が発火した率」だが、HA48 の
O スコアは 1–5 の整数スケールで運用されており、`O ≤ 0.4` に該当する問は
0 件。このため分母 0 で `low_quality_recall = 0.000` となった。実質的な
低品質リコールは以下の通り:

- accept subset で O が低い問: `q010 (O=2)`, `q053 (O=2)`
- 採用閾値で発火した問: `q010 (anchor=0.796 → anchor_missing)`, `q072 (collapse=0.266 → collapse_downgrade)`
- q010 は低品質で正しく発火（true positive）
- q072 は O=4 で高品質だが発火（false positive）

HA48 のスケール下では実質 recall = 1/2 = 0.5（低品質 accept 2 件中 1 件を
捕捉）。設計 §4 の threshold 0.4 は仕様ギャップであり HA96+ 時に見直す。

## Leak check

- `pearson_r(C, anchor_alignment) = 0.2777`
- `spearman_r(C, anchor_alignment) = 0.3187`
- `n = 48`

解釈: `|r| < 0.50` のため leak は許容範囲。`anchor_alignment` は `C` と
一定の相関を持つが独立の信号として扱える。

## 上位候補

| τ_collapse | τ_anchor | ρ_adv_full | fire_rate | low_q_recall | loo_shr |
|---|---|---|---|---|---|
| 0.26 | 0.80 | 0.526 | 0.154 | 0.000 | 0.002 |

## 採用理由

- ステータス: `ok`
- 唯一候補。設計 §4 の選択基準 (`rho_advisory_full >= rho_primary_full - 0.02`,
  `fire_rate ∈ [0.10, 0.30]`, `|pearson_r(C, anchor)| < 0.50`) を全て満たす。
- LOO shrinkage = 0.002（極小）。n=13 の accept サブセットの割にロバスト。
- ただし `low_quality_recall` は HA48 の O スケール (1–5) と設計 threshold
  (0.4) のミスマッチで 0.000 を記録。実スケールでは recall ≈ 0.5（上記「注意」参照）。

## 採用する実装値

`mode_grv.py`:

```python
_TAU_COLLAPSE_HIGH: float = 0.26
_TAU_ANCHOR_LOW: float = 0.80
```

primary verdict と `is_reliable` は不変（設計 §8 判断 3, 4）。advisory は
API レスポンスに追加するのみで、consumer は任意に参照する。

## 再校正トリガ条件

以下のいずれかに該当した時点で `calibrate_phase_e_thresholds.py` を再実行
する:

1. accept subset `n ≥ 40` に到達（HA96+ など）
2. mcg 算出式（`anchor_alignment` / `collapse_risk`）の定義が変更された
3. ΔE 閾値（0.10/0.25）が再校正で変更された
4. Phase C の SBert バックエンドが更新された

再校正時は grid も観測分布の quantile から再設定する（設計 §4 プロトコル追補）。

## 生データ

- 探索した全ペア: [`phase_e_calibration_grid.csv`](phase_e_calibration_grid.csv)
- 再実行: `python analysis/calibrate_phase_e_thresholds.py`

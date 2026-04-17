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

**再校正トリガ条件:** accept subset `n ≥ 40` に到達した時点で
`analysis/calibrate_phase_e_thresholds.py` を再実行する。現在の provisional
閾値 (`_TAU_COLLAPSE_HIGH=0.90`, `_TAU_ANCHOR_LOW=0.10`) は **発火ゼロの
sentinel** であり、運用閾値ではない。HA96+ 到達前に downgrade ロジックを
有効化してはならない。

## 結論: no-ship（暫定値で実装）

`docs/phase_e_verdict_integration.md` §4 の選択基準を満たす候補が存在しない。
プロトコルに従い閾値をハードコードせず、provisional 値で advisory plumbing のみを
実装する。HA96+ での再校正で閾値確定の再試行が必要。

**provisional 実装値（実質的に発火しない保守値）:**

- `_TAU_COLLAPSE_HIGH = 0.90`（grid 上限）
- `_TAU_ANCHOR_LOW   = 0.10`（grid 下限）

上記値は accept サブセットの観測分布（下記参照）からは発火しないため、
primary verdict との整合性を壊さずに advisory フィールドを API スキーマに
導入できる。テストは synthetic fixture で閾値を上書きして検証する。

## HA48 分布（accept サブセット）

accept サブセットでは `anchor_alignment`, `collapse_risk` が狭い帯域に
集中しており、設計 §4 の探索グリッド `{0.50..0.90} × {0.10..0.50}` の
範囲で発火する候補が 0 件になった。

| 成分 | min | max | mean |
|---|---|---|---|
| `anchor_alignment` (accept, n=13) | 0.796 | 0.881 | 0.842 |
| `collapse_risk` (accept, n=13)    | 0.134 | 0.266 | 0.224 |
| `anchor_alignment` (全体, n=48)   | 0.736 | 0.912 | 0.823 |
| `collapse_risk` (全体, n=48)      | 0.134 | 0.341 | 0.242 |

グリッド最下端の `τ_anchor_low=0.50` でも全 accept 行が >0.79 のため発火せず、
`τ_collapse_high=0.50` でも全行が <0.35 のため発火しない。

## メトリクス

| 項目 | 値 |
|---|---|
| `rho_primary_full` (参照, HA48 primary verdict) | 0.5004 |
| `rho_advisory_full` | 全候補で 0.5004（advisory ≡ primary, 発火ゼロ） |
| `fire_rate` (accept 内) | 0.000（全候補） |
| `low_quality_recall` | 0.000（全候補） |
| `n_accept` | 13 |
| `n_full` (degraded 除外) | 48 |

verdict 分布: `{'accept': 13, 'rewrite': 24, 'regenerate': 11}`

## Leak check

- `pearson_r(C, anchor_alignment) = 0.2777`
- `spearman_r(C, anchor_alignment) = 0.3187`
- `n = 48`

解釈: `|r| < 0.50` のため leak は許容範囲。`anchor_alignment` は `C` と一定の
相関を持つが独立の信号として扱える。この結果は将来の再校正時にも維持される
見込み。

## 上位候補

該当なし（全候補で fire_rate=0）。参考として `phase_e_calibration_grid.csv`
に探索した 81 ペアの全評価を出力してある。

## 採用理由

1. **設計プロトコルに従った no-ship 判定**
   設計 §4 は「条件を満たす候補が 1 つもない場合は、閾値をハードコードせず
   no-ship として結果を記録する」と明示。これに準拠。
2. **観測分布との乖離**
   Phase C 校正の `anchor_alignment ρ=+0.41` / `collapse_risk ρ=-0.32` は
   HA48 全体の相関であり、accept サブセット内では分布が圧縮され、設計の
   閾値グリッドでは downgrade 候補を拾えない。
3. **plumbing のみ先行実装する判断**
   - consumer (REST/MCP) スキーマを先に確定させる利点がある
   - 発火しない provisional 値なら primary verdict に影響を与えない
   - 将来 HA96+ でデータ量が増え accept 内の分散が拡大すれば、同一
     スクリプトで再校正して閾値を更新できる

## 次のステップ

- HA96+（あるいは accept n≥30）で同じスクリプトを再実行して再校正する
- 必要に応じて設計 §4 の探索グリッドを拡張する（例: `τ_anchor_low ∈ {0.50..0.80}`、
  `τ_collapse_high ∈ {0.25..0.50}`）
- accept サブセット内の分散が広がらない場合は、signal design の見直し
  （`anchor_alignment` / `collapse_risk` の算出式）を検討する

## 生データ

- 探索した全ペア: [`phase_e_calibration_grid.csv`](phase_e_calibration_grid.csv)
- 再実行: `python analysis/calibrate_phase_e_thresholds.py`

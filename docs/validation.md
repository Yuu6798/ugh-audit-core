# 検証結果 (HA48 / HA20 / ベースライン)

本ドキュメントは `ugh-audit-core` の主要指標 (ΔE / quality_score /
L_sem) の検証結果を一元管理する。

## HA48 検証結果

n=48, v5 ベースライン 197/310 hits, scipy.stats.spearmanr (タイ補正あり):

| 指標 | Spearman ρ | p 値 | 備考 |
|------|-----------|-----|------|
| **ΔE vs O (system C)** | **-0.5195** | **0.000154** | **現行デプロイ可能指標** |
| **L_sem vs O (Phase 4)** | **-0.5563** | **<0.001** | **L_F 独立項化で改善** |
| ΔE vs O (human C) | 0.8616 | <0.001 | 参照上限（ターゲット情報含む） |

### HA48 個別指標

| 指標 | 値 | 説明 |
|------|-----|------|
| Spearman ρ (ΔE vs O, system C) | -0.5195 (p=0.000154) | デプロイ可能指標 (scipy, タイ補正あり) |
| Spearman ρ (ΔE vs O, human C) | 0.8616 (p<0.001) | 参照上限 (scipy, タイ補正あり) |
| v5 ベースライン | 197/310 hits, cascade rescued 11 | audit_102_main_baseline_v5.csv |
| verdict 単調性 | accept(3.44) > rewrite(2.62) > regenerate(1.00) | HA48 検証済み |

## HA20 参考値 (n=20, t=0.0 統一スライス)

| 指標 | Spearman ρ | p 値 | 備考 |
|------|-----------|-----|------|
| ΔE (system C) | -0.7737 | <0.001 | n=20 サブセット |
| ΔE (human C) | -0.9266 | <0.001 | 参照上限 |
| S (構造完全性) | 0.5770 | 0.008 | f2 が主要寄与因子 |

## ボトルネックと今後の改善

- system 命題照合の精度改善が ΔE 改善のボトルネック
- 参照上限 ρ=0.862 との差は検出パイプラインの精度改善で縮まる

## 命題ヒット率ベースライン

102 問 × 310 命題の全件リラン結果:

| hit_source | 件数 | 割合 |
|-----------|------|------|
| tfidf | 184 | 59.4% |
| cascade_rescued | 5 | 1.6% |
| miss | 121 | 39.0% |
| **合計** | **310** | — |
| **命題ヒット率** | **189/310** | **61.0%** |

ベースライン CSV: `data/eval/audit_102_main_baseline_cascade.csv`

## HA48 統合アノテーション

HA20 (20 件) + HA28 (28 件) を統一スキーマで結合した 48 件データセット。

- **スキーマ**: `id, category, S, C, O, propositions_hit, notes`
- **S/C**: 全 48 件入力済み（HA20 は annotation_spec_v2 遡及テーブルから取得）
- **O**: HA20 は human_score (1-5)、HA28 は O (1-4)
- **統合 CSV**: `data/human_annotation_48/annotation_48_merged.csv`
- **生成スクリプト**: `scripts/merge_annotations_48.py`

## L_sem (Phase 4) HA48 校正結果

各項の単独 Spearman ρ (vs human O):

| 項 | ρ | p 値 | 備考 |
|---|---|---|---|
| L_F = f2 | **-0.3853** | 0.0068 | **単独最強** |
| L_P = 1-C | -0.3739 | 0.0088 | |
| L_Q = f3 | -0.1684 | 0.2525 | 信号弱 |
| L_R = f4 | -0.1259 | 0.3938 | 信号弱 |
| L_A = f1 | nan | — | HA48 で f1 全件 0 |

最適重み (grid search step=0.05, 正規化後):
`L_P=0.375, L_R=0.125, L_F=0.500` → ρ=-0.5563

詳細: [`semantic_loss.md`](semantic_loss.md)
校正スクリプト: `analysis/optimize_semantic_loss_weights.py`

## LLM オーケストレーション検証

自由質問に対する LLM 動的メタ生成 PoC の検証結果 (n=102):

- degraded 排除: 100%
- verdict 一致率: 61.8%
- ΔE 相関: ρ=0.378 (p<0.001)

敵対的 meta hack 実験 (n=30):
- C 軸は突破される (96.7%)
- S 軸に 50% の確率で痕跡が残る

詳細: [`orchestration_design.md`](orchestration_design.md)

## 分析データ

- `analysis/verdict_threshold_validation.md` — verdict 閾値校正
- `analysis/pipeline_a_correlation/` — ΔE 相関分析 (n=20, n=48)
- `analysis/ha48_regression_check.csv` — 回帰検証データ
- `analysis/n48_verification/` — HA48 マージ検証
- `analysis/semantic_loss_optimization_result.csv` — L_sem 校正成果物

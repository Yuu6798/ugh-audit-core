# 検証結果 (HA48 / HA20 / ベースライン)

本ドキュメントは `ugh-audit-core` の主要指標 (ΔE / quality_score /
L_sem) の検証結果を一元管理する。

## core vs cascade の分離 (API `hit_sources`)

`/api/audit` レスポンスの `hit_sources` フィールドで、命題ヒットを以下の 3
ソースに分離して公開している:

```jsonc
"hit_sources": {
  "core_hit": 2,              // tfidf hits (core pipeline, 決定的)
  "cascade_rescued": 1,       // cascade layer 回収 (SBert, 確率的)
  "miss": 0,
  "total": 3,
  "core_only_hit_rate": "2/3", // 決定性主張の分子 (tfidf-only)
  "per_proposition": {"0": "tfidf", "1": "cascade_rescued", "2": "tfidf"}
}
```

論文で「core pipeline は決定的」と主張する際の分子は **`core_only_hit_rate`**
の tfidf-only 件数。cascade を含む拡張結果は `core_hit + cascade_rescued`。
この分離により、査読で「決定性主張の scope」を外部から検証可能にする。

未検出 (命題総数 0) の場合は `"hit_sources": null`。詳細: `ugh_calculator.summarize_hit_sources()`。

## 主指標政策 (Primary Metric Policy)

| 指標 | 位置づけ | 根拠 |
|---|---|---|
| **ΔE (ΔE_A, system C)** | **主評価指標** | 決定的 core pipeline で算出。`S, C` からの 2 項合成、HA48 で ρ=-0.5195 (p=0.000154)。**verdict 境界 (0.10/0.25) は HA48 校正済み固定値** |
| **L_sem (Phase 5)** | **診断用指標** | 7 項線形和で劣化側面を項別に識別する用途。HA48 ρ=-0.6020 (Phase 5 full-sample) だが n=48 で LOO-CV shrinkage=0.128 を観測、runtime 重みは保守的に配分 |
| quality_score | 表示用 | `5 - 4×ΔE` の派生値 |
| verdict_advisory (Phase E) | 副次判定 | `mode_conditioned_grv` 由来の downgrade-only advisory。primary verdict は不変 |

**運用原則:**

- Deploy 時の go/no-go は **ΔE の verdict 境界** で行う。`is_reliable` も ΔE ベース
- L_sem は「どの項が悪いか」を debug する診断用。runtime 重みは
  `semantic_loss.py:DEFAULT_WEIGHTS` で L_P/L_F/L_G を優先、L_Q/L_A/L_X は
  保守的に保持（HA48 で信号弱いが理論的保持）
- 論文・レポートで「システムの相関」を主張する際は **ΔE ρ を主数字**として
  報告し、L_sem は補足診断として併記する
- 設計判断で「追加指標が必要」になる前に、まず L_sem の 7 項を見て原因分解する

**文脈:** `semantic_loss.py:34-47` の LOO-CV shrinkage コメントは、full-sample
最適重みと runtime 重みが異なる理由を記録したもの。主指標 ΔE を動かさず、
L_sem 側で保守的縮小をかける判断の履歴として保持している。

## HA48 検証結果

n=48, v5 ベースライン 197/310 hits, scipy.stats.spearmanr (タイ補正あり):

| 指標 | Spearman ρ | p 値 | 備考 |
|------|-----------|-----|------|
| **ΔE vs O (system C)** | **-0.5195** | **0.000154** | **ΔE baseline** |
| **L_sem vs O (Phase 4)** | **-0.5563** | **<0.001** | **L_P+L_F 2項最適化** |
| **L_sem vs O (Phase 5)** | **-0.6020** | **<0.001** | **L_P+L_F+L_G 3項最適化 (grv 統合)** |
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

## 信頼区間 (95% CI, Fisher z 変換)

報告済み Spearman ρ の Fisher z 変換ベース 95% 信頼区間:

| 指標 | n | ρ 点推定 | 95% CI |
|---|---|---|---|
| HA48 ΔE vs O (system C) | 48 | -0.5195 | [-0.7003, -0.2761] |
| HA48 ΔE vs O (human C, 参照上限) | 48 | +0.8616 | [+0.7625, +0.9210] |
| HA48 L_sem Phase 5 vs O | 48 | -0.6020 | [-0.7567, -0.3835] |
| HA20 ΔE vs O (system C) | 20 | -0.7737 | [-0.9060, -0.5036] |
| HA20 ΔE vs O (human C) | 20 | -0.9266 | [-0.9710, -0.8200] |

計算式: `z = atanh(ρ)`, `SE = 1/sqrt(n-3)`, `CI = tanh(z ± 1.96*SE)`。
再現:

```python
from scipy.stats import spearmanr
import math
def fisher_ci(rho, n, alpha=0.05):
    z = math.atanh(rho); se = 1.0 / math.sqrt(n - 3)
    zc = 1.959963984540054
    return math.tanh(z - zc*se), math.tanh(z + zc*se)
```

## Limitations

本システムの検証結果を査読・論文・導入判断で利用する際の前提条件:

### n=48 (HA48) の統計的薄さ

HA48 は核評価データだが **n=48 は統計的には小標本**。主指標 `ΔE vs O
(system C)` の点推定 ρ=-0.5195 は強い相関だが、**95% CI 下端 -0.2761 は
ρ=-0.50 の運用閾値を下回る**。すなわち「`|ρ| ≥ 0.5` の主張は点推定では
成立するが、CI ベースでは保証されない」状態。

含意:

- HA48 単独で「相関強度 0.5 超」を断定しない
- HA20 (n=20) との合算や、accept サブセット拡張 (accept40 = 40 件) で
  段階的に精度を上げる運用
- 大規模 (n≥100) での再検証は将来課題。新規アノテーションは `docs/annotation_protocol.md` の手順で計画

Phase 5 L_sem (ρ=-0.6020) も同様で、CI 下端は -0.3835。点推定で ΔE を
上回るが、CI ベースでの優位は保証されない。

### Single-Annotator Constraint (IRR 不在)

HA48 / HA20 / HA28 の全アノテーションは **single annotator (プロジェクト
著者) による作業**。複数アノテータによる作業は現状実施されておらず、
**inter-rater reliability (IRR) は未測定**。

これが意味すること:

- 参照上限 `ρ=0.8616` (ΔE vs O, human C) は **single-annotator 前提下の
  上限値**。複数アノテータ間で annotator agreement がどの程度か不明のため、
  真の参照上限を過大評価している可能性がある
- system C の evaluation は参照 C への一致度を測っているが、参照 C 自体
  の信頼性区間は本検証では算出されていない
- 「O スコア」「C スコア」の値は annotator の判断が反映されており、
  annotator が変われば値も変わりうる

Mitigation（部分的対応）:

- アノテーション手順を `docs/annotation_protocol.md` に codify し、将来
  2nd annotator が合流した際に IRR 測定を走らせる前提を整備
- `data/human_annotation_accept40/snapshots/` で annotation 過程の
  中間成果物を保全し、後追い検証を可能にしている

将来課題として **2nd annotator を入れた IRR 測定** を `docs/annotation_protocol.md`
の `Future Work` に明示する。

### ベースライン比較の不在（本リポジトリ上で）

現行リポジトリには BERTScore / BLEURT / BLEU 等の既存手法との HA48/HA20
上での直接比較は収録されていない。将来リリース (`docs/roadmap.md` — WIP)
で HA48/HA20 上での直接測定を計画。

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

## L_sem (Phase 5) HA48 校正結果

### 各項の単独 Spearman ρ (vs human O)

| 項 | ρ | p 値 | n | 備考 |
|---|---|---|---|---|
| L_F = f2 | **-0.3853** | 0.0068 | 48 | **単独最強 (f-flag 系)** |
| L_P = 1-C | -0.3739 | 0.0088 | 48 | |
| **L_G = grv** | **-0.3565** | **0.0129** | **48** | **grv 統合 (Phase 5 新規)** |
| L_Q = f3 | -0.1684 | 0.2525 | 48 | 信号弱 |
| L_R = f4 | -0.1259 | 0.3938 | 48 | 信号弱 |
| L_A = f1 | nan | — | 48 | HA48 で f1 全件 0 |
| L_X (polarity) | -0.0885 | 0.7354 | 17 | n 不足、信号なし |

### grv 成分別相関

| 成分 | ρ(成分, O) | 備考 |
|------|-----------|------|
| drift | -0.3304 | 主成分 |
| collapse_v2 | -0.3191 | |
| cover_soft | +0.3144 | 正の相関 (到達度) |
| wash_index | -0.2814 | |
| dispersion | -0.2057 | |

### 重み最適化 (段階的グリッドサーチ)

| 構成 | 最適 ρ | Δρ vs baseline | 最適重み |
|------|--------|---------------|---------|
| ΔE baseline | -0.5195 | — | — |
| L_P + L_F (Phase 4) | -0.5342 | +0.015 | L_P=0.075, L_F=0.100 |
| **L_P + L_F + L_G (Phase 5)** | **-0.6020** | **+0.083** | **L_P=0.425, L_F=0.275, L_G=0.850** |
| +L_R | -0.6119 | +0.092 | L_P=0.85, L_F=0.70, L_G=0.65, L_R=0.05 |
| +L_Q | -0.5976 | +0.078 | L_Q=0.00 (増分寄与なし) |

L_G 増分寄与: Δρ=+0.068 (P+F のみ -0.5342 → P+F+G -0.6020)

### Phase 5 確定 DEFAULT_WEIGHTS (LOO-CV 補正後の runtime 値)

```python
DEFAULT_WEIGHTS = {
    "L_P": 0.27,   # 命題損失 (LOO mean 比率ベース)
    "L_Q": 0.02,   # 制約損失 (HA48 信号なし、理論的保持)
    "L_R": 0.03,   # 参照安定性 (HA48 Δρ=+0.01 微弱増分)
    "L_A": 0.02,   # 曖昧性増大 (HA48 全零、理論的保持)
    "L_G": 0.35,   # 因果構造 (LOO-CV 補正: 0.48→0.35、過学習抑制)
    "L_F": 0.21,   # 用語捏造 (LOO mean 比率ベース)
    "L_X": 0.10,   # 極性反転 (理論的保持、L_G 削減分の一部再配分)
}
```

注: full-sample 最適では `L_P=0.425, L_F=0.275, L_G=0.850` で ρ=-0.6020
を達成 (§93 表参照) だが、LOO-CV で shrinkage=0.128 (n=48 で不安定) が
検出されたため、LOO mean 比率 `L_P:L_F:L_G ≈ 0.41:0.30:0.91` を正規化
して保守的に配分した結果が上記 runtime。`semantic_loss.py:34-47` と同期。

### grv タグ閾値校正

HA48 grv 分布: mean=0.185, σ=0.051, range=[0.10, 0.31]

| 閾値 | 旧 (暫定) | 新 (HA48 校正) |
|------|----------|---------------|
| TAG_MID | 0.33 | **0.20** |
| TAG_HIGH | 0.66 | **0.30** |

旧閾値では全48件が low_gravity に分類され、タグ分類が機能していなかった。

詳細: [`semantic_loss.md`](semantic_loss.md)
校正スクリプト: `analysis/calibrate_grv_lsem.py` (Phase 5), `analysis/optimize_semantic_loss_weights.py` (Phase 4)

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
- `analysis/grv_lsem_calibration_result.csv` — grv/L_sem Phase 5 統合校正成果物

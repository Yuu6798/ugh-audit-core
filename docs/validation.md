# 現行契約と旧検証記録

## schema 4.0.0 の判定範囲

現行の対象は `instruction_coverage_v2`、すなわち明示された指示・制約の
記述充足である。人が用意した各要件を `fulfilled` / `missing` / `unknown` に
照合する。回答の真実性・一般的な品質・現実の作業実施を認定しない。
要件自体の妥当性も別途確認する。AI が生成した要件は `requires_review=true`。

| 要件照合 | 最終判定 | 上位の数値 |
|---|---|---|
| 有効な要件が1件以上あり全件 fulfilled | accept | C=1 |
| unknown がなく、missing がある | rewrite | C=fulfilled/全要件数 |
| unknown がある、要件なし・不正、照合不能 | degraded | C=null |

上位 `S`・`delta_e`・`quality_score` は常に null。
`metrics_scope="semantic_coverage_only"`。保留を分母から除かない。
旧数値と判定は `legacy_metrics`、構造ゲートは `binding=false` として残す。
これらの診断値は上位の判定を上下させない。旧 ΔE 閾値を承認条件に使わない。

要件準備後に25秒の意味照合予算を開始し、任意の埋め込み・旧診断より先に照合する。
認証・API・引用・モデル・時間切れ等の検証に失敗した要件は保留となる。
Python/CLI の修復は確認済み欠落に対する `ADD_PROPOSITION` のみを提案し、
旧構造指標から修復順序を生成しない。契約・制限・互換性は
[`semantic_matching_v2.md`](semantic_matching_v2.md) を参照。

回帰テストは判定契約や過去応答の再生を検査するものであり、それだけで意味精度の
改善や単純な LLM 監査に UGH を加える価値を証明しない。以下の旧数値比較も、
その上乗せ価値の根拠として扱わない。

## 旧診断の core vs cascade の分離 (`legacy_metrics.hit_sources`)

旧検出器の命題ヒットは以下の3ソースに分離する。schema 4 の上位
`hit_sources` は LLM の fulfilled / missing / unknown であり、以下は
`legacy_metrics.hit_sources` の診断例である。

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

## 旧主指標政策の保存記録 (schema 4 の承認条件には使わない)

| 指標 | 位置づけ | 根拠 |
|---|---|---|
| **ΔE (ΔE_A, system C)** | **主評価指標** | 決定的 core pipeline で算出。`S, C` からの 2 項合成、HA48 で ρ=-0.4817 (p=0.000527)。**verdict 境界 (0.10/0.25) は HA48 校正済み固定値** |
| **L_sem (Phase 5)** | **診断用指標** | 7 項線形和で劣化側面を項別に識別する用途。HA48 ρ=-0.5477 (Phase 5 full-sample, post-#95) だが n=48 で LOO-CV shrinkage=0.1997 を観測、runtime 重みは保守的に据え置き |
| quality_score | 表示用 | `5 - 4×ΔE` の派生値 |
| verdict_advisory (Phase 8) | 副次判定 | `mode_conditioned_grv` 由来の downgrade-only advisory。primary verdict は不変 |

当時は ΔE の境界を go/no-go に使い、L_sem を診断用としていた。
schema 4 ではその方針を採用しない。旧重みと数値式は診断・再現のため保持する。

**旧方針の文脈:** `semantic_loss.py:34-47` の LOO-CV shrinkage コメントは、full-sample
最適重みと runtime 重みが異なる理由を記録したもの。主指標 ΔE を動かさず、
L_sem 側で保守的縮小をかける判断の履歴として保持している。

## HA48 既知データの回帰版 (2026-10-28)

否定極性チェックの適用経路を修正し、同じ 48 回答を再実行した現行の
legacy 診断スナップショットは **96/146 hits**。変化は q012 の 3→2 hits と
その派生値のみで、人手評価 `O` は変更していない。修正前の **97/146 hits** の
CSV は `analysis/snapshots/ha48_regression_check.v2.csv` にバイト単位で保存した。
これは機械出力の版更新であり、新しい未見データによる独立評価や意味精度の
改善を示す人手検証ではない。版間の全差分は
[`ha48_snapshot_versions.md`](ha48_snapshot_versions.md) を参照。

以下の HA48 統計表は 2026-04 時点の保存記録であり、表内の `current` は
その当時の版を指す。現行 CSV の回帰一致で、これらの旧統計を保証するものではない。

## HA48 検証結果 (2026-04 保存記録)

n=48, current pipeline snapshot (2026-04-22), scipy.stats.spearmanr (タイ補正あり):

| 指標 | Spearman ρ | p 値 | 備考 |
|------|-----------|-----|------|
| **ΔE vs O (system C)** | **-0.4817** | **0.000527** | **ΔE baseline (current)** |
| **L_sem vs O (Phase 4)** | **-0.4693** | **<0.001** | **L_P+L_F 2項最適化 (current pipeline, post-#95)** |
| **L_sem vs O (Phase 5)** | **-0.5477** | **<0.001** | **L_P+L_F+L_G 3項最適化 (current pipeline, post-#95)** |
| ΔE vs O (human C) | 0.8616 | <0.001 | 参照上限（ターゲット情報含む） |

**測定精度履歴 (ΔE vs O, system C):**

| pipeline revision | ρ (点推定) | 備考 |
|---|---|---|
| Apr 6, 2026 (v5 baseline) | -0.5195 | 初版 HA48 snapshot。検出層精度改善前の intermediate 値 |
| **Apr 21, 2026 (current)** | **-0.4817** | **検出層精度改善後（PR #95 `original_*` key fallback 等を反映）** |

両版とも旧 CI [-0.70, -0.28] の範囲内にあり統計的には区別困難だが、測定精度が
**測定精度履歴 (L_sem Phase 5):**

| pipeline revision | ρ (点推定) | 最適重み | 備考 |
|---|---|---|---|
| Apr 6, 2026 (v5 baseline) | -0.6020 | L_P=0.425, L_F=0.275, L_G=0.850 | 初版 HA48 校正 (pre-#95) |
| **Apr 22, 2026 (current)** | **-0.5477** | **L_P=0.075, L_F=0.075, L_G=0.325** | **post-#95 再校正** |

改善した結果として点推定が -0.5195 → -0.4817 へ移動。主数字は current revision
(ρ=-0.4817) を用いていた。2026-10-28 以降の `tests/test_ha48_regression.py` は
上記の版管理された legacy 診断出力の回帰を検査する。

### HA48 個別指標 (2026-04 保存スナップショット)

| 指標 | 値 | 説明 |
|------|-----|------|
| Spearman ρ (ΔE vs O, system C) | -0.4817 (p=0.000527) | デプロイ可能指標 (scipy, タイ補正あり) |
| Spearman ρ (ΔE vs O, human C) | 0.8616 (p<0.001) | 参照上限 (scipy, タイ補正あり) |
| HA48 hit 総数 | 97/146 | 修正前 v2 CSV として保存。現行 legacy 診断は 96/146 |
| verdict 単調性 | accept(3.44) > rewrite(2.67) > regenerate(1.25) | current pipeline 検証済み |

## HA20 参考値 (旧検証記録、n=20, t=0.0 統一スライス)

| 指標 | Spearman ρ | p 値 | 備考 |
|------|-----------|-----|------|
| ΔE (system C) | -0.7737 | <0.001 | n=20 サブセット |
| ΔE (human C) | -0.9266 | <0.001 | 参照上限 |
| S (構造完全性) | 0.5770 | 0.008 | f2 が主要寄与因子 |

## 信頼区間 (95% CI, Fisher z 変換)

報告済み Spearman ρ の Fisher z 変換ベース 95% 信頼区間:

| 指標 | n | ρ 点推定 | 95% CI |
|---|---|---|---|
| HA48 ΔE vs O (system C) | 48 | -0.4817 | [-0.6736, -0.2289] |
| HA48 ΔE vs O (human C, 参照上限) | 48 | +0.8616 | [+0.7647, +0.9204] |
| HA48 L_sem Phase 5 vs O | 48 | -0.5477 | [-0.7198, -0.3121] |
| HA20 ΔE vs O (system C) | 20 | -0.7737 | [-0.9060, -0.5036] |
| HA20 ΔE vs O (human C) | 20 | -0.9266 | [-0.9710, -0.8205] |

注: HA48 ΔE (system C) は current pipeline (2026-04-21) の値。
pipeline 前版 (Apr 6) では ρ=-0.5195 / CI=[-0.7003, -0.2761]。いずれも旧 CI の
範囲内で重なり、測定精度差分の範疇。詳細: §「HA48 検証結果 → 測定精度履歴」。
L_sem Phase 5 も pre-#95 ρ=-0.6020 / CI=[-0.7567, -0.3835] から、
post-#95 ρ=-0.5477 / CI=[-0.7198, -0.3121] へ更新した。

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
(system C)` の current 点推定 ρ=-0.4817 に対する **95% CI は [-0.6736, -0.2289]**
で、CI 下端は ρ=-0.50 の運用閾値を下回る。すなわち「`|ρ| ≥ 0.5` の主張は
CI ベースでは保証されない」状態（点推定でも 0.4817 は 0.5 を僅かに下回る）。

含意:

- HA48 単独で「相関強度 0.5 超」を断定しない
- HA20 (n=20) との合算や、accept サブセット拡張 (accept40 = 40 件) で
  段階的に精度を上げる運用
- 大規模 (n≥100) での再検証は将来課題。新規アノテーションは `docs/annotation_protocol.md` の手順で計画

Phase 5 L_sem (ρ=-0.5477) も同様で、CI 下端は -0.3121。点推定で ΔE を
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

### ベースライン比較 (旧 pipeline の保存記録)

本リポジトリで HA20 / HA48 上の (response, reference, human O) トリプルに
対し、以下 3 baseline との直接比較を実施済み:

- **BLEU (sacrebleu, char tokenization)** — 古典的 lexical overlap
- **BERTScore F1 (xlm-roberta-base, lang='ja')** — 多言語 contextual embedding
- **SBert cosine (paraphrase-multilingual-MiniLM-L12-v2)** — semantic 距離

**向きの統一:** 全 metric を "高 similarity = 良回答" に揃え正相関を期待。
UGHer は `1 - ΔE` で変換、O との Spearman ρ を比較。

**HA20 (n=20):**

| 指標 | ρ | 95% CI |
|---|---|---|
| BLEU | +0.492 | [+0.064, +0.768] |
| BERTScore F1 | +0.556 | [+0.150, +0.801] |
| SBert cos | +0.428 | [-0.018, +0.732] |
| **UGHer (ΔE, pipeline)** | **+0.770** | **[+0.496, +0.904]** |

**HA48 (n=48):**

| 指標 | ρ | 95% CI |
|---|---|---|
| BLEU | +0.318 | [+0.037, +0.552] |
| BERTScore F1 | +0.331 | [+0.052, +0.562] |
| SBert cos | +0.261 | [-0.025, +0.507] |
| **UGHer (ΔE, pipeline)** | **+0.482** | **[+0.229, +0.674]** |

### 差の統計有意性 (Steiger's Z, dependent correlations)

同一サンプルで UGHer と各 baseline の ρ の差 Δρ を Steiger's Z test で検定。
両側 p 値、α=0.05 で判定。

**HA20 (n=20):**

| vs baseline | Δρ | Steiger Z | p | 有意 (α=0.05) |
|---|---|---|---|---|
| BLEU | +0.277 | +1.502 | 0.133 | no |
| BERTScore | +0.214 | +1.189 | 0.234 | no |
| SBert cos | +0.341 | +1.759 | **0.079** | no (**10% 水準では有意方向**) |

**HA48 (n=48):**

| vs baseline | Δρ | Steiger Z | p | 有意 (α=0.05) |
|---|---|---|---|---|
| BLEU | +0.164 | +0.984 | 0.325 | no |
| BERTScore | +0.151 | +0.910 | 0.363 | no |
| SBert cos | +0.221 | +1.380 | 0.168 | no |

**解釈:**

- **全ペアで α=0.05 有意差なし**。UGHer の点推定優位 (Δρ=+0.15〜+0.34) は
  効果量として medium 以上だが、n=20/48 の検出力不足で統計有意性は未達
- ただし **HA20 UGHer vs SBert は p=0.079** で 10% 水準では有意方向 — 同一
  内部モデル (SBert) に対する UGHer 合成の優位性が最も明瞭
- 統計有意性を α=0.05 で確立するには **n≥100 への annotation 拡張**が
  実質的に必要。Δρ=+0.15 で n=100 なら p < 0.05 の見込み
- **現 n で査読で書ける主張:**
  - 「UGHer is the highest point-estimate correlation across all baselines
    on both datasets」
  - 「UGHer's correlation with human O is statistically significant
    (HA20 p<0.001, HA48 p<0.001)」 — **UGHer 単独の有意性のみ言明**
  - 「BLEU and BERTScore are also individually significant
    (HA20 p=0.027, 0.011; HA48 p=0.028, 0.022)」 — 個別 baseline の
    有意性は指標別に明示する
- **現 n で書けない主張:**
  - 「UGHer significantly outperforms baselines」 (Steiger's Z 非有意のため
    過大主張)
  - 「All metrics are individually significant」 — **SBert cos は両 dataset
    で α=0.05 非有意** (HA20 p=0.0596, HA48 p=0.0735)、全指標一括の有意性
    主張は SBert で破綻する。SBert の扱いは個別に記述する必要がある

**解釈の範囲:** これらは旧サンプルと旧指標に対する比較であり、単純な LLM 監査を
比較対象にしていない。点推定の順位や同じ SBert の利用だけから、合成指標の有用性・
構成要素の因果的な寄与・v4 の性能向上を結論しない。BLEURT は当時の比較対象外。

**再現:**

```bash
pip install -e ".[baseline]"   # bert-score + sacrebleu + sentence-transformers + scipy を self-contained で導入
python analysis/baseline_comparison.py
```

詳細: [`analysis/baseline_comparison.py`](../analysis/baseline_comparison.py),
per-qid score CSV (`analysis/baseline_comparison_ha{48,20}.csv`),
[`analysis/baseline_comparison_summary.md`](../analysis/baseline_comparison_summary.md)。

## 現行契約に対する評価の扱い

要件の記述充足、事実の正しさ、一般的な回答品質を区別して評価する。
旧 HA48 の相関や回帰一致を、v4 の独立評価として数えない。UGH 診断の追加価値を
主張するには、同条件の単純な LLM 監査と比較した別の根拠が必要となる。

## 命題ヒット率ベースライン (旧検証記録)

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

## L_sem (Phase 5) HA48 校正結果 (旧検証記録)

### 各項の単独 Spearman ρ (vs human O)

| 項 | ρ | p 値 | n | 備考 |
|---|---|---|---|---|
| L_F = f2 | **-0.3853** | 0.0068 | 48 | **単独最強 (f-flag 系)** |
| L_P = 1-C | -0.3060 | 0.0344 | 48 | |
| **L_G = grv** | **-0.3565** | **0.0129** | **48** | **grv 統合 (Phase 5 新規)** |
| L_Q = f3 | -0.1684 | 0.2525 | 48 | 信号弱 |
| L_R = f4 | -0.1259 | 0.3938 | 48 | 信号弱 |
| L_A = f1 | nan | — | 48 | HA48 で f1 全件 0 |
| L_X (polarity) | -0.0726 | 0.7817 | 17 | n 不足、信号なし |

### grv 成分別相関

| 成分 | ρ(成分, O) | 備考 |
|------|-----------|------|
| drift | -0.3304 | 主成分 |
| collapse_v2 | -0.3191 | |
| cover_soft | +0.3144 | 正の相関 (到達度) |
| wash_index | -0.2814 | |
| dispersion | -0.2057 | |

### 重み最適化 (段階的グリッドサーチ)

以下は **current pipeline (post-#95) の再校正 snapshot**。
pre-#95 値は §測定精度履歴 (L_sem Phase 5) に退避し、主表は current 値で統一する。

| 構成 | 最適 ρ | Δρ vs baseline | 最適重み |
|------|--------|---------------|---------|
| ΔE baseline (current system C) | -0.4817 | — | — |
| L_P + L_F (Phase 4) | -0.4693 | -0.012 | L_P=0.075, L_F=0.100 |
| **L_P + L_F + L_G (Phase 5)** | **-0.5477** | **+0.066** | **L_P=0.075, L_F=0.075, L_G=0.325** |
| +L_R | -0.5673 | +0.086 | L_P=0.30, L_F=0.30, L_G=0.65, L_R=0.05 |
| +L_Q | -0.5477 | +0.066 | L_Q=0.00 (増分寄与なし) |

L_G 増分寄与: Δρ=+0.078 (P+F のみ -0.4693 → P+F+G -0.5477)

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

注: full-sample 最適では `L_P=0.075, L_F=0.075, L_G=0.325` で ρ=-0.5477
を達成したが、LOO-CV 再測定で ρ=-0.3479 / shrinkage=0.1997 (n=48) を観測。
fold 間の重み分散も大きいため、runtime `DEFAULT_WEIGHTS` は本リリースでは据え置き
（`semantic_loss.py:34-47`）とし、n 拡張後に再判定する。

### grv タグ閾値校正

HA48 grv 分布: mean=0.185, σ=0.051, range=[0.10, 0.31]

| 閾値 | 旧 (暫定) | 新 (HA48 校正) |
|------|----------|---------------|
| TAG_MID | 0.33 | **0.20** |
| TAG_HIGH | 0.66 | **0.30** |

旧閾値では全48件が low_gravity に分類され、タグ分類が機能していなかった。

詳細: [`semantic_loss.md`](semantic_loss.md)
校正スクリプト: `analysis/calibrate_grv_lsem.py` (Phase 5), `analysis/optimize_semantic_loss_weights.py` (Phase 4)

## LLM オーケストレーション検証 (旧検証記録)

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

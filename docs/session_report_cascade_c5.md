# Cascade Tier 3 c5 緩和 — セッションレポート

## 実施日: 2026-04-02

## サマリー
- 判定: **CONDITIONAL GO**
- concept_absent: 1/10
- hard_negative: 0/5
- ovl_insufficient: 3/5

## 各ステップの結果

### Step 1: 現状確認
- ファイル 7 件すべて確認済み
- Tier 3 c5 の対象テキスト: top1_sentence のみ
- dev_cascade_20: 20 行、atomic units 36 個
- 既存テスト: 253 件

### Step 2: c5 緩和（段階的 3 パターン試行）

| パターン | c_absent | h_neg | ovl_insuf | 採用 |
|----------|----------|-------|-----------|------|
| A (top1+top2) | 0/10 | 0/5 | 2/5 | — |
| B (response全文) | 1/10 | 0/5 | 3/5 | ✓ |
| C | スキップ | — | — | — |

採用理由: パターン B が concept_absent +1（q016_p0）、ovl_insufficient +1（q048_p2）を追加回収。hard_negative 誤救済なし。

### Step 3: c4 修正（dev_cascade_20 差し替え）
- 除外: q064_p0, q064_p2（f4_flag=0.5 で c4 blocked）
- 追加: q052_p1, q052_p2（f4_flag=0.0、ai_philosophy/concept_absent）
- 効果: ±0（q052_p1 は c5 で blocked、q052_p2 は c2 で blocked）

### Step 4: c3 条件付き緩和
- 条件: top1_score > 0.70 → δ_gap = 0.02（デフォルト 0.04）
- 効果: ±0（q090_p0 の gap=0.0158 < 0.02 のため該当ケースなし）
- hard_negative 最大 score=0.6124 < 0.70 → 安全弁維持

## ボトルネック分析

concept_absent 残り 9 件の阻害要因内訳:

| 阻害パターン | 件数 | 該当 id |
|-------------|------|---------|
| c2: score < θ (0.50) | 4 | q016_p1, q065_p1, q065_p2, q052_p2 |
| c2: gap < effective_δ (pass_t2_eff=False) | 3 | q090_p0, q090_p1, q016_p2 |
| c5: atomic 不整合 | 2 | q030_p0, q052_p1 |

主要ボトルネック:
1. **score < θ (4件)**: SBert の弁別力不足。命題と response の意味的距離が embedding 空間で十分に近くない。θ=0.50 は hard_negative 排除のために必要な下限。
2. **gap 不足 (3件)**: response 内の複数文が命題に均等に類似し、top1 が突出しない。q090_p0 (score=0.751) は高スコアだが gap=0.016 で脱落。
3. **atomic 不整合 (2件)**: response 全文に atomic unit の左辺・右辺が部分文字列として含まれない。q030_p0 の右辺「過大評価リスクを生む」が response の表現と乖離。

## 最終パラメータ

### Tier 2
- θ_sbert: 0.50
- δ_gap: 0.04
- モデル: paraphrase-multilingual-MiniLM-L12-v2

### Tier 3
- c1: tier1_hit == False（二重カウント防止）
- c2: top1_score >= θ_sbert かつ pass_t2_eff（effective_delta 使用）
- c3: gap >= effective_delta かつ pass_t2_eff
  - effective_delta = 0.02 if top1_score > 0.70 else 0.04
- c4: f4_flag == 0.0
- c5: response 全文で atomic 整合チェック（aligned_count >= 1）

### 緩和パラメータ
- HIGH_SCORE_THRESHOLD: 0.70
- RELAXED_DELTA_GAP: 0.02

## 次のアクション候補

1. **θ_sbert の引き下げ (0.50 → 0.45)**: Grid search で θ=0.45 でも precision=1.000 を維持。concept_absent 4件の score（0.40-0.49）をカバー可能。ただし hard_negative q022_p0 (score=0.4955) が閾値に接近するリスクあり。
2. **gap 条件の完全撤廃 + c5 強化**: gap フィルタを外し、atomic 整合チェックの閾値を aligned_count >= 2 に引き上げることで precision を維持。q090_p0 (score=0.751, gap=0.016) の回収が見込める。
3. **atomic_units の再設計**: q030_p0, q052_p1 の atomic_units を response の表現に合わせて再定義。表層マッチ依存の限界に対処。
4. **LLM Shadow Scoring の導入**: cascade_design.md に記載済みの設計。Tier 3 判定と並行して LLM に判定させ、Tier 3 が落とした命題の回収可能性を測定。
5. **dev_cascade_20 の拡充**: 20件では統計的検出力が不足。50-100件規模への拡張で閾値校正の信頼性を向上。

## テスト状況
- 既存テスト: 253 passed / 3 skipped
- 新規追加テスト: なし

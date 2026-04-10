# 検出層 (detector.py) 設計ドキュメント

`detector.py` は Audit Engine の検出層であり、4 つの検出指標 (f1–f4) と
命題カバレッジから `Evidence` を生成する。本ドキュメントは演算子フレーム検出と
Relaxed Tier1 Safety Valve の設計詳細をまとめる。

## 演算子フレーム検出

`detect_operator()` が命題中の演算子を検出し、`check_propositions()` の
回収パスで活用される。

```python
from detector import detect_operator, OperatorInfo, OPERATOR_CATALOG

op = detect_operator("低ΔEは良い回答を保証しない")
# OperatorInfo(family='negation', token='しない', position=13)
```

### OPERATOR_CATALOG — 4 族定義

| 族 | effect | priority | 典型パターン |
|----|--------|----------|-------------|
| negation | polarity_flip | 2 | ではない / 未〜 / 不可能 |
| deontic | normative_flag | 1 | べき / すべきではない |
| skeptical_modality | certainty_downgrade | 3 | かもしれない / とは限らない |
| binary_frame | contrastive_split | 1 | ではなく / 二項対立 |

### 共起ルール (priority で解決)

- deontic + negation → deontic 優先 (「べきではない」は当為表現)
- skeptical + binary_frame → binary_frame 優先

### 回収パスの多層ゲート

1. `detect_operator(prop)` で演算子検出
2. 概念近傍マーカーチェック (文レベルスコーピング)
3. 緩和閾値: `direct_recall ≥ 0.10`, `full_recall ≥ 0.25`, `overlap ≥ 2`
4. 極性検証 (節レベルスコーピング + 推量表現除外):
   - negation (polarity_flip): 回答の概念近傍に否定形が必要
   - negative deontic (べきではない等): 同上
   - positive deontic (すべき): 回答が否定していたら却下
   - skeptical_modality: 極性チェック不要

### 否定 deontic トークン一覧

`check_propositions()` 内の `neg_deontic` タプルで定義される 6 バリアント:

- べきではない / すべきではない
- べきでない / すべきでない
- べきじゃない / すべきじゃない

`semantic_loss.py` の `_NEG_DEONTIC_TOKENS` もこの定義と同期する必要がある。

## Relaxed Tier1 Safety Valve（緩和閾値による命題回収）

`check_propositions()` の通常閾値 (direct≥0.15, full≥0.30, overlap≥3) で
miss した命題に対し、低い閾値で再判定する安全弁。`detect()` が
`relaxed_context` を自動付与する。

### 昇格条件 (全て AND)

1. 緩和閾値を通過（バイグラム数に応じた段階的閾値）
2. `_relaxed_candidate_allowed`: 内容チャンクの文レベル一致 + 汎用チャンクのみ除外
3. 極性検証 (`needs_polarity_full` / `is_positive_deontic`) を通過
4. `fail_max < 1.0` (構造的欠陥がない)
5. 現状 ΔE ≤ 0.04 かつ relaxed ΔE ≤ 0.04 (既に高品質なケースのみ)

### 極性チェックの 2 層構造

- **メイン hit パス**: `needs_polarity_deontic` (deontic 否定のみ)
- **演算子回収パス + relaxed パス**: `needs_polarity_full` (polarity_flip + deontic)

### 緩和閾値 (バイグラム数別)

| 命題サイズ | direct_recall | full_recall | min_overlap |
|---|---|---|---|
| 大 (≥8 bg) | ≥ 0.10 | ≥ 0.30 | ≥ 2 |
| 中 (≥5 bg) | ≥ 0.12 | ≥ 0.30 | ≥ 2 |

### 関連ファイル

- 実験スクリプト: `analysis/threshold_validation/run_proposition_hit_experiment.py`
- テスト: `tests/test_relaxed_tier1.py`

## Evidence の構造

`detect()` は `ugh_calculator.Evidence` (frozen dataclass) を返す。
主要フィールド:

| フィールド | 型 | 説明 |
|---|---|---|
| `f1_anchor` | float (0.0/0.5/1.0) | 主題逸脱 |
| `f2_unknown` | float (0.0/0.5/1.0) | 用語捏造 |
| `f3_operator` | float (0.0/0.5/1.0) | 演算子未処理 |
| `f4_premise` | Optional[float] | 前提受容 (None 可) |
| `propositions_hit` / `propositions_total` | int | 命題照合結果 |
| `hit_ids` / `miss_ids` | List[int] | 命題インデックス |
| `hit_sources` | Dict[int, str] | `"tfidf"` / `"cascade_rescued"` / `"miss"` |
| `f3_operator_family` | str | 検出された演算子族 |
| `f4_trap_type` | str | 検出された trap_type |

## 参考

- Cascade 詳細: [`cascade_design.md`](cascade_design.md)
- 意味損失関数: [`semantic_loss.md`](semantic_loss.md)
- 電卓層の計算式: [`formulas.md`](formulas.md)

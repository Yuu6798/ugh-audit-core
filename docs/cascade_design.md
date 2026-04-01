# Cascade Tier 2 設計ドキュメント

## 概要

cascade_matcher は detector.py の既存ロジックを変更せず、**判定層から呼ばれる補助モジュール**として
命題回収を試みる。Tier 2 は SBert embedding による候補生成、Tier 3（将来実装）で精密フィルタを行う。

## モジュール構成図

```
cascade_matcher.py
├── load_model()            # SentenceTransformer ロード（キャッシュ付き）
├── encode_texts()          # batch encoding ラッパー
├── split_response()        # response → 文/節リスト
└── tier2_candidate()       # 命題 × response → 候補生成 + スコア
    ├── split_response()
    ├── encode_texts()
    └── cosine_similarity
```

**呼び出し関係:**
- 判定層（ugh_calculator.py 等）→ `tier2_candidate()`
- `tier2_candidate()` → `split_response()`, `encode_texts()`
- detector.py からは **呼ばない**

## split_response 仕様

### 基本ルール
1. 句点「。」で分割
2. 80字超の文は読点「、」でさらに分割を試行
3. 空文字列・空白のみは除外
4. 前後空白を strip

### エッジケース一覧

| ケース | 入力例 | 処理 |
|--------|--------|------|
| 箇条書き（「・」「-」開始） | `・項目1。・項目2。` | 句点で分割後、各項目を独立文として扱う。箇条書きマーカーは保持。 |
| 括弧内の句点 | `（参考文献参照。）以降は…` | 括弧内の句点では分割しない。全角括弧 `（）` および半角 `()` を保護。 |
| 改行のみで区切られた文 | `文1\n文2` | 改行も分割境界として扱う（句点なし改行 = 暗黙の文境界）。 |
| 80字超で読点なし | `aaaa...（100字）` | 分割不能。そのまま1文として扱う。 |
| 連続句点 | `文。。次の文。` | 空文字列を除外して処理。 |
| Markdown 太字 | `**重要**な点。` | マーカーは除去せず保持。embedding は文脈で処理。 |

### 実装上の注意
- 括弧保護は正規表現で括弧内の句点を一時プレースホルダに置換 → 分割後に復元
- 改行分割は句点分割の前に実施（`\n` → 句点相当として扱う）

## Embedding モデル選定理由

**paraphrase-multilingual-MiniLM-L12-v2**

| 基準 | 評価 |
|------|------|
| 日本語対応 | 50言語対応。日本語パラフレーズで学習済み |
| サイズ | 471MB。CI/ローカルで実行可能 |
| 速度 | CPU でも実用的（~100文/秒） |
| 実績 | Phase C v0/v1 で使用済み。calibration_notes.md に結果蓄積あり |
| 弁別幅 | 日本語で Δ≈0.1–0.3（狭い）→ θ 設計で考慮必須 |

**既知の制約（Phase C v0 からの知見）:**
- PoC hit 率 0.850 だが ρ=0.160（順位相関が低い → 過剰マッチ傾向）
- θ=0.50 で F1 最大化した経験あり
- **SBert は過剰マッチしやすい → Tier 3 フィルタが必須**

## パラメータ校正手順

### θ_sbert（cosine similarity 閾値）
- 初期値: **0.55**（Phase C の θ=0.50 より保守的）
- 校正: dev_cascade_20 上で grid search（0.45–0.65、0.05 刻み）
- 評価指標: should_rescue の rescue 率、must_reject の false rescue 率

### δ_gap（top1 - top2 のギャップ閾値）
- 初期値: **0.05**
- 校正: dev_cascade_20 上で grid search（0.03–0.10、0.01 刻み）
- 目的: top1 が突出していない場合（団子状態）を除外

### 校正プロトコル（scripts/run_cascade_poc.py）
1. dev_cascade_20.csv を読み込み
2. 全20件に Tier 2 を実行
3. θ × δ の全組合せで pass_tier2 を計算
4. 各組合せの指標:
   - rescue_count: should_rescue + may_rescue で pass_tier2=True の件数
   - false_rescue: must_reject で pass_tier2=True の件数
   - precision: rescue_count / (rescue_count + false_rescue)
   - recall: rescue_count / 15（should_rescue 10 + may_rescue 5）
5. F1 最大化する (θ, δ) を推奨値として提案

## Tier 3 へ渡すデータ構造

```python
@dataclass
class Tier2Result:
    proposition: str          # 元の命題テキスト
    top1_sentence: str        # 最も類似度が高い文/節
    top1_score: float         # cosine similarity
    top2_score: float         # 2番目の類似度
    gap: float                # top1 - top2
    all_scores: list[float]   # 全文/節のスコア（デバッグ用）
    pass_tier2: bool          # θ_sbert AND δ_gap を満たすか
```

Tier 3 では `top1_sentence` に対して:
- 否定極性の検証（negation polarity check）
- 前提受容の検出（f4 premise gate）
- 概念近傍マーカーの再検証

を実施し、最終的な hit/miss を判定する。

## テストケース設計

### Positive テスト（pass_tier2=True を期待）
- concept_absent: q064_p0「嘘は意図的欺瞞を前提とする」
  - response に「嘘をつくことは意図的に誤った情報を提供する行為」と明記
  - top1_score ≥ θ_sbert が期待される

### Negative テスト（pass_tier2=False を期待）
- hard_negative: q098_p0「『感情を持たない』は確定事項ではない」
  - response は前提を断定受容（極性逆転）
  - top1_score は高いかもしれないが、Tier 3 で reject される設計
  - ※ Tier 2 単体では reject できない可能性あり → Tier 3 の必要性を示す

### split_response テスト
- 基本分割: 3文 → 3要素
- 80字超: 長文 → 読点で分割
- 括弧内句点: 分割されないことを確認
- 空文字列: 除外されることを確認

---

## Tier 3: 多条件フィルタ

### 概要

Tier 2 を通過した候補に対し、以下の全条件を AND で判定。
1つでも fail → miss 確定。全 pass → Z_RESCUED。

### フィルタ条件一覧

| # | 条件 | 判定方法 | fail 時の意味 |
|---|------|---------|-------------|
| c1 | tfidf miss 確認 | tier1_hit == False | 二重カウント防止 |
| c2 | embedding 閾値 | top1_score >= θ_sbert | 類似度不足 |
| c3 | gap 閾値 | gap >= δ_gap | 候補が団子＝弁別不能 |
| c4 | f4 非発火 | f4_flag == 0.0 | 前提受容の疑い |
| c5 | atomic 整合 | atomic 1単位以上が top1_sentence に含まれる | 表層類似だが命題と不整合 |

### f4 参照の実装

`structural_gate_summary.csv` を `Dict[str, float]` にロードし、
`question_id + temperature=0.0` で lookup する。

```python
f4_map = load_f4_flags("data/gate_results/structural_gate_summary.csv")
f4 = f4_map.get(question_id, 0.0)
```

f4_flag の値:
- 0.0 → pass（前提受容なし）
- 0.5 → warn（部分的前提受容）→ **Tier 3 で reject**
- 1.0 → fail（明確な前提受容）→ **Tier 3 で reject**

### atomic 整合チェック

各 atomic を `|` で split し、左辺（主語/対象）と右辺（述語/属性）の
**両方**が `top1_sentence` に含まれるかを判定。

含有判定（OR で評価）:
1. 完全一致
2. `synonym_dict` での展開後の一致（detector.py の `_SYNONYM_MAP` を再利用）
3. 3文字以上の部分文字列一致

synonym_dict は `from detector import _SYNONYM_MAP` でインポートする。
detector.py 内の dict リテラルとして管理されているため、外部ファイル化は不要。

### 閾値チューニング手順

θ_sbert × δ_gap の grid search は `scripts/run_cascade_poc.py` で実行:
- θ: 0.45–0.65（0.05 刻み）
- δ: 0.03–0.10（0.01 刻み）
- 評価指標: precision, recall, F1（should_rescue + may_rescue vs must_reject）

### LLM Shadow Scoring 設計

Phase 1 では Tier 3 判定と並行して LLM にも判定させる:
1. Tier 3 が Z_RESCUED と判定した命題を LLM に提示
2. LLM も独立に hit/miss を判定
3. 結果は記録するが **merged_hit には混ぜない**
4. 目的: Tier 3 が落とした命題で LLM が rescue する率を計測

### 撤退条件の自動チェック

| 条件 | 閾値 | アクション |
|------|------|----------|
| hard_negative 誤救済 | >= 2 | cascade 不採用 |
| concept_absent 回収 | == 0 | SBert 弁別力不足 |

### PoC 受理基準

| 指標 | 閾値 | 判定 |
|------|------|------|
| concept_absent 回収数 | >= 3 | PASS/FAIL |
| hard_negative 誤救済数 | == 0 | PASS/FAIL |
| ovl_insufficient 回収数 | >= 2 | PASS/FAIL |
| Tier 1 回帰 | == 0 | PASS/FAIL |
| 総合 | 全 PASS | GO/NO-GO |

---

## PoC 実行結果（dev_cascade_20, 2026-04-01）

### スコア分布

| category | n | mean | std | min | max |
|----------|---|------|-----|-----|-----|
| concept_absent | 10 | 0.606 | 0.145 | 0.396 | 0.841 |
| hard_negative | 5 | 0.465 | 0.116 | 0.323 | 0.612 |
| ovl_insufficient | 5 | 0.650 | 0.123 | 0.460 | 0.834 |

**弁別力:** concept_absent (0.606) vs hard_negative (0.465) でΔ=0.14。日本語 SBert の弁別幅 Δ≈0.1–0.3 の範囲内だが、分布が重なっている（hard_negative max=0.612 > concept_absent mean=0.606）。

### Gap (δ) の重要性

hard_negative 5件中4件の gap < 0.04。一方 concept_absent で pass した3件はすべて gap > 0.15。
**gap フィルタが hard_negative 排除に有効**。

### Grid Search 結果

| θ | δ | rescue | false_rescue | precision | recall | F1 |
|---|---|--------|-------------|-----------|--------|-----|
| **0.45** | **0.04** | **6** | **0** | **1.000** | **0.400** | **0.571** |
| 0.55 | 0.04 | 6 | 0 | 1.000 | 0.400 | 0.571 |
| 0.45 | 0.03 | 6 | 1 | 0.857 | 0.400 | 0.545 |
| 0.65 | 0.03 | 4 | 0 | 1.000 | 0.267 | 0.421 |

### 推奨初期値

| パラメータ | 推奨値 | 根拠 |
|-----------|--------|------|
| θ_sbert | **0.50** | θ=0.45–0.55 で同等性能。中間値を採用。 |
| δ_gap | **0.04** | δ=0.04 で false_rescue=0 を達成。δ=0.03 では1件漏れ。 |

### 課題と次のステップ

1. **Recall が 0.40 (6/15)** — concept_absent 10件中3件 + ovl_insufficient 5件中3件のみ rescue。残り9件は top1_score < θ または gap < δ で落ちている。
2. **gap が小さい高スコアケース**（q090_p0: score=0.751, gap=0.016）は Tier 3 での回収を検討。
3. **δ_gap 緩和の代替:** gap 条件を外し、Tier 3 の精密フィルタに委ねる設計も検討余地あり。その場合 θ_sbert=0.55 以上に引き上げて precision を維持する。
4. **Tier 3 の必須性:** q054_p2 (hard_negative) は top1_score=0.612 かつ gap=0.040 で、δ=0.03 なら pass してしまう。Tier 3 の前提受容チェックが不可欠。

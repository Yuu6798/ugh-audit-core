# Cascade Tier 2 設計ドキュメント

## 概要

cascade_matcher は detector.py の既存ロジックを変更せず、**判定層から呼ばれる補助モジュール**として
命題回収を試みる。Tier 2 は SBert embedding による候補生成、Tier 3（将来実装）で精密フィルタを行う。

## モジュール構成図

```
cascade_matcher.py
├── load_model()                  # SentenceTransformer ロード（モジュール関数）
├── get_shared_model()            # プロセス内共有シングルトン
├── encode_texts()                # batch encoding ラッパー（キャッシュ非経由）
├── encode_texts_cached()         # 永続キャッシュ経由の encode
├── _infer_model_id()             # SBert インスタンスからモデル識別子を推論
├── _make_cache_key()             # (model_name, text) → sha256[:24]
├── _load/_save_embedding_cache() # .npz 永続化
├── split_response()              # response → 文/節リスト
└── tier2_candidate()             # 命題 × response → 候補生成 + スコア
    ├── split_response()
    ├── encode_texts_cached()
    └── cosine_similarity
```

**呼び出し関係:**
- 判定層（ugh_calculator.py 等）→ `tier2_candidate()`
- `tier2_candidate()` → `split_response()`, `encode_texts_cached()`
- detector.py からは **直接呼ばない**（`tier2_candidate()` / `tier3_filter()` 経由のみ）
- detector.py / golden_store.py → `get_shared_model()`（共有シングルトン）

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
    top2_sentence: str        # 2番目に類似度が高い文/節
    top2_score: float         # 2番目の類似度
    gap: float                # top1 - top2
    all_scores: list[float]   # 全文/節のスコア（デバッグ用）
    pass_tier2: bool          # θ_sbert AND δ_gap を満たすか
```

Tier 3 では以下のチェックを実施し、最終的な hit/miss を判定する:
- c1: Tier 1 miss 確認（二重カウント防止）
- c2/c3: embedding スコア + gap 閾値（条件付き緩和あり）
- c4: f4 非発火（前提受容チェック）
- c5: atomic 整合（response 全文に対して判定）

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
| c4 | f4 非発火 | f4_flag < 1.0 | 前提受容確定（f4=1.0）のみブロック |
| c5 | atomic 整合 | atomic 1単位以上が **response 全文**に含まれる | 表層類似だが命題と不整合 |

### f4 参照の実装

`structural_gate_summary.csv` を `Dict[str, float]` にロードし、
`question_id + temperature=0.0` で lookup する。

```python
f4_map = load_f4_flags("data/gate_results/structural_gate_summary.csv")
f4 = f4_map.get(question_id, 0.0)
```

f4_flag の値:
- 0.0 → pass（前提受容なし）
- 0.5 → pass（部分的前提受容 → 緩和対象、2026-04-02 変更）
- 1.0 → fail（明確な前提受容）→ **Tier 3 で reject**

### atomic 整合チェック

各 atomic を `|` で split し、左辺（主語/対象）と右辺（述語/属性）の
**両方**が response 全文（`tier3_filter` に渡された `response` 引数、未指定時は `top1_sentence` にフォールバック）に含まれるかを判定。

含有判定（OR で評価）:
1. 完全一致
2. `synonym_dict` での展開後の一致（detector.py の `_SYNONYM_MAP` を再利用）
3. 3文字以上の部分文字列一致

synonym_dict は `from detector import _SYNONYM_MAP` でインポートする。
detector.py 内の dict リテラルとして管理されているため、外部ファイル化は不要。

### c5 対象テキストの変更（2026-04-02）

c5 の対象テキストを `top1_sentence` → **response 全文**に変更。
`tier3_filter()` に `response` 引数を追加し、`run_cascade_full()` から渡す。
`response=None` の場合は `top1_sentence` にフォールバック（後方互換）。

変更理由: top1_sentence のみでは atomic unit の左辺・右辺が含まれないケースが多く、
concept_absent の回収が制限されていた。response 全文にすることで ovl_insufficient が +1 改善。

### c4 閾値緩和（2026-04-02）

c4 条件を `f4_flag == 0.0` → `f4_flag < 1.0` に変更。
f4_flag=0.5（部分的前提受容/warn）を通過させ、f4_flag=1.0（確定 fail）のみブロック。

変更理由: f4_flag=0.5 は前提受容が「部分的」であり、cascade の他条件（c2/c3/c5）で
十分にフィルタリングできる。q064_p0（score=0.84, gap=0.15）が c4 のみで blocked されていた。
hard_negative で f4=0.5 の3件（q022_p0, q093_p0, q098_p0）は c2/c3 で blocked 済み。

### c3 条件付き緩和（2026-04-02）

top1_score が十分高い場合、gap 閾値を緩和して c2/c3 の通過率を改善する。

```python
HIGH_SCORE_THRESHOLD = 0.70
RELAXED_DELTA_GAP = 0.02

# tier3_filter 内:
effective_delta = relaxed_delta if top1_score > high_score_threshold else delta
```

`pass_tier2` に依存せず、`tier3_filter` 内で `n_segments`, `gap`, `score` から
独立に `pass_t2_eff` を再計算する。
パラメータはモジュール定数 + `tier3_filter` のデフォルト引数で外部から調整可能。

判定結果: CONDITIONAL GO（concept_absent 1/10, hard_negative 0/5）。
現 dev_cascade_20 では緩和が発動するケースなし（q090_p0 gap=0.016 < 0.02）。

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

---

## 永続埋め込みキャッシュ（2026-04-10 追加）

### 動機

HA48 反復評価や閾値チューニングでは、同一命題・同一リファレンスに対して
`tier2_candidate()` が繰り返し呼ばれる。毎回 SBert で再エンコードするのは
無駄なので、(model_id, text) をキーにした永続キャッシュを導入した。

MemPalace (ChromaDB + all-MiniLM-L6-v2) の「一度エンコードしたら永続化」
という設計思想を借用しているが、ChromaDB は過剰なので `.npz` + in-memory
dict の最小構成としている。

### キーとキャッシュ構造

| 項目 | 内容 |
|---|---|
| **キー** | `sha256(model_id || '\x00' || text)` の先頭 24 hex 文字（96 bit） |
| **値** | `np.ndarray` (dtype=float32, shape=(D,))  |
| **メモリ表現** | `Dict[str, np.ndarray]` |
| **ディスク表現** | `.npz`（各エントリを `savez(**dict)` で保存） |
| **保存先** | `~/.ugh_audit/embedding_cache.npz` |
| **衝突確率** | 24 hex = 96 bit、本プロジェクト規模では実質ゼロ |

### キャッシュ対象の選別（Codex review r3067115341 対応）

永続キャッシュに入れるのは **再利用性の高いテキストのみ**。具体的には:

| テキスト種別 | キャッシュ経由 | 理由 |
|---|---|---|
| リファレンス命題 (proposition) | ✅ | HA48 反復評価で同一命題が繰り返し使われる |
| GoldenStore の entry.question | ✅ | reference 検索で繰り返し照会される |
| **AI回答の response segments** | ❌ | AI回答ごとに一意で二度と使われない |
| **GoldenStore の query (ユーザー質問)** | ❌ | 多くの場合 one-off クエリ |

`tier2_candidate` では proposition のみ `encode_texts_cached` 経由で、
segments は `encode_texts` 直接呼び出しで処理する:

```python
prop_emb = encode_texts_cached(model, [proposition])[0]  # ✅ cached
seg_embs = encode_texts(model, segments)                 # ❌ bypass
```

これを守らないと、1 回の HA48 評価 (~48 response × ~10 segments) で
~480 個の使い捨てエントリが cache に残り、継続実行でキャッシュが単調増加、
`.npz` の full-file load/rewrite コストが累積的に悪化する。

### Shape guard / stale cache 検出（Codex review r3067145596 対応）

モデル識別子（`_infer_model_id()` の結果文字列）は同一でも、ローカル
パスや retagged checkpoint で **モデル重みが更新されて次元が変わる**
ケースが起こり得る。この場合、キャッシュ上の stale vector と新しい
モデルが返す vector で次元が一致せず、`_cosine_similarity_batch` が
numpy の matrix shape error で abort する → detector flow 全体が graceful
に degrade できない。

層状防御を実装:

1. **Cache 層の dim tracking** (`cascade_matcher._cache_embed_dim`)
   - 初回エントリ投入時または `_load_embedding_cache()` 時に記録
   - `invalidate_embedding_cache(reason="...")` でメモリ cache + dim を破棄
   - 次回 save 時に空状態が disk に反映される（古い .npz を上書き）

2. **`tier2_candidate` の proactive shape check**
   - `prop_emb`（possibly cached）と `seg_embs`（fresh）の次元を比較
   - mismatch → `invalidate_embedding_cache()` → `encode_texts_cached()`
     再実行 → 新しい dim でリフレッシュ
   - 再エンコード後も mismatch（= caller 側のモデル不整合）の場合は
     `pass_tier2=False` の空結果を返して safe degrade

3. **`_sbert_rerank` の同等 guard**
   - `query_emb`（fresh）と `target_embs`（possibly cached）の次元を比較
   - 同じ invalidate → re-encode → 最悪 `None` 返却パターン

これにより:
- stale cache を起因とする `ValueError: shapes ... not aligned` は発生しない
- detector.py の cascade パイプラインは必ず完了する（Tier 1 のみで継続可能）
- GoldenStore の `find_reference()` は最悪 bigram fallback に落ちる

fingerprint-based cache key（SBert weights hash を key に含める方式）は
起動時に全 tensors をハッシュする必要があり過剰なため採用しない。

### 容量上限（hard cap）

defense-in-depth として、`_MAX_CACHE_ENTRIES`（デフォルト 10000）を超えた
場合、新規エントリは返却はするがメモリ/ディスクには永続化しない。
LRU ではなく単純な hard cap で、`_logger.warning` を出す。

`UGH_AUDIT_EMBED_CACHE_MAX` 環境変数で調整可能:

```bash
UGH_AUDIT_EMBED_CACHE_MAX=50000 pytest ...   # 上限緩和
UGH_AUDIT_EMBED_CACHE_MAX=1000 python ...    # 上限厳格化
```

本来は「キャッシュ対象の選別」で one-off テキストを排除しているため
cap に達することはないはずだが、将来の追加呼び出し箇所で間違って
one-off テキストを渡された場合の保険。

### モデル識別子の auto-inference

`encode_texts_cached(model, texts, model_name=None)` の `model_name=None`
時は `_infer_model_id(model)` が実 SBert インスタンスから識別子を抽出する。
異なるモデルを渡したときに silent にキャッシュ衝突するのを防ぐため、
**default MODEL_NAME へのフォールバックは絶対に行わない**（Codex review
[#60 r3067060572](https://github.com/Yuu6798/ugh-audit-core/pull/60#discussion_r3067060572)
の指摘で修正済み）。

抽出試行順:

1. `model._first_module().auto_model.config._name_or_path`
   （HuggingFace config の標準パス）
2. `model.model_card_data.base_model`
   （新しめの sentence-transformers）
3. `tokenizer.name_or_path`
4. いずれも取れなければ `"unknown-model:<ClassName>"` を返す
   （キー分離は保証されるが、プロセス間の永続共有は期待しない）

### 永続化戦略

- **読み込み**: 初回 `encode_texts_cached` 呼び出しで lazy load
- **書き込み**: dirty flag 付きで in-memory に蓄積
- **永続化タイミング**: `atexit` フックによるプロセス終了時の一括保存
- **Atomic write**: `.npz.tmp` に書いた後 `Path.replace()` で差し替え
- **破損回復**: 読み込み時に `np.load` が失敗したら警告ログを出して空
  スタート
- **並行プロセス対策**: 書き出し前に disk 上の現在状態を reload して
  自プロセスの in-memory と merge してから書き戻す
  （in-memory 優先、dim consistency + 容量上限チェック付き）
- **Invalidation 優先**: `invalidate_embedding_cache()` 後は
  `_invalidation_pending` フラグで次回 save の merge を無効化し、
  in-memory 状態で authoritative に上書きする（disk 上の stale エントリ
  が merge で復活するのを防ぐ）

### 並行プロセスセマンティクス（Codex review r3067185...）

**カバーする問題**: 2 つのプロセス A / B が同じキャッシュファイルに
書き込むと、後に exit した側が先に exit した側の新規エントリを silent
に上書き削除する lost-update 問題。

**対策**: 書き出し前の **reload-then-merge**:

```
save():
  if invalidation_pending:
      # Authoritative write（merge しない）
      disk = in_memory
  else:
      merged = dict(in_memory)        # 自プロセスが優先
      for k, v in disk.items():
          if k in merged: continue     # 既存は上書きしない
          if v.shape[0] != cache_dim: continue  # 次元不一致は stale
          if len(merged) >= MAX: break  # 容量上限
          merged[k] = v                 # 他プロセスの追加を取り込む
      disk = merged
```

**カバーしない限界**: load と write の間の race window（非常に小さい）。
ここで lost update が起きうるのは最大 1 エントリで、次回呼び出し時に
自然に再キャッシュされる。File lock を使った厳密な排他は、研究段階の
scale（HA48）と運用想定（シングル or 少数プロセス）に対して過剰と判断
して採用しない。production scale に移行した場合は portalocker / fcntl
ベースの lock 追加を検討する。

### 環境変数

| 変数名 | 効果 | デフォルト |
|---|---|---|
| `UGH_AUDIT_EMBED_CACHE_DISABLE` | `1/true/yes` で完全無効化（毎回 encode） | 無効化しない |
| `UGH_AUDIT_CACHE_DIR` | キャッシュディレクトリ変更 | `~/.ugh_audit/` |
| `UGH_AUDIT_EMBED_CACHE_MAX` | 容量上限（hard cap） | 10000 |

無効化すると `encode_texts()` に直接フォールバックし、キャッシュファイル
の読み書きは一切行わない。CI や読み取り専用環境で使う。

### 公開 API

```python
from cascade_matcher import (
    encode_texts_cached,         # 主要エントリ: cached batch encoding
    clear_embedding_cache,       # メモリクリア（テスト用）
    flush_embedding_cache,       # 即座にディスク永続化
    embedding_cache_stats,       # {entries, loaded, dirty, disabled}
    invalidate_embedding_cache,  # stale cache 検出時の全破棄
)
```

テストでは `clear_embedding_cache()` + `monkeypatch.setattr` で
`_EMBED_CACHE_PATH` を `tmp_path` に差し替えて隔離する。

### テストカバレッジ

`tests/test_embedding_cache.py`（27 件）で以下を検証:

| テスト | 検証内容 |
|---|---|
| `test_cache_miss_then_hit` | 2 回目は encode がスキップされる |
| `test_partial_cache_hit` | 混在時に新規分のみ encode |
| `test_order_preservation` | 出力順序が入力順序と一致 |
| `test_model_name_isolation` | 明示 model_name による分離 |
| `test_persistence_across_reload` | flush → clear → reload でヒット |
| `test_disabled_cache_bypasses` | 無効化フラグで毎回 encode |
| `test_empty_input` | 空リスト処理 |
| `test_make_cache_key_stability` | ハッシュ安定性 |
| `test_stats_reports_state` | stats の報告 |
| `test_corrupted_cache_file_recovers` | 破損ファイルからの回復 |
| `test_infer_model_id_from_config_name_or_path` | 属性抽出の正常系 |
| `test_infer_model_id_fallback_to_class_name` | 未解決時 MODEL_NAME に落ちない |
| `test_encode_texts_cached_auto_infers_model_id` | auto-inference |
| `test_different_models_do_not_share_cache_via_tier2_candidate` | Codex r3067060572 の回帰テスト |
| `test_explicit_model_name_overrides_inference` | 明示指定による上書き |
| `test_cache_respects_capacity_cap` | cap 超過時はベクトルは返すが永続化しない |
| `test_capacity_cap_subsequent_call_still_returns_uncached_vectors` | cap 超過分は次回も miss 扱い |
| `test_tier2_candidate_only_caches_proposition` | segments が cache に入らない |
| `test_tier2_candidate_reuses_cached_proposition_across_responses` | prop は hit、segs だけ毎回 encode |
| `test_tier2_candidate_segments_do_not_fill_cache_over_runs` | 複数 response で cache サイズが prop 数に収束 |
| `test_invalidate_embedding_cache_clears_state` | invalidate で dim tracker もクリア |
| `test_cache_embed_dim_tracked_on_new_entries` | 初回投入時の dim 記録 |
| `test_tier2_candidate_recovers_from_stale_cache_dim` | Codex r3067145596 回帰: stale cache から gracefully recover |
| `test_tier2_candidate_does_not_raise_on_persistent_mismatch` | invalidation 後も不一致なら safe degrade |
| `test_save_merges_with_concurrent_disk_additions` | Codex r3067185 回帰: 並行プロセスの追加分を保持 |
| `test_merge_skips_disk_entries_with_wrong_dim` | merge 時の dim consistency check |
| `test_invalidation_pending_skips_merge` | invalidate 後は merge スキップして authoritative write |

実 SBert モデルを使わない fake encoder で完結するため、SBert 未導入の CI
環境でも全件実行される。

## 共有モデルシングルトン

`cascade_matcher.get_shared_model()` はプロセス内で SBert インスタンスを
1 回だけロードするシングルトン。`detector.py` と `ugh_audit/reference/
golden_store.py` の両方から利用され、モデルロードの重複を避ける。

- 初回呼び出し時に `load_model()` を実行
- **Bounded retry** (Codex review r3067206914 対応): ロード失敗時は
  `_MAX_SHARED_LOAD_ATTEMPTS` (デフォルト 3) までは次回呼び出しで再試行
  する。HF 初回 DL の I/O hiccup などの一過性失敗で SBert が恒久的に
  disable されるのを防ぐ。一方、連続 N 回失敗後は None を返し続け、
  真に欠損しているケースで毎コール重いロードを繰り返すコストを防ぐ
- 成功時は失敗カウンタをリセット（断続的失敗後の成功も正常動作）
- sentence-transformers 未導入環境でも `None` を返すのみでエラーは投げない
  （`_HAS_SBERT=False` は failure カウントを消費しない）

以前は `detector.py` が独自にシングルトンを持っていたが、`golden_store.py`
の Stage 3 再スコアでも SBert を使うため、重複ロードを避けるため
cascade_matcher に統一した。


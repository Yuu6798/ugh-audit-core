# GoldenStore リファレンス検索設計

## 概要

`ugh_audit/reference/golden_store.py` は、質問テキストに対する「期待される
理想回答（reference）」を管理する軽量ストア。`find_reference(question)` が
質問文字列を受け取り、最も近いリファレンスを返す。

従来は bigram Jaccard の単段検索だったが、2026-04-10 に **3 段階検索** に
拡張し、リファレンスセットのスケール時の retrieval 品質低下を抑える設計と
した（MemPalace の Wings/Rooms メタデータフィルタ + 再ランキング設計から
発想を借用）。

## 検索パイプライン

```
query
  │
  ▼
[Stage 1] 完全部分文字列一致
  │  hit → reference 返却（終了）
  │  miss
  ▼
[Stage 2] bigram Jaccard で候補プール生成 (top-K=5, min=0.1)
  │  候補 0 件 → None 返却（終了）
  │  候補 1 件 → その reference を返却（終了）
  │  候補 2 件以上
  ▼
[Stage 3] SBert 再スコア + gap 条件
  │  SBert 利用不可 → Stage 2 top1 にフォールバック
  │  gap 十分 → SBert top1 の reference を返却
  │  gap 不足 → Stage 2 top1 にフォールバック（保守的）
```

## 各ステージの詳細

### Stage 1: 完全部分文字列一致

`question in entry.question` または `entry.question in question` の直接
包含チェック。最も信頼度が高いため最優先。

### Stage 2: bigram Jaccard 候補プール

日本語の形態素解析を避けるため、文字レベル bigram の Jaccard 係数を使う。

```python
bigrams(text) = {text[i:i+2] for i in range(len(text) - 1)}
jaccard(q, e) = |bigrams(q) ∩ bigrams(e)| / |bigrams(q) ∪ bigrams(e)|
```

- `top_k = 5`: 候補プール上限
- `min_score = 0.1`: 採用最低閾値（従来と同一）
- スコア降順でソートして上位 K 件を返す

### Stage 3: SBert 再スコア + gap 条件

候補が 2 件以上ある場合、`cascade_matcher.get_shared_model()` で共有される
SBert インスタンスを使って各候補の `entry.question` と query の cosine
similarity を計算し、候補を再ランキングする。

**エンコードのキャッシュ方針** (Codex review #60 r3067133071 対応):

- `question` (ユーザークエリ): one-off なので `encode_texts()` で直接
  エンコードし、永続キャッシュを汚染しない
- `entry.question` (リファレンス): 再利用性が高いため
  `encode_texts_cached()` 経由でキャッシュする

この分離により、多様なクエリに対して呼ばれても cache サイズは
GoldenStore のエントリ数で頭打ちとなる。

再ランキング後、以下の gap 条件で top1 を採用するかを決める:

| 条件 | 閾値 | 由来 |
|---|---|---|
| θ（実質未使用 / Stage 2 の min_score で代替） | — | cascade_matcher.THETA_SBERT |
| δ_gap | 0.04 | cascade_matcher.DELTA_GAP |
| high_score | > 0.70 | cascade_matcher.HIGH_SCORE_THRESHOLD |
| relaxed_δ_gap | 0.02 | cascade_matcher.RELAXED_DELTA_GAP |

```python
effective_delta = (
    RELAXED_DELTA_GAP if top1_score > HIGH_SCORE_THRESHOLD else DELTA_GAP
)
if gap >= effective_delta:
    return reranked_top1.reference      # 再ランキングを採用
else:
    return bigram_top1.reference        # 保守的にフォールバック
```

**設計ポイント**: gap 不足時に `None` を返すと既存 API を破壊するため、
必ず Stage 2 の bigram top1 を返す（後方互換）。gap 情報は
`find_reference_detailed()` 経由で確認可能。

## API

### `find_reference(question, use_sbert_rerank=None) -> Optional[str]`

後方互換の単純 API。`use_sbert_rerank=False` で Stage 3 を無効化できる。
デフォルト（None）では SBert が利用可能なら自動的に Stage 3 が走る。

### `find_reference_detailed(question) -> Optional[Dict]`

診断 / デバッグ用の拡張 API。選択経路と信頼度情報を返す:

```python
{
    "reference": str,
    "stage": "direct" | "bigram" | "sbert_rerank",
    "confidence": "high" | "ambiguous",
    "bigram_top1_score": float,
    "sbert_top1_score": float | None,
    "sbert_gap": float | None,
}
```

- `stage="direct"`: Stage 1 でヒット
- `stage="bigram"`: Stage 2 単独で確定（候補 1 件 or SBert 利用不可）
- `stage="sbert_rerank" + confidence="high"`: Stage 3 で gap 十分
- `stage="sbert_rerank" + confidence="ambiguous"`: Stage 3 で gap 不足、bigram top1 にフォールバック

## 共有モデルシングルトン

Stage 3 は `cascade_matcher.get_shared_model()` を経由して SBert モデルを
取得する。このシングルトンは `detector.py` の cascade パイプラインとも
共有されるため、プロセス内で SBert モデルは 1 回しかロードされない。

SBert が未導入の環境では `get_shared_model()` が `None` を返し、Stage 3 は
自動的に no-op となる。既存の bigram-only 挙動にフォールバックするため、
インストール要件は `find_reference` 使用者に強制されない。

## パラメータチューニング

| パラメータ | 現在値 | 場所 | 備考 |
|---|---|---|---|
| top_k (候補プール上限) | 5 | `_BIGRAM_CANDIDATE_TOP_K` | 大規模ストア時に増やす余地あり |
| min_score (bigram 閾値) | 0.1 | `_BIGRAM_MIN_JACCARD` | 従来と同一 |
| δ_gap | 0.04 | `_SBERT_GAP_DELTA` | cascade_matcher と同期 |
| high_score | 0.70 | `_SBERT_HIGH_SCORE` | cascade_matcher と同期 |
| relaxed_δ_gap | 0.02 | `_SBERT_RELAXED_GAP` | cascade_matcher と同期 |

cascade_matcher 側の定数と同一値を使っているのは意図的: cascade Tier 3 で
校正済みの閾値をそのまま借用することで、追加のキャリブレーションなしに
堅牢な「自信なし → フォールバック」挙動を得ている。将来的にリファレンス
retrieval 専用のキャリブレーションを行う余地あり。

## 後方互換性

1. `find_reference(question)` の戻り値型・引数は不変
2. 単一候補 / SBert 未導入時の挙動は bigram Jaccard 単段と同一
3. 既存テスト（`tests/test_golden_store.py` の基本 4 件）は変更なしで通過
4. gap 不足時は `None` ではなく bigram top1 にフォールバックするため、
   「何かしら返る」という前提の呼び出し側は壊れない

## テストカバレッジ

`tests/test_golden_store.py` に以下を追加:

| テスト | 検証内容 |
|---|---|
| `test_sbert_rerank_picks_semantic_top1_over_bigram` | bigram top1 ≠ SBert top1 の時 SBert 採用 |
| `test_sbert_rerank_gap_insufficient_falls_back_to_bigram` | gap < δ で bigram top1 フォールバック |
| `test_sbert_rerank_high_score_relaxed_gap` | top1 > 0.70 で緩和 δ=0.02 |
| `test_rerank_disabled_preserves_bigram_behavior` | `use_sbert_rerank=False` で Stage 3 スキップ |
| `test_find_reference_detailed_reports_stage` | stage + confidence の報告 |
| `test_find_reference_detailed_direct_match` | Stage 1 ヒット時の report |
| `test_find_reference_single_candidate_no_rerank_needed` | 候補 1 件で Stage 3 呼び出し回避 |
| `test_bigram_candidates_respects_top_k` | top-K 制限とスコア降順 |

SBert 実モデルを使わず、`monkeypatch` でベクトルを直接注入する fixture
（`scripted_rerank`）で決定論的にロジックを検証している。

## 設計経緯

当初は bigram Jaccard の単段検索のみで、リファレンスが 3 件固定の研究
段階では十分機能していた。将来的にリファレンスが数十〜数百件に増えた
際に bigram のみでは retrieval 品質が劣化するため、SBert 再スコアを
追加した。

MemPalace の調査（2026-04-10 セッション）から以下の設計知見を借用:

1. **Partition → rerank の 2 段構え**: MemPalace の Wings/Rooms メタデータ
   フィルタ (60.9% → 94.8%) に相当する発想。bigram が高速な partition、
   SBert が精密な rerank を担う
2. **gap 条件**: cascade_matcher の gap 条件を借用し、「紛らわしい 1 位」
   の採用を避けて保守的フォールバック
3. **共有 SBert モデル**: detector.py と同じモデルインスタンスを共有し、
   プロセス内の重複ロードを避ける

既存の監査パイプライン精度には影響せず、retrieval 品質の将来スケール
耐性のみを改善する設計（現行 3 エントリでは Stage 1 で解決するため
Stage 3 は通常発動しない）。

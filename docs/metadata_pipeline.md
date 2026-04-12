# メタデータパイプライン設計

metadata_generator / soft_rescue / metadata_policy の設計と
`computed_ai_draft` mode の仕様を記述する。

## 概要

LLM による `question_meta` 動的生成から、AI 草案メタデータの品質管理、
soft-hit rescue までの一連のパイプラインを構成する。

```
detect_missing_metadata()     欠損フィールド検出
        │
        ▼ (auto_generate_meta=true)
generate_meta()               LLM による question_meta 生成
        │
        ▼
_validate_meta()              スキーマ正規化 + メタ記述フィルタ
        │
        ▼
detect → calculate            通常パイプライン (mode=computed_ai_draft)
        │
        ▼
maybe_build_soft_rescue()     C=0 時の部分ヒット回収
        │
        ▼
metadata_policy               昇格判定 (オフライン、将来実装)
```

## computed_ai_draft mode

### 定義

`derive_mode(state, metadata_source=META_SOURCE_LLM)` が返す mode。
`metadata_source == "llm_generated"` かつ `state.C is not None` の場合に設定される。

### VALID_MODES

```python
VALID_MODES = frozenset({"computed", "computed_ai_draft", "degraded"})
```

### 動作上の差異

| 観点 | computed | computed_ai_draft |
|------|----------|-------------------|
| DB 保存 | する | する |
| is_reliable | gate_verdict 依存 | gate_verdict 依存 (同一ロジック) |
| soft_rescue | 発動しない | 条件を満たせば発動 |
| metadata_source | `"inline"` | `"llm_generated"` |

## metadata_generator (`ugh_audit/metadata_generator.py`)

### detect_missing_metadata()

`question_meta` の欠損フィールドを検出する。

```python
def detect_missing_metadata(question_meta: Optional[dict]) -> list[str]
```

**判定ロジック:**

| フィールド | 欠損条件 |
|-----------|---------|
| `core_propositions` | falsy (None / 空リスト / 未設定) |
| `trap_type` | キー未設定 or `None` |

`trap_type=""` は「罠なし」の明示指定であり、欠損とはみなさない。

### build_metadata_request()

LLM に送る生成リクエスト payload を構築する。
欠損フィールドがなければ `None` を返す。

### auto_generate_meta フロー (server.py / mcp_server.py)

1. `detect_missing_metadata()` で欠損チェック
2. 欠損あり + `auto_generate_meta=True` → `generate_meta()` 呼び出し
3. **マージ**: `dict(question_meta)` ベースで欠損フィールドのみ補完
   - truthy ではなく `is not None` で判定 (`trap_type=""` を受容)
   - inline 提供分は一切上書きしない
4. 欠損が実際に埋まった場合のみ `metadata_source = META_SOURCE_LLM`

## soft_rescue (`ugh_audit/soft_rescue.py`)

### 目的

AI 草案メタデータで `C=0` (全命題ミス) になった場合に、
テキスト表層の部分一致で救済候補を探索する。

### ガード条件 (8 個、全て AND)

| # | 条件 | 理由 |
|---|------|------|
| 1 | `mode == "computed_ai_draft"` | AI 草案メタデータのみ対象 |
| 2 | `question_meta is not None` | メタデータ必須 |
| 3 | `C == 0.0` | 全命題ミス時のみ発動 |
| 4 | `S >= 0.85` | 構造的に破綻していない回答のみ |
| 5 | `metadata_confidence >= 0.8` | 高信頼度メタデータのみ |
| 6 | `f2 == 0.0` | 捏造なし |
| 7 | `f3 < 1.0` | 演算子完全無視でない |
| 8 | `core_propositions` が非空 | 命題が存在する |

### スコアリング

- 文トークン (bigram + trigram) を事前計算
- 命題ごとに phrase 分割 → トークンも事前計算
- overlap score = `|sent ∩ prop| / |prop|`
- phrase overlap score = matched phrases / total phrases (閾値 0.25)
- combined = max(overlap, phrase_overlap)
- 最終閾値: `confidence >= 0.08`

### 出力

```python
{
    "type": "ai_draft_c_floor",
    "target_proposition_index": int,
    "target_proposition": str,
    "evidence_span": str,
    "confidence": float,
    "overlap_terms": list[str],    # max 6
    "matched_phrases": list[str],  # max 6
}
```

## _validate_meta のメタ記述フィルタ

`experiments/meta_generator.py` の `_validate_meta()` は
LLM 生成の `disqualifying_shortcuts` からメタ言語的記述を除外する。

### フィルタパターン

```python
_META_DESCRIPTION_RE = re.compile(r'「.+」と|と全否定|のみで答える$')
```

| パターン | 除外例 |
|---------|--------|
| `「.+」と` | 「AIは美を理解できない」と全否定する |
| `と全否定` | 〜と全否定 |
| `のみで答える$` | はい/いいえのみで答える |

`と断言` / `と主張する` は鉤括弧なしの表層フレーズとして有効なため除外しない。

### metadata_confidence の保持

`_validate_meta()` は `metadata_confidence` を `float` に変換して保持する。
文字列表現 (`"0.9"`) も `try/except float()` で受容。

## metadata_policy (`ugh_audit/metadata_policy.py`)

### PromotionPolicy

AI 草案メタデータの昇格判定基準。オフライン/定期処理で使用（パイプライン本体には組み込まない）。

```python
@dataclass(frozen=True)
class PromotionPolicy:
    min_usage_count: int = 3         # 最低使用回数
    min_accepted_count: int = 2      # 最低 accept 回数
    min_confidence: float = 0.7      # 最低信頼度
    max_rejected_count_for_promotion: int = 0  # 昇格時の最大 reject 数
    rejected_count_threshold: int = 3          # reject 閾値
```

### 設定ファイル

`config/metadata_promotion_policy.json` から読み込み。
ファイルが存在しない場合は `DEFAULT_PROMOTION_POLICY` にフォールバック。

## 定数一覧

| 定数 | 値 | 定義場所 |
|------|-----|---------|
| `VALID_MODES` | `{computed, computed_ai_draft, degraded}` | `ugh_calculator.py` |
| `VALID_METADATA_SOURCES` | `{inline, llm_generated, none}` | `ugh_calculator.py` |
| `META_SOURCE_LLM` | `"llm_generated"` | `ugh_calculator.py` |
| `META_SOURCE_INLINE` | `"inline"` | `ugh_calculator.py` |
| `META_SOURCE_NONE` | `"none"` | `ugh_calculator.py` |
| `GATE_FAIL` | `"fail"` | `ugh_calculator.py` |
| `_DQ_NEGATION_CUES` | 否定文脈キュー (15 個) | `detector.py` |
| `_META_DESCRIPTION_RE` | メタ記述フィルタ正規表現 | `experiments/meta_generator.py` |
| `METADATA_GENERATION_SCHEMA_VERSION` | `"1.0.0"` | `ugh_audit/metadata_generator.py` |

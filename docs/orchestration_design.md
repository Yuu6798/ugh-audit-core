# オーケストレーション設計ドキュメント

## 動機

UGH Audit の監査パイプラインは `question_meta`（`core_propositions` 等）を必要とし、
これがないと `verdict="degraded"` を返す。102 問の手動メタでは自由質問に対応できない。

LLM を使って `question_meta` を動的生成することで、任意の質問に対して監査を実行可能にする。

## 設計原則

### 推論ゼロ原則との関係

既存パイプラインの設計原則は「電卓層は推論ゼロ、決定的」。
本オーケストレーションはこの原則と以下のように共存する:

```
[LLM 前処理層]  ← 推論あり（Claude/GPT）— 新規追加
     ↓ question_meta
[検出層]         ← パターンマッチ（cascade の SBert を除く）
[電卓層]         ← 推論ゼロ、決定的
[判定層]         ← 決定的
```

- 電卓層以降は一切変更なし
- LLM はパイプラインの「上流」に位置し、入力データを生成するだけ
- cascade (SBert) と同じ位置づけ — 検出層の上流での推論

### 自作自演の回避

meta 生成と回答生成を同一 LLM が行うと、命題が回答に合わせて甘くなるリスクがある。
これを防ぐため、異なるベンダーに分離する:

| 役割 | 担当 | 理由 |
|------|------|------|
| 出題者（meta 生成） | Claude (Anthropic) | 意味構造の分析が得意 |
| 被監査者（回答生成） | GPT/Codex (OpenAI) | 異なるベンダーで独立性を確保 |
| 審判（監査） | 既存パイプライン | 決定的、どちらの味方もしない |

## 命題改善の品質保証

### 問題: 基準のすり替え

改善ループで命題の意味が変わると「当てやすいように基準を下げた」だけになる。

```
NG: 「安全フレーズの頻出で歪みが増大」→「曖昧な言い換えが歪みパターン」
    （別の命題にすり替え — 基準の放棄）

OK: 「安全フレーズの頻出で歪みが増大」→「安全性フレーズへの語彙集中が歪みを増大させる」
    （同じ意味、照合しやすい表現 — 出題の磨き込み）
```

### 3 層ガード

1. **プロンプト制約**: 改善プロンプトに「意味を変えるな」「hit 命題は変更禁止」を明記
2. **hit 命題保護ガード**: プログラムで hit 命題の改変を検出し、強制復元
3. **refinement_notes**: LLM に変更理由を出力させ、ログに記録

## question_meta スキーマ

`detect()` が消費する最小スキーマ:

```json
{
  "question": "質問テキスト（入力値で固定、LLM 出力で上書きしない）",
  "core_propositions": [
    "15-40文字の断定形。具体的な用語を含む",
    "tfidf バイグラムマッチで照合される"
  ],
  "disqualifying_shortcuts": ["悪い回答に含まれるフレーズ"],
  "acceptable_variants": ["命題の代替表現"],
  "trap_type": "7種のいずれか or 空文字"
}
```

### trap_type の有効値

`registry/premise_frames.yaml` で定義:

| trap_type | 説明 |
|-----------|------|
| `premise_acceptance` | 質問の前提を無批判に受容 |
| `binary_reduction` | 多面的問題の二択矮小化 |
| `scope_deflection` | スコープの逸脱 |
| `metric_omnipotence` | 単一指標万能の誤謬 |
| `authority_appeal` | 権威への訴え |
| `safety_boilerplate` | 安全性フレーズによる空洞化 |
| `relativism_drift` | 相対主義への逃避 |
| `""` | 罠なし |

## フォールバックチェーン

プロジェクト慣習（`try/except ImportError` + グローバルフラグ）に従う:

### meta 生成 (meta_generator.py)
```
Anthropic SDK (claude-sonnet-4-6) → fallback meta (空の core_propositions)
```

### 回答生成 (response_source.py)
```
Codex MCP → OpenAI GPT-4o → Anthropic Claude → 空プレースホルダー
```

## 検証方法

### 102 問比較検証 (validate_against_102.py)

手動キュレーション済み 102 問に対して:
1. Claude で meta を生成
2. 同じ GPT-4o 回答（t=0.0 ベースライン）で audit を実行
3. 手動メタでの結果と比較

### 成功基準

| 指標 | 基準 | n=30 実測 |
|------|------|-----------|
| degraded 排除率 | 100% | **100%** |
| verdict 一致率 | ≥60% | **73.3%** |
| ΔE 相関 (Spearman) | ≥0.4 | **0.7976** |
| C 相関 (Spearman) | ≥0.4 | 0.3717 |

## 今後の改善方向

1. **プロンプト最適化**: few-shot 例を増やし、UGH 固有用語の命題精度を改善
2. **Codex MCP 統合**: `gpt-5.3-codex` アクセス権取得後、GPT-4o を Codex に置換
3. **改善ループの自動化**: CI/CD での定期検証パイプライン構築
4. **n=102 全件検証**: API コスト管理と合わせて実施

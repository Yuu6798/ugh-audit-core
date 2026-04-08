# experiments/ — Claude × GPT/Codex オーケストレーション PoC

## 概要

自由質問に対して LLM で `question_meta` を動的生成し、
既存監査パイプライン（detect → calculate → decide）が有意な verdict を返せるかを検証する PoC。

**既存パイプラインへの変更はゼロ。** `audit()` を import して呼ぶだけ。

## 問題背景

監査パイプラインは `question_meta`（特に `core_propositions`）がないと
`verdict="degraded"` を返す。102問分は手動キュレーション済みだが、
自由質問には監査が働かない — これが致命的ボトルネックだった。

## アーキテクチャ

```
┌─────────────────────────────────────────────────┐
│  Claude (Anthropic API)                          │
│  → question_meta 生成・改善（出題者）              │
└──────────┬──────────────────────────────────────┘
           │ core_propositions, trap_type, ...
           ▼
┌─────────────────────────────────────────────────┐
│  GPT-4o / Codex MCP (OpenAI)                     │
│  → 回答生成・改善（被監査者）                       │
└──────────┬──────────────────────────────────────┘
           │ response_text
           ▼
┌─────────────────────────────────────────────────┐
│  既存パイプライン (audit.py)                       │
│  → detect → calculate → decide（審判、決定的）     │
└──────────┬──────────────────────────────────────┘
           │ verdict, S, C, ΔE
           ▼
       改善ループ（Claude が meta を磨き、GPT が回答を磨く）
```

**自作自演の回避**: meta 生成 (Claude) と回答生成 (GPT/Codex) を異なる LLM ベンダーに分離。

## ファイル構成

| ファイル | 役割 |
|---------|------|
| `meta_generator.py` | Claude API → question_meta 生成 + 改善 |
| `response_source.py` | Codex MCP → GPT-4o → Anthropic のフォールバックチェーン |
| `orchestrator.py` | 統合オーケストレーション + 改善ループ |
| `validate_against_102.py` | 手動メタ vs LLM メタの 102 問比較検証 |
| `prompts/meta_generation_v1.py` | メタ生成プロンプトテンプレート |
| `prompts/meta_improvement_v1.py` | メタ改善プロンプト（意味保持制約付き） |
| `logs/` | JSONL 出力（.gitignore 対象） |

## 使用方法

```bash
# 依存インストール
pip install -e ".[experiment]"

# 環境変数
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...        # GPT-4o 回答生成用

# 単発テスト
python -m experiments.orchestrator \
    --question "AIは本当に創造性を持てるのか？"

# 改善ループ付き（最大3回）
python -m experiments.orchestrator \
    --question "AIは本当に創造性を持てるのか？" \
    --iterate 3

# Codex MCP 不使用（GPT-4o にフォールバック）
python -m experiments.orchestrator \
    --question "..." --iterate 3 --no-codex

# 102問比較検証（最初の10問）
python -m experiments.validate_against_102 --limit 10

# 102問全件検証
python -m experiments.validate_against_102
```

## 回答生成のフォールバックチェーン

1. **Codex MCP** — `codex mcp-server` (stdio transport) で `codex()` ツール呼び出し
2. **OpenAI GPT-4o** — `OPENAI_API_KEY` で Chat Completions API
3. **Anthropic Claude** — 最終フォールバック（自作自演になるため非推奨）
4. **プレースホルダー** — 空文字（オフライン/CI 用）

## 改善ループの設計

```
iteration 0: meta生成 → 回答生成 → 監査
iteration 1+:
  Claude: meta改善（miss命題の表現を調整、意味は保持）
    → 再監査（新metaでhit/missを確定）
  GPT: 回答改善（miss命題をカバーする回答を生成）
    → 最終監査
```

### 品質保証ガード

1. **プロンプト制約**: hit 命題は変更禁止、miss 命題は意味保持の表現調整のみ
2. **hit 命題保護ガード**: LLM が hit 命題を変更した場合は強制復元
3. **refinement_notes**: 変更理由をログに記録（後から人間が監査可能）
4. **収束条件**: accept 到達 / ΔE 改善 < 0.02 / 最大回数

## 検証結果 (n=30)

| 指標 | 基準 | 実測 | 判定 |
|------|------|------|------|
| degraded 排除 | 100% | **100%** | PASS |
| verdict 一致率 | ≥60% | **73.3%** | PASS |
| ΔE 相関 (Spearman) | ≥0.4 | **0.7976** (p<0.001) | PASS |
| C 相関 (Spearman) | ≥0.4 | 0.3717 (p=0.043) | 境界 |
| LLM の偏り | 甘くない | 甘い 3 vs 厳しい 5 | 健全 |

### 改善ループの実例

`grv語彙重力の歪み` 質問:
- iteration 0: verdict=rewrite, C=0.5, ΔE=0.177
- iteration 1: verdict=accept, C=1.0, ΔE=0.094

## 敵対的 meta hack 実験

ミトスレベル AI が meta を逆算して監査を通過できるかの検証。

```bash
python -m experiments.adversarial_meta_hack --limit 30
```

3条件比較: hack（回答を見て meta 逆算）/ honest（回答を見ずに生成）/ hand（手動）

### 結果 (n=30)

- hack で C=1.0: **96.7%** — C 軸は完全に突破される
- hack accept 率: **80%** vs hand: 43%
- **hack C≥0.9 かつ S<0.9 (痕跡検出): 50%**
  - f2（用語捏造）が UGH 固有用語で貫通発火
  - 一般質問では S≈1.0 で検出困難 → grv 実装が必要

詳細: `docs/orchestration_design.md`

## 既知の課題

- **C 相関 0.37**: 命題の立て方が手動と異なるため。改善余地あり
- **UGH 固有用語**: GPT が UGH 用語を知らないため C=0.00 になるケースあり
- **Codex MCP**: `gpt-5.3-codex` モデルのアクセス権が必要（現在 GPT-4o で代替）
- **hack 検出**: 一般質問で S≈1.0 の場合、C 軸の hack を検出できない（grv 未実装）

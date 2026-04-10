# CLAUDE.md — ugh-audit-core

## Project Overview

UGH Audit Core — AI回答の意味論的監査基盤。
UGHer（無意識的重力仮説）の指標 **PoR / ΔE / grv** でAI回答の意味的誠実性を定量評価する。

- **PoR** (Point of Resonance): 問いの核心に対する回答の位置座標 **(S, C)**
  - **S 軸**（構造完全性）: 回答が壊れていないか (0–1)
  - **C 軸**（命題カバレッジ）: 核心を拾っているか (0–1)
- **ΔE** (Delta E): PoR 座標上における理想回答からの距離 (0–1)
- **quality_score**: 品質スコア = 5 - 4 × ΔE (1–5)
- **grv** (Gravity): 回答内の語彙重力分布 — 操作化は未着手（中期タスク）

## Tech Stack

- **Language**: Python 3.10+
- **Build**: setuptools (pyproject.toml)
- **Lint**: ruff (line-length=100, target py310)
- **Test**: pytest
- **CI**: GitHub Actions (Python 3.10/3.11/3.12)
- **DB**: SQLite
- **License**: MIT

## Advisor Strategy（モデル運用方針）

- **メインエージェント**: Opus（設計判断・レビュー・品質ゲート）
- **サブエージェント**: Sonnet 固定（探索・実装・定型タスク）

Agent ツールで spawn する際は必ず `model: "sonnet"` を指定すること。

```python
# 正しい例
Agent({ model: "sonnet", subagent_type: "Explore", prompt: "..." })
Agent({ model: "sonnet", isolation: "worktree", prompt: "..." })

# NG — model 省略すると Opus で動き、コスト効率が下がる
Agent({ subagent_type: "Explore", prompt: "..." })
```

## Session Memory（永続記憶ワークフロー）

セッション間の記憶喪失を防ぐため、`.claude/memory/` にセッションサマリーを蓄積する。

### 起動時ルール

1. セッション開始時に `.claude/memory/_index.md` を読み、過去の決定事項・コンテキストを把握する
2. 直近3件のサマリーファイルは必要に応じて詳細を参照する
3. 過去の設計判断に関する質問には、サマリーを確認してから回答する

### 終了時ルール（自動トリガー）

ユーザーがセッション終了を示す発言をしたら、**確認なしで即座に `/wrap-up` を実行する**。

**トリガーフレーズ**（以下を含む発言を検出）:
- 「終わり」「おわり」「閉じる」「終了」「落ちる」「離れる」
- 「また明日」「また今度」「ありがとう、以上」「お疲れ」
- 「close」「done for today」「that's all」「bye」

**実行内容:**
- 会話の振り返りサマリーを `.claude/memory/YYYY-MM-DD.md` に保存
- `_index.md` に1行サマリーを追記
- CLAUDE.md への更新候補があればユーザーに提案
- `/wrap-up` は手動実行も可能（途中でサマリーを取りたい場合など）

### ディレクトリ構成

```
.claude/
├── settings.json              # フック設定
├── skills/
│   └── wrap-up/
│       └── SKILL.md           # /wrap-up スキル定義
└── memory/
    ├── _index.md              # メタインデックス（全セッション1行要約）
    ├── YYYY-MM-DD.md          # 日次セッションサマリー
    └── archive/               # 月次統合（Phase 2）
```

## Architecture

```
# Audit Engine（構造的意味監査パイプライン）
audit.py              # パイプライン統合 (detect → calculate → decide)
detector.py           # 検出層 — テキスト → Evidence
ugh_calculator.py     # 電卓層 — Evidence → State (S, C, ΔE, quality_score)
decider.py            # 判定層 — State + Evidence → Policy
cascade_matcher.py    # 回収補助 — SBert Tier 2 + 多条件 Tier 3
registry/             # YAML辞書（予約語・演算子・前提フレーム）
opcodes/              # 修復opcode定義

# UGH Audit Layer（REST/MCP サーバー + 永続化）
ugh_audit/
├── collector/        # audit_collector.py — 監査+保存パイプライン
├── storage/          # audit_db.py — SQLite永続化 (audit_runs table)
├── reference/        # golden_store.py — リファレンスセット管理 (JSON)
├── report/           # phase_map.py — テキスト/CSVレポート生成
├── engine/           # Phase 2 エンジン (calculator, decision, runtime, metapatch)
├── server.py         # REST API + MCP 統合サーバー (FastAPI)
└── mcp_server.py     # MCP スタンドアロンサーバー (stateless_http)
tests/                # pytest (フィクスチャはtmp_path, モック不使用)
examples/             # basic_audit.py — E2Eサンプル

# 実験基盤（LLM オーケストレーション PoC）
experiments/
├── meta_generator.py          # Claude API → question_meta 動的生成
├── response_source.py         # Codex MCP / GPT-4o → 回答生成
├── orchestrator.py            # 統合オーケストレーション + 改善ループ
├── validate_against_102.py    # 手動メタ vs LLM メタ比較検証
└── prompts/                   # プロンプトテンプレート
```

### LLM オーケストレーション（自由質問対応 PoC）

`experiments/` は自由質問に対して LLM で `question_meta` を動的生成し、
既存パイプラインが有意な verdict を返せるかを検証する実験基盤。

**既存パイプラインへの変更なし。** `audit()` を import して呼ぶだけ。

- **Claude (Anthropic API)**: question_meta 生成・改善（出題者）
- **GPT-4o / Codex MCP (OpenAI)**: 回答生成・改善（被監査者）
- **既存パイプライン**: 審判（決定的、変更なし）

自作自演を避けるため、meta 生成と回答生成を異なるベンダーに分離。
改善ループで Claude が meta を磨き、GPT が回答を磨く。

**検証結果 (n=102)**: degraded 排除 100%, verdict 一致率 61.8%, ΔE 相関 ρ=0.378 (p<0.001)

手動メタ = 基準値（理想的な命題）、LLM メタ = 実践値（検出パイプラインで機能する命題）として並行運用。

**敵対的 meta hack 実験 (n=30)**: C 軸は突破される (96.7%) が S 軸に 50% の確率で痕跡が残る。
詳細: `docs/orchestration_design.md`

設計詳細: `docs/orchestration_design.md`
使用方法: `experiments/README.md`

### Cascade Matcher（命題回収補助）

`cascade_matcher.py` は命題回収を補助するモジュール。
`detector.py` の `detect()` から呼び出され、Tier 1 (tfidf) で miss した命題に対して SBert ベースの回収を試みる。
SBert 未インストール時は自動的に Tier 1 のみで動作する（フォールバック）。

- **Tier 2**: SBert embedding (paraphrase-multilingual-MiniLM-L12-v2) で response を文分割し、命題との cosine similarity を計算
- **Tier 3**: 多条件 AND フィルタ (c1: tfidf miss確認, c2: embedding閾値, c3: gap閾値(高スコア時緩和あり), c4: f4確定発火のみブロック(< 1.0), c5: response全文でatomic整合)
- 全条件 pass → `Z_RESCUED`、1つでも fail → `miss`
- SBert モデルは初回ロード時にモジュールレベルでキャッシュ

設計詳細: `docs/cascade_design.md`
テストセット: `data/eval/dev_cascade_20.csv` (20命題, 36 atomic units)

#### hit_source フィールド

命題ごとの判定結果に `hit_source` を付与（`Evidence.hit_sources: Dict[int, str]`）:
- `tfidf`: Tier 1 で hit（既存の tfidf バイグラム照合）
- `cascade_rescued`: Tier 1 miss → Tier 2/3 通過で rescue
- `miss`: Tier 1 miss かつ cascade でも rescue されず（または cascade 利用不可）

### Audit Engine（検出層の詳細）

detector.py は4つの検出指標 (f1–f4) + 命題カバレッジで Evidence を生成する。

#### 演算子フレーム検出

`detect_operator()` が命題中の演算子を検出し、`check_propositions()` の回収パスで活用。

```python
from detector import detect_operator, OperatorInfo, OPERATOR_CATALOG

op = detect_operator("低ΔEは良い回答を保証しない")
# OperatorInfo(family='negation', token='しない', position=13)
```

**OPERATOR_CATALOG** — 4族定義:

| 族 | effect | priority | 典型パターン |
|----|--------|----------|-------------|
| negation | polarity_flip | 2 | ではない / 未〜 / 不可能 |
| deontic | normative_flag | 1 | べき / すべきではない |
| skeptical_modality | certainty_downgrade | 3 | かもしれない / とは限らない |
| binary_frame | contrastive_split | 1 | ではなく / 二項対立 |

**共起ルール** (priority で解決):
- deontic + negation → deontic 優先 (「べきではない」は当為表現)
- skeptical + binary_frame → binary_frame 優先

**回収パスの多層ゲート**:
1. `detect_operator(prop)` で演算子検出
2. 概念近傍マーカーチェック (文レベルスコーピング)
3. 緩和閾値: `direct_recall ≥ 0.10`, `full_recall ≥ 0.25`, `overlap ≥ 2`
4. 極性検証 (節レベルスコーピング + 推量表現除外):
   - negation (polarity_flip): 回答の概念近傍に否定形が必要
   - negative deontic (べきではない等): 同上
   - positive deontic (すべき): 回答が否定していたら却下
   - skeptical_modality: 極性チェック不要

### Relaxed Tier1 Safety Valve（緩和閾値による命題回収）

`check_propositions()` の通常閾値 (direct≥0.15, full≥0.30, overlap≥3) で miss した命題に対し、
低い閾値で再判定する安全弁。`detect()` が `relaxed_context` を自動付与。

**昇格条件** (全て AND):
1. 緩和閾値を通過（バイグラム数に応じた段階的閾値）
2. `_relaxed_candidate_allowed`: 内容チャンクの文レベル一致 + 汎用チャンクのみ除外
3. 極性検証 (`needs_polarity_full` / `is_positive_deontic`) を通過
4. `fail_max < 1.0` (構造的欠陥がない)
5. 現状 ΔE ≤ 0.04 かつ relaxed ΔE ≤ 0.04 (既に高品質なケースのみ)

**極性チェックの2層構造**:
- メインhitパス: `needs_polarity_deontic` (deontic否定のみ)
- 演算子回収パス + relaxedパス: `needs_polarity_full` (polarity_flip + deontic)

実験スクリプト: `analysis/threshold_validation/run_proposition_hit_experiment.py`
テスト: `tests/test_relaxed_tier1.py`

### UGH 計算式（ugh_calculator.py — 電卓層）

Audit Engine の電卓層で PoR 座標、ΔE、quality_score を算出する。推論ゼロ、決定的。

注: cascade（SBert）導入により推論ゼロの厳密な定義は再検討中。電卓層自体は推論ゼロだが、上流の検出層で cascade_matcher が SBert embedding を使用する場合がある。

```
PoR = (S, C)

S = 1 - Σ(w_k × f_k) / Σ(w_k)
    w = {f1: 5, f2: 25, f3: 5, f4: 5}    デフォルト Σ(w_k) = 40
    f4=None 時: f4 の重み（5）を除外し Σ(w_k) = 35 で計算

C = hits / n_propositions
    n_propositions=0（未提供）時: C=None（計算不能）

ΔE = (w_s × (1-S)² + w_c × (1-C)²) / (w_s + w_c)
    w_s = 2, w_c = 1
    C=None 時: ΔE=None（算出不可）

quality_score = 5 - 4 × ΔE    # パラメータフリー [1,5]
    ΔE=None 時: quality_score=None
```

**各式の意味:**
- **S**: f1〜f4 の加重平均による構造完全性。f2（用語捏造）に最大重み 25 を配置。f4=None 時は重み除外
- **C**: 命題照合（tfidf + cascade）による核心カバレッジ。命題未提供時は None
- **ΔE**: S と C の加重二乗和。両軸からの距離を1つのスカラーに統合。C=None 時は算出不可
- **quality_score**: ΔE の線形変換。ΔE=0 → 5.0, ΔE=1 → 1.0

### verdict 判定（HA48 検証済み確定値）

| verdict | 条件 | 意味 |
|---------|------|------|
| accept | C≠None AND ΔE ≤ 0.10 | 意味的に十分な回答 |
| rewrite | C≠None AND 0.10 < ΔE ≤ 0.25 | 部分的な修正で改善可能 |
| regenerate | C≠None AND ΔE > 0.25 | 再生成が必要 |
| degraded | C=None OR ΔE=None | メタデータ不足で本計算不能 |

### 検証結果

**HA48 (n=48, v5 ベースライン 197/310 hits, scipy.stats.spearmanr タイ補正あり):**

| 指標 | Spearman ρ | p値 | 備考 |
|------|-----------|-----|------|
| **ΔE vs O (system C)** | **-0.5195** | **0.000154** | **デプロイ可能指標** |
| ΔE vs O (human C) | 0.8616 | <0.001 | 参照上限（ターゲット情報含む） |

**HA20 参考値 (n=20, t=0.0 統一スライス):**

| 指標 | Spearman ρ | p値 | 備考 |
|------|-----------|-----|------|
| ΔE (system C) | -0.7737 | <0.001 | n=20 サブセット |
| ΔE (human C) | -0.9266 | <0.001 | 参照上限 |
| S (構造完全性) | 0.5770 | 0.008 | f2 が主要寄与因子 |

注記:
- system 命題照合の精度改善が ΔE 改善のボトルネック
- 参照上限 ρ=0.857 との差は検出パイプラインの精度改善で縮まる

分析データ: `analysis/pipeline_a_correlation/`, `analysis/verdict_threshold_validation.md`

## Development Setup

```bash
# 基本インストール (テスト + サーバー依存)
pip install -e ".[dev]"

# サーバーデプロイ (REST API + MCP)
pip install -e ".[server]"

# 分析スクリプト (scipy + matplotlib)
pip install -e ".[analysis]"

# 実験基盤 (Claude/GPT オーケストレーション)
pip install -e ".[experiment]"
```

## Commands

```bash
# Lint
ruff check .

# Lint with auto-fix
ruff check --fix .

# Test
pytest -q --tb=short

# Test with coverage
pytest --cov=ugh_audit --tb=short

# Run example
python examples/basic_audit.py
```

## Coding Conventions

### Style

- ruff準拠 (line-length=100)
- 型ヒント必須: `Optional`, `List`, `Dict` を使用
- `from __future__ import annotations` を全モジュール先頭に記述
- docstring / コメントは日本語OK
- float表示は小数点3–4桁に丸める

### Patterns

- **Frozen dataclass**: `Evidence`, `State` は不変
- **フォールバックチェーン**: import時にtry/exceptでフラグ設定、実行時に分岐
- **値のクランプ**: float値は `max(0.0, min(1.0, value))` で [0, 1] に正規化
- **タイムスタンプ**: UTC, ISO 8601形式で保存
- **演算子フレーム検出**: `detect_operator()` → `OperatorInfo(NamedTuple)` で独立関数化
- **概念近傍スコーピング**: マーカーは文レベル、否定は節レベル（逆接接続詞で分割）
- **推量表現除外**: `_SPECULATIVE_EXCLUSIONS` で「かもしれない/かもしれません」を事前除外

### Error Handling

- 明示的な例外送出は避け、フォールバックチェーンで吸収する
- オプショナル依存のimportは `try/except ImportError` + グローバルフラグ
- DB操作はコンテキストマネージャで接続管理

### Testing

- テストファイル: `tests/test_*.py`
- `tmp_path`でファイルシステムを分離
- ヘルパーファクトリでオブジェクト生成 (モック不使用)
- `pytest.approx()` でfloat比較

## File Locations

| ファイル | 用途 | デフォルトパス |
|---------|------|--------------|
| Golden Store | リファレンスセット | `~/.ugh_audit/golden_store.json` |
| Audit DB | 監査ログ | `~/.ugh_audit/audit.db` |
| Meta Cache | LLM 生成メタキャッシュ | `~/.ugh_audit/meta_cache/` |
| HA48 統合 CSV | 48件統合アノテーション | `data/human_annotation_48/annotation_48_merged.csv` |

### audit_runs テーブル追加カラム

| カラム | 型 | 説明 |
|--------|-----|------|
| `metadata_source` | TEXT | `inline` / `llm_generated` / `none` |
| `generated_meta` | TEXT | LLM 生成メタの JSON（llm_generated 時のみ） |
| `hit_sources` | TEXT | 命題ごとの判定結果 JSON（`{"0": "tfidf", "1": "miss"}`） |

### 環境変数

| 変数名 | 説明 | デフォルト |
|--------|------|-----------|
| `UGH_AUDIT_DB` | SQLite DB ファイルパス | `~/.ugh_audit/audit.db` |
| `ANTHROPIC_API_KEY` | Claude API キー（LLM meta 生成 / 実験基盤用） | なし |
| `OPENAI_API_KEY` | OpenAI API キー（GPT/Codex 回答生成用） | なし |
| `UGH_META_CACHE_DIR` | LLM meta キャッシュディレクトリ | `~/.ugh_audit/meta_cache/` |

読み取り専用環境では `UGH_AUDIT_DB=/tmp/audit.db` で書き込み可能パスを指定する。
`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` は `experiments/` の実行時のみ必要。

## Key Thresholds

| 定数 | 値 | 場所 |
|------|-----|------|
| S: WEIGHTS_F | f1=5, f2=25, f3=5, f4=5 (f4=None時は重み除外, ÷35) | `ugh_calculator.py` |
| ΔE: WEIGHT_S | 2 | `ugh_calculator.py` |
| ΔE: WEIGHT_C | 1 | `ugh_calculator.py` |
| quality_score | 5 - 4 × ΔE | `ugh_calculator.py` |
| verdict: accept | C≠None AND ΔE ≤ 0.10 | `server.py` / `mcp_server.py` |
| verdict: rewrite | C≠None AND 0.10 < ΔE ≤ 0.25 | 同上 |
| verdict: regenerate | C≠None AND ΔE > 0.25 | 同上 |
| verdict: degraded | C=None OR ΔE=None | 同上 |
| Bigram Jaccard閾値 | ≥ 0.10 | `golden_store.py` |
| 命題マッチ: direct_recall | ≥ 0.15 | `detector.py` |
| 命題マッチ: full_recall | ≥ 0.30 | `detector.py` |
| 命題マッチ: min_overlap | ≥ 3 | `detector.py` |
| 演算子回収: direct_recall | ≥ 0.10 | `detector.py` |
| 演算子回収: full_recall | ≥ 0.25 | `detector.py` |
| 演算子回収: min_overlap | ≥ 2 | `detector.py` |
| cascade: θ_sbert | 0.50 | `cascade_matcher.py` |
| cascade: SBert モデル | paraphrase-multilingual-MiniLM-L12-v2 | `cascade_matcher.py` |
| cascade: δ_gap | 0.04 (score > 0.70 時 0.02) | `cascade_matcher.py` |
| cascade: c4 閾値 | f4_flag < 1.0 (f4=None時はrescue全体をスキップ) | `cascade_matcher.py` |
| fail_max (f4=None) | 1.0 (fail-closed: relaxed promotion をブロック) | `detector.py` |
| relaxed: ΔE上限 | ≤ 0.04 (current + relaxed) | `detector.py` |
| relaxed: 大命題 (≥8bg) | direct≥0.10, full≥0.30, overlap≥2 | `detector.py` |
| relaxed: 中命題 (≥5bg) | direct≥0.12, full≥0.30, overlap≥2 | `detector.py` |

## Baseline & Validation

### 命題ヒット率ベースライン

102問 × 310命題の全件リラン結果:

| hit_source | 件数 | 割合 |
|-----------|------|------|
| tfidf | 184 | 59.4% |
| cascade_rescued | 5 | 1.6% |
| miss | 121 | 39.0% |
| **合計** | **310** | — |
| **命題ヒット率** | **189/310** | **61.0%** |

ベースライン CSV: `data/eval/audit_102_main_baseline_cascade.csv`

### HA48 統合アノテーション

HA20 (20件) + HA28 (28件) を統一スキーマで結合した 48件データセット。

- **スキーマ**: `id, category, S, C, O, propositions_hit, notes`
- **S/C**: 全48件入力済み（HA20 は annotation_spec_v2 遡及テーブルから取得）
- **O**: HA20 は human_score (1-5)、HA28 は O (1-4)
- **統合 CSV**: `data/human_annotation_48/annotation_48_merged.csv`
- **生成スクリプト**: `scripts/merge_annotations_48.py`

### HA48 検証結果

| 指標 | 値 | 説明 |
|------|-----|------|
| Spearman ρ (ΔE vs O, system C) | -0.5195 (p=0.000154) | デプロイ可能指標 (scipy, タイ補正あり) |
| Spearman ρ (ΔE vs O, human C) | 0.8616 (p<0.001) | 参照上限 (scipy, タイ補正あり) |
| v5 ベースライン | 197/310 hits, cascade rescued 11 | audit_102_main_baseline_v5.csv |
| verdict 単調性 | accept(3.44) > rewrite(2.62) > regenerate(1.00) | HA48 検証済み |

分析データ: `analysis/verdict_threshold_validation.md`, `analysis/pipeline_a_correlation/`

## Public API

```python
from ugh_audit import (
    AuditDB,          # SQLite保存
    AuditCollector,   # パイプライン (audit + save)
    SessionCollector,  # セッション単位収集
    GoldenStore,      # リファレンス管理
    GoldenEntry,      # リファレンスエントリ
    generate_text_report,  # テキストレポート
    generate_csv,     # CSVエクスポート
)
```

## Git Workflow

### Branches

- `main` — 安定版。直接pushしない
- `claude/*` — 作業ブランチ

### Commit Messages

- Conventional Commits形式: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`
- 日本語メッセージ可

### Pull Request

PRはリンク発行で作成する。`gh pr create` は使わない。

```bash
# 1. ブランチをpush
git push -u origin <branch-name>

# 2. PRリンクを生成して提示
# フォーマット:
# https://github.com/Yuu6798/ugh-audit-core/compare/main...<branch-name>?expand=1
```

PR作成時は上記のcompareリンクをユーザーに提示し、ユーザー自身がGitHub上でPRを作成する。

## Server

### 起動方法

```bash
# REST API + MCP 統合サーバー
uvicorn ugh_audit.server:app --host 0.0.0.0 --port 8000

# MCP スタンドアロン
python -m ugh_audit.mcp_server --port 8000
```

### エンドポイント

| メソッド | パス | 説明 |
|---------|------|------|
| POST | `/api/audit` | AI回答を意味監査 |
| GET | `/api/history` | 直近の監査履歴 |
| POST | `/mcp` | MCP Streamable HTTP |
| GET | `/health` | ヘルスチェック (`{"status": "ok"}`) |

### API 出力フォーマット (schema_version: 2.0.0)

**computed モード（本計算完了時）:**
```json
{
  "schema_version": "2.0.0",
  "S": 0.6875,
  "C": 0.5,
  "delta_e": 0.1484,
  "quality_score": 4.4062,
  "verdict": "rewrite",
  "hit_rate": "1/2",
  "structural_gate": {
    "f1": 0.0, "f2": 0.5, "f3": 0.0, "f4": 0.0,
    "gate_verdict": "warn",
    "primary_fail": "f2"
  },
  "saved_id": 1,
  "mode": "computed",
  "matched_id": "q001",
  "metadata_source": "inline",
  "computed_components": ["C", "S", "delta_e", "f1", "f2", "f3", "f4", "quality_score"],
  "missing_components": [],
  "errors": [],
  "degraded_reason": []
}
```

**degraded モード（メタデータ不足時）:**
```json
{
  "schema_version": "2.0.0",
  "S": 1.0,
  "C": null,
  "delta_e": null,
  "quality_score": null,
  "verdict": "degraded",
  "hit_rate": null,
  "structural_gate": {
    "f1": 0.0, "f2": 0.0, "f3": 0.0, "f4": null,
    "gate_verdict": "incomplete",
    "primary_fail": "none"
  },
  "saved_id": null,
  "mode": "degraded",
  "matched_id": null,
  "metadata_source": "none",
  "computed_components": ["S"],
  "missing_components": ["C", "delta_e", "f1", "f2", "f3", "f4", "quality_score"],
  "errors": ["question_meta_missing", "detection_skipped"],
  "degraded_reason": ["question_meta_missing", "detection_skipped"]
}
```

**gate_verdict の値:**

| gate_verdict | 条件 | 意味 |
|---|---|---|
| pass | fail_max == 0.0 AND f4 ≠ None | 構造完全 |
| warn | 0.0 < fail_max < 1.0 AND f4 ≠ None | 部分的な構造欠陥 |
| fail | fail_max ≥ 1.0 | 構造的に破綻 |
| incomplete | f4 == None | f4 未計算 |

**DB 保存ポリシー:** degraded 結果は DB に保存しない（saved_id=null）。ベースライン汚染を防止。

### 設計方針

- MCP は `stateless_http=True` で動作（マルチワーカー/LB対応）
- `session_id` を REST/MCP 両方でオプショナルに受け付け、会話単位の分析に対応

## CI Pipeline

GitHub Actions (`.github/workflows/ci.yml`):

1. Python 3.10 / 3.11 / 3.12 マトリクス
2. `pip install -e ".[dev]"` (dev extra にサーバー依存を含む)
3. `ruff check .` — lint
4. `pytest -q --tb=short` — test (REST/MCP テスト含む)

CI通過 = lint clean + 全テストpass (275 collected, 2 skipped: cascade SBert 未インストール)

## Important Notes

- `.gitignore`に `*.db`, `*.sqlite`, `.env` が含まれる — DB・環境ファイルをコミットしない
- GoldenStoreは初期データ3件をハードコード (ugh_definition, por_definition, delta_e_definition)
- テスト時は `_empty_store()` ヘルパーで初期データをクリアして分離する
- 演算子フレーム検出テストは `tests/test_operator_frame.py` (32件)
- `_SYNONYM_MAP` (110エントリ) と `OPERATOR_CATALOG` (4族) は detector.py 内の dict リテラルで管理
- cascade テスト (`tests/test_cascade_tier2.py`, `tests/test_cascade_tier3.py`) は SBert 未インストール環境で自動 skip
- 否定極性マーカー (`_NEGATION_POLARITY_FORMS`) は全トークン2文字以上の具体形で定義（bare 1文字トークン禁止）
- quality_score テストは `tests/test_pipeline_a.py` (15件: 計算検証, verdict閾値, API出力フォーマット)
- verdict 判定は全系統 HA48 確定値 (ΔE ≤ 0.10 / 0.25) に統一済み。旧 bin ベース (0.02/0.12/0.35) は廃止
- `engine/runtime.py` の `_POR_FIRE_THRESHOLD` / `_LEGACY_BIN*` はレガシー互換層。メインパイプラインでは未使用
- 敵対的 meta hack 実験スクリプト: `experiments/adversarial_meta_hack.py`

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

計算式の詳細: [`docs/formulas.md`](docs/formulas.md)
検証結果: [`docs/validation.md`](docs/validation.md)

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

# NG — model 省略すると Opus で動き、コスト効率が下がる
Agent({ subagent_type: "Explore", prompt: "..." })
```

## Session Memory（永続記憶ワークフロー）

セッション間の記憶喪失を防ぐため、`.claude/memory/` にセッションサマリーを蓄積する。

### 起動時ルール

1. セッション開始時に `.claude/memory/_index.md` を読み、過去の決定事項・コンテキストを把握する
2. 直近 3 件のサマリーファイルは必要に応じて詳細を参照する
3. 過去の設計判断に関する質問には、サマリーを確認してから回答する

### 終了時ルール（自動トリガー）

ユーザーがセッション終了を示す発言をしたら、**確認なしで即座に `/wrap-up` を実行する**。

**トリガーフレーズ**（文脈付きの終了意図を検出。汎用トークン単体では発火しない）:
- 「今日はここまで」「今日は終わり」「今日はおわり」
- 「セッション終了」「セッション閉じて」
- 「また明日」「また今度」「お疲れ様」「お疲れさま」
- 「done for today」「that's all」
- 手動: `/wrap-up`

**実行内容:**
- 会話の振り返りサマリーを `.claude/memory/YYYY-MM-DD.md` に保存
- `_index.md` に 1 行サマリーを追記
- CLAUDE.md への更新候補があればユーザーに提案

## Architecture

```
# Audit Engine（構造的意味監査パイプライン）
audit.py              # パイプライン統合 (detect → calculate → decide)
detector.py           # 検出層 — テキスト → Evidence
ugh_calculator.py     # 電卓層 — Evidence → State (S, C, ΔE, quality_score)
decider.py            # 判定層 — State + Evidence → Policy
cascade_matcher.py    # 回収補助 — SBert Tier 2 + 多条件 Tier 3
semantic_loss.py      # 意味損失関数 L_sem — 診断用分解指標
registry/             # YAML辞書（予約語・演算子・前提フレーム）
opcodes/              # 修復opcode定義

# UGH Audit Layer（REST/MCP サーバー + 永続化）
ugh_audit/
├── collector/        # 監査+保存パイプライン
├── storage/          # SQLite永続化
├── reference/        # リファレンスセット管理
├── report/           # テキスト/CSVレポート生成
├── engine/           # Phase 2 エンジン (calculator, decision, runtime, metapatch)
├── server.py         # REST API + MCP 統合サーバー
└── mcp_server.py     # MCP スタンドアロンサーバー

# 実験基盤
experiments/          # LLM オーケストレーション PoC (Claude×GPT)
tests/                # pytest (fixture は tmp_path, モック不使用)
examples/             # basic_audit.py — E2E サンプル
docs/                 # 設計ドキュメント
analysis/             # 検証・分析スクリプト + 成果物
```

### コンポーネント別設計ドキュメント

| コンポーネント | ドキュメント |
|---|---|
| 検出層 (演算子フレーム + Relaxed Tier1) | [`docs/detector_design.md`](docs/detector_design.md) |
| Cascade Matcher (SBert Tier 2/3) | [`docs/cascade_design.md`](docs/cascade_design.md) |
| 計算式 (PoR / ΔE / verdict / gate) | [`docs/formulas.md`](docs/formulas.md) |
| 意味損失関数 L_sem | [`docs/semantic_loss.md`](docs/semantic_loss.md) |
| LLM オーケストレーション | [`docs/orchestration_design.md`](docs/orchestration_design.md) |
| REST API + MCP サーバー | [`docs/server_api.md`](docs/server_api.md) |
| 検証結果 (HA48 / HA20 / baseline) | [`docs/validation.md`](docs/validation.md) |

### ドキュメント管理ポリシー

**CLAUDE.md はリポジトリ横断の普遍的内容のみ記述する (目標: 300 行以内)。**

新機能・新仕様を追加する際のドキュメント作成ルール:

1. **機能・仕様の詳細は `docs/<topic>.md` を新規作成して記述する**
   - 設計思想、計算式、パラメータ、検証結果、使用例など
   - CLAUDE.md に詳細を追加してはならない
2. **CLAUDE.md への追記は最小限に留める**
   - Architecture ツリーに 1 行（ファイル配置の記載）
   - 上記「コンポーネント別設計ドキュメント」索引表に 1 行（新 doc へのリンク）
   - それ以外の詳細は追加しない
3. **既存の task-specific 内容を見つけたら対応する `docs/` に移管する**
   - CLAUDE.md が肥大化していないか定期的に精査する

**判断基準**:
- **普遍的 (CLAUDE.md に残す)**: 開発環境、コーディング規約、git workflow、コア定数、
  ファイル配置の一覧、ドキュメント索引 — どの作業者・どの機能でも参照する内容
- **task-specific (`docs/` に分離)**: 1 コンポーネントの実装詳細、1 指標の校正結果、
  1 機能の API スキーマ、1 実験の検証データ — 特定タスクの深掘り情報

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

- ruff 準拠 (line-length=100)
- 型ヒント必須: `Optional`, `List`, `Dict` を使用
- `from __future__ import annotations` を全モジュール先頭に記述
- docstring / コメントは日本語 OK
- float 表示は小数点 3–4 桁に丸める

### Patterns

- **Frozen dataclass**: `Evidence`, `State` は不変
- **フォールバックチェーン**: import 時に try/except でフラグ設定、実行時に分岐
- **値のクランプ**: float 値は `max(0.0, min(1.0, value))` で [0, 1] に正規化
- **タイムスタンプ**: UTC, ISO 8601 形式で保存
- **概念近傍スコーピング**: マーカーは文レベル、否定は節レベル（逆接接続詞で分割）

### Error Handling

- 明示的な例外送出は避け、フォールバックチェーンで吸収する
- オプショナル依存の import は `try/except ModuleNotFoundError` でモジュール名を
  確認してからフラグ設定（transitive 依存エラーは fail-fast）
- DB 操作はコンテキストマネージャで接続管理

### Testing

- テストファイル: `tests/test_*.py`
- `tmp_path` でファイルシステムを分離
- ヘルパーファクトリでオブジェクト生成 (モック不使用)
- `pytest.approx()` で float 比較

## File Locations

| ファイル | 用途 | デフォルトパス |
|---------|------|--------------|
| Golden Store | リファレンスセット | `~/.ugh_audit/golden_store.json` |
| Audit DB | 監査ログ | `~/.ugh_audit/audit.db` |
| Meta Cache | LLM 生成メタキャッシュ | `~/.ugh_audit/meta_cache/` |
| HA48 統合 CSV | 48 件統合アノテーション | `data/human_annotation_48/annotation_48_merged.csv` |

### 環境変数

| 変数名 | 説明 | デフォルト |
|--------|------|-----------|
| `UGH_AUDIT_DB` | SQLite DB ファイルパス | `~/.ugh_audit/audit.db` |
| `ANTHROPIC_API_KEY` | Claude API キー（LLM meta 生成 / 実験基盤用） | なし |
| `OPENAI_API_KEY` | OpenAI API キー（GPT/Codex 回答生成用） | なし |
| `UGH_META_CACHE_DIR` | LLM meta キャッシュディレクトリ | `~/.ugh_audit/meta_cache/` |

読み取り専用環境では `UGH_AUDIT_DB=/tmp/audit.db` で書き込み可能パスを指定する。
`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` は `experiments/` の実行時のみ必要。

## Key Thresholds（コア定数）

| 定数 | 値 | 場所 |
|------|-----|------|
| S: WEIGHTS_F | f1=5, f2=25, f3=5, f4=5 (f4=None時は除外, ÷35) | `ugh_calculator.py` |
| ΔE: WEIGHT_S | 2 | `ugh_calculator.py` |
| ΔE: WEIGHT_C | 1 | `ugh_calculator.py` |
| quality_score | 5 - 4 × ΔE | `ugh_calculator.py` |
| verdict: accept | C≠None AND ΔE ≤ 0.10 | `server.py` / `mcp_server.py` |
| verdict: rewrite | C≠None AND 0.10 < ΔE ≤ 0.25 | 同上 |
| verdict: regenerate | C≠None AND ΔE > 0.25 | 同上 |
| verdict: degraded | C=None OR ΔE=None | 同上 |

実装詳細・チューニング閾値は各コンポーネントドキュメントを参照。

## Public API

```python
from ugh_audit import (
    AuditDB,          # SQLite保存
    AuditCollector,   # パイプライン (audit + save)
    SessionCollector, # セッション単位収集
    GoldenStore,      # リファレンス管理
    GoldenEntry,      # リファレンスエントリ
    generate_text_report,  # テキストレポート
    generate_csv,     # CSVエクスポート
)
```

## Git Workflow

### Branches

- `main` — 安定版。直接 push しない（例外: `.claude/memory/` の運用ログは直接 commit 可）
- `claude/*` — 作業ブランチ

### Commit Messages

- Conventional Commits 形式: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`
- 日本語メッセージ可

### Pull Request

PR はリンク発行で作成する。`gh pr create` は使わない。

```bash
# 1. ブランチを push
git push -u origin <branch-name>

# 2. PR リンクを提示
# https://github.com/Yuu6798/ugh-audit-core/compare/main...<branch-name>?expand=1
```

## CI Pipeline

GitHub Actions (`.github/workflows/ci.yml`):

1. Python 3.10 / 3.11 / 3.12 マトリクス
2. `pip install -e ".[dev]"` (dev extra にサーバー依存を含む)
3. `ruff check .` — lint
4. `pytest -q --tb=short` — test (REST/MCP テスト含む)

CI 通過 = lint clean + 全テスト pass

## Important Notes

- `.gitignore` に `*.db`, `*.sqlite`, `.env` が含まれる — DB・環境ファイルをコミットしない
- `GoldenStore` は初期データ 3 件をハードコード (ugh_definition, por_definition, delta_e_definition)
- テスト時は `_empty_store()` ヘルパーで初期データをクリアして分離する
- cascade テスト (`tests/test_cascade_tier*.py`) は SBert 未インストール環境で自動 skip
- 否定極性マーカー (`_NEGATION_POLARITY_FORMS`) は全トークン 2 文字以上の具体形で定義（bare 1 文字トークン禁止）
- verdict 判定は全系統 HA48 確定値 (ΔE ≤ 0.10 / 0.25) に統一済み。旧 bin ベース (0.02/0.12/0.35) は廃止
- `engine/runtime.py` の `_POR_FIRE_THRESHOLD` / `_LEGACY_BIN*` はレガシー互換層。メインパイプラインでは未使用

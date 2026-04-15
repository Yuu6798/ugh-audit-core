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

## 出力の自己監査原則（Self-Audit Principle）

本リポジトリは AI 回答の意味的誠実性を **PoR / ΔE / L_sem** で定量評価する
基盤である。**Claude 自身の出力もこの指標で自己監査すること**。
このリポジトリが測ろうとしているものに、自分の発話が抵触していないかを
常に確認する。迷ったら「今の出力を `audit.py` に通したらどう判定されるか」
を考える。

### 指標と出力挙動の対応

| 指標 | Claude 出力での意味 |
|---|---|
| **C 軸**（命題カバレッジ） | 次の判断に必要な情報を含んでいるか |
| **f2**（未知性） | 知っていることと推測を明確に区別しているか |
| **f4**（前提受容） | ユーザー発言やレビューコメントを無批判に受け入れていないか |
| **L_Q**（演算子ロス） | 評価語・お世辞で命題の極性を偽装していないか |
| **L_F**（未知性ロス） | 情報量ゼロの banner・感想段落を挟んでいないか |

### 書くべきもの（C 軸に貢献）

- 設計判断の根拠、採用しなかった選択肢とその理由
- 将来のリスク・次の意思決定ポイント
- 既存仮定との矛盾の発見（例: `invalidate` と `merge` のセマンティクス衝突）
- 次に同じコードを触る人がミスを避けるための文脈

### 書かないもの（ノイズ）

- 事実の感情的な再記述（「的確な指摘でした」「非常に勉強になります」）
- 累計報告・過去会話の要約（ログを見れば済む）
- 体裁だけの banner セクション（「所感」「観察」）
- 評価語だけで情報量ゼロの段落
- 自動挙動の再宣言（「次の CI 結果を待ちます」等）

### 判断基準

自分の出力を含意レベルで分解し、**既存命題を言い換えているだけの文は
削る**。新しい含意を追加する文だけ残す。これは L_sem で言えば
`L_Q` / `L_F` を最小化する操作に対応する。

この原則は、本リポジトリが測る「意味的誠実性」を Claude 自身の出力にも
適用するという単純な一貫性要請に由来する。

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
grv_calculator.py     # 因果構造損失 grv — 3項式 (drift/dispersion/collapse)
mode_signal.py        # 応答モード適合度信号 response_mode_signal — cue-list ベース
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
├── metadata_generator.py  # メタデータ欠損検出 + LLM 生成リクエスト構築
├── metadata_policy.py     # AI草案メタデータの昇格ポリシー
├── soft_rescue.py    # AI草案メタデータ向け soft-hit rescue (C=0 部分回収)
├── server.py         # REST API + MCP 統合サーバー
└── mcp_server.py     # MCP スタンドアロンサーバー

# 実験基盤
experiments/          # LLM オーケストレーション PoC (Claude×GPT)
tests/                # pytest (fixture は tmp_path, モック不使用)
examples/             # basic_audit.py — E2E サンプル
docs/                 # 設計ドキュメント
analysis/             # 検証・分析スクリプト + 成果物
                      #   self_audit_session.py        — Self-Audit 実験の proxy metric
                      #   extract_claude_transcript.py — Claude Code jsonl → transcript

# デプロイ
Dockerfile            # Railway デプロイ用 (python:3.11-slim + PyTorch CPU)
.dockerignore         # イメージ軽量化
railway.toml          # Railway 設定 (ヘルスチェック)
```

### 公開URL

```
https://ugh-audit-core-production.up.railway.app
```

Railway にデプロイ済み。main push で自動デプロイ。永続ボリューム `/data` で DB 保持。
詳細: [`docs/server_api.md`](docs/server_api.md)

### コンポーネント別設計ドキュメント

| コンポーネント | ドキュメント |
|---|---|
| 検出層 (演算子フレーム + Relaxed Tier1) | [`docs/detector_design.md`](docs/detector_design.md) |
| Cascade Matcher (SBert Tier 2/3 + 永続キャッシュ) | [`docs/cascade_design.md`](docs/cascade_design.md) |
| GoldenStore リファレンス検索 (bigram + SBert rerank) | [`docs/golden_store.md`](docs/golden_store.md) |
| 計算式 (PoR / ΔE / verdict / gate) | [`docs/formulas.md`](docs/formulas.md) |
| 意味損失関数 L_sem | [`docs/semantic_loss.md`](docs/semantic_loss.md) |
| LLM オーケストレーション | [`docs/orchestration_design.md`](docs/orchestration_design.md) |
| REST API + MCP サーバー | [`docs/server_api.md`](docs/server_api.md) |
| 検証結果 (HA48 / HA20 / baseline) | [`docs/validation.md`](docs/validation.md) |
| Self-Audit 実験 (Claude 出力の proxy 測定) | [`docs/self_audit_experiment.md`](docs/self_audit_experiment.md) |
| メタデータパイプライン (metadata_generator / soft_rescue / computed_ai_draft) | [`docs/metadata_pipeline.md`](docs/metadata_pipeline.md) |
| grv 因果構造損失 (drift / dispersion / collapse) | [`docs/grv_design.md`](docs/grv_design.md) |
| mode_affordance / response_mode_signal | [`docs/mode_affordance.md`](docs/mode_affordance.md), [`addendum`](docs/mode_affordance_v1_addendum.md) |

### ドキュメント管理ポリシー

**CLAUDE.md はリポジトリ横断の普遍的内容のみ記述する (目標: 400 行以内)。**

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
| Embedding Cache | SBert 永続埋め込み (`.npz`) | `~/.ugh_audit/embedding_cache.npz` |
| HA48 統合 CSV | 48 件統合アノテーション | `data/human_annotation_48/annotation_48_merged.csv` |

### 環境変数

| 変数名 | 説明 | デフォルト |
|--------|------|-----------|
| `UGH_AUDIT_DB` | SQLite DB ファイルパス | `~/.ugh_audit/audit.db` |
| `ANTHROPIC_API_KEY` | Claude API キー（LLM meta 生成 / 実験基盤用） | なし |
| `OPENAI_API_KEY` | OpenAI API キー（GPT/Codex 回答生成用） | なし |
| `UGH_META_CACHE_DIR` | LLM meta キャッシュディレクトリ | `~/.ugh_audit/meta_cache/` |
| `UGH_AUDIT_CACHE_DIR` | 埋め込みキャッシュディレクトリ | `~/.ugh_audit/` |
| `UGH_AUDIT_EMBED_CACHE_DISABLE` | `1/true/yes` で埋め込みキャッシュ無効化 | 無効化しない |
| `UGH_AUDIT_EMBED_CACHE_MAX` | 埋め込みキャッシュのエントリ上限 (hard cap) | 10000 |

読み取り専用環境では `UGH_AUDIT_DB=/tmp/audit.db` / `UGH_AUDIT_CACHE_DIR=/tmp/ugh_cache`
で書き込み可能パスを指定する。
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
| is_reliable | mode∈{computed, computed_ai_draft} AND verdict∈{accept,rewrite,regenerate} AND gate_verdict≠fail | `server.py` / `mcp_server.py` |
| VALID_MODES | `{computed, computed_ai_draft, degraded}` | `ugh_calculator.py` |

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

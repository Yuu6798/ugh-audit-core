# CLAUDE.md — ugh-audit-core

## Project Overview

UGH Audit Core — AI回答の意味論的監査基盤。
UGHer（無意識的重力仮説）の3指標 **PoR / ΔE / grv** でAI回答の意味的誠実性を定量評価する。

- **PoR** (Point of Resonance): 質問↔回答の意味的共鳴度 (0–1)
- **ΔE** (Delta E): 期待回答↔実回答の意味ズレ量 (0–1)
- **grv** (Gravity): 回答内の語彙重力分布 (dict)

## Tech Stack

- **Language**: Python 3.10+
- **Build**: setuptools (pyproject.toml)
- **Lint**: ruff (line-length=100, target py310)
- **Test**: pytest
- **CI**: GitHub Actions (Python 3.10/3.11/3.12)
- **DB**: SQLite (JSON列あり)
- **License**: MIT

## Architecture

```
ugh_audit/
├── scorer/           # UGH指標スコアリング
│   ├── models.py     # AuditResult (frozen dataclass)
│   └── ugh_scorer.py # 3層フォールバック: ugh3 → sentence-transformers → minimal
├── collector/        # audit_collector.py — スコアリング+保存パイプライン
├── storage/          # audit_db.py — SQLite永続化 (audit_runs table)
├── reference/        # golden_store.py — リファレンスセット管理 (JSON)
├── report/           # phase_map.py — テキスト/CSVレポート生成
├── server.py         # REST API + MCP 統合サーバー (FastAPI)
└── mcp_server.py     # MCP スタンドアロンサーバー (stateless_http)
tests/                # pytest (フィクスチャはtmp_path, モック不使用)
examples/             # basic_audit.py — E2Eサンプル
```

### Scorer Fallback Chain

UGHScorerは3層フォールバックで動作する:

1. **ugh3-metrics-lib** — フル精度 (要 `pip install -e ".[ugh3]"`)
2. **sentence-transformers** — 埋め込みベース近似 (要 `pip install -e ".[full]"`)
3. **minimal stub** — ゼロ値返却 (依存なし、テスト用)

## Development Setup

```bash
# 基本インストール (minimal backend + テスト + サーバー依存)
pip install -e ".[dev]"

# フル機能 (sentence-transformers + 日本語形態素解析)
pip install -e ".[full]"

# サーバーデプロイ (REST API + MCP + スコアリングバックエンド)
pip install -e ".[server]"

# ugh3バックエンド
pip install -e ".[ugh3]"
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

- **Frozen dataclass**: `AuditResult`は不変。computedフィールドはpropertyで実装
- **フォールバックチェーン**: import時にtry/exceptでフラグ設定、実行時に分岐
- **値のクランプ**: float値は `max(0.0, min(1.0, value))` で [0, 1] に正規化
- **タイムスタンプ**: UTC, ISO 8601形式で保存
- **JSON永続化**: `ensure_ascii=False` (日本語対応)
- **ΔE閾値**: ≤0.04 同一意味圏 / ≤0.10 軽微なズレ / >0.10 意味乖離

### Error Handling

- 明示的な例外送出は避け、フォールバックチェーンで吸収する
- オプショナル依存のimportは `try/except ImportError` + グローバルフラグ
- DB操作はコンテキストマネージャで接続管理

### Testing

- テストファイル: `tests/test_*.py`
- `tmp_path`でファイルシステムを分離
- ヘルパーファクトリでオブジェクト生成 (モック不使用)
- `pytest.approx()` でfloat比較
- minimal backendでCIテストが通ること

## File Locations

| ファイル | 用途 | デフォルトパス |
|---------|------|--------------|
| Golden Store | リファレンスセット | `~/.ugh_audit/golden_store.json` |
| Audit DB | 監査ログ | `~/.ugh_audit/audit.db` |

### 環境変数

| 変数名 | 説明 | デフォルト |
|--------|------|-----------|
| `UGH_AUDIT_DB` | SQLite DB ファイルパス | `~/.ugh_audit/audit.db` |

読み取り専用環境では `UGH_AUDIT_DB=/tmp/audit.db` で書き込み可能パスを指定する。

## Key Thresholds

| 定数 | 値 | 場所 |
|------|-----|------|
| `POR_FIRE_THRESHOLD` | 0.82 | `ugh_scorer.py` |
| ΔE 同一意味圏 | ≤ 0.04 | `models.py` |
| ΔE 軽微なズレ | ≤ 0.10 | `models.py` |
| Bigram Jaccard閾値 | ≥ 0.10 | `golden_store.py` |

## Public API

```python
from ugh_audit import (
    UGHScorer,        # スコアリング
    AuditResult,      # 結果データクラス
    AuditDB,          # SQLite保存
    AuditCollector,   # パイプライン (score + save)
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

### 設計方針

- MCP は `stateless_http=True` で動作（マルチワーカー/LB対応）
- `session_id` を REST/MCP 両方でオプショナルに受け付け、会話単位の分析に対応
- sentence-transformers モデルロード失敗時は `logging.warning` で通知

## CI Pipeline

GitHub Actions (`.github/workflows/ci.yml`):

1. Python 3.10 / 3.11 / 3.12 マトリクス
2. `pip install -e ".[dev]"` (dev extra にサーバー依存を含む)
3. `ruff check .` — lint
4. `pytest -q --tb=short` — test (REST/MCP テスト含む)

CI通過 = lint clean + 全テストpass

## Important Notes

- `ugh3-metrics-lib`は外部Git依存。CIではminimal backendで動作
- `.gitignore`に `*.db`, `*.sqlite`, `.env` が含まれる — DB・環境ファイルをコミットしない
- GoldenStoreは初期データ3件をハードコード (ugh_definition, por_definition, delta_e_definition)
- テスト時は `_empty_store()` ヘルパーで初期データをクリアして分離する
- `grv` はDBにJSON文字列として保存 (`json.dumps`)
- `por_fired` はSQLiteにINTEGER (0/1) で保存

# CLAUDE.md — ugh-audit-core

## Project Overview

UGH Audit Core — AI回答の意味論的監査基盤。
UGHer（無意識的重力仮説）の指標 **PoR / ΔE / grv** でAI回答の意味的誠実性を定量評価する。

- **PoR** (Point of Resonance): 問いの核心に対する回答の位置座標 **(S, C)**
  - **S 軸**（構造完全性）: 回答が壊れていないか (0–1)
  - **C 軸**（命題カバレッジ）: 核心を拾っているか (0–1)
- **ΔE** (Delta E): PoR 座標上における理想回答からの距離 (0–1)
- **grv** (Gravity): 回答内の語彙重力分布 (dict) — 操作化は未着手

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

### UGH 計算式（ugh_calculator.py — 電卓層）

Audit Engine の電卓層で PoR 座標と ΔE を算出する。推論ゼロ、決定的。

```
PoR = (S, C)

S = 1 - Σ(w_k × f_k) / Σ(w_k)
    w = {f1: 5, f2: 25, f3: 5, f4: 5}    Σ(w_k) = 40

C = hits / n_propositions

ΔE = (w_s × (1-S)² + w_c × (1-C)²) / (w_s + w_c)
    w_s = 2, w_c = 1
```

**各式の意味:**
- **S**: f1〜f4 の加重平均による構造完全性。f2（用語捏造）に最大重み 25 を配置
- **C**: 命題照合（tfidf + cascade）による核心カバレッジ
- **ΔE**: S と C の加重二乗和。両軸からの距離を1つのスカラーに統合

**HA20 検証結果 (n=20, human_score vs 各指標, t=0.0 統一スライス):**

| 指標 | Spearman ρ | p値 | 備考 |
|------|-----------|-----|------|
| **ΔE (system C)** | **-0.7737** | **<0.001** | **メイン指標（デプロイ可能）** |
| ΔE (human C) | -0.9266 | <0.001 | 参照上限（ターゲット情報含む） |
| C_sys (system) | 0.4030 | 0.078 | system 命題照合 |
| C_human (human) | 0.9090 | <0.001 | 人間アノテーター（参照上限） |
| S (構造完全性) | 0.5770 | 0.008 | t=0.0 スライスで有意 |

- ΔE (system C) は C_sys 単独 (ρ=0.403) を大幅に上回る。S の統合が品質予測を改善
- S 単独でも ρ=0.577 (p=0.008) で有意。t=0.0 の f2(用語捏造) が主要寄与因子
- ΔE (human C) ρ=-0.927 は参照上限。system C の精度が ΔE のボトルネック

分析データ: `analysis/pipeline_a_correlation/`

### Quality Score（Model C' ボトルネック型）

`detector.py` の `compute_quality_score()` が propositions_hit_rate, fail_max, delta_e_full を統合し、1-5 の品質スコアを算出する。

```python
from detector import compute_quality_score

result = compute_quality_score(
    propositions_hit_rate=0.667,
    fail_max=0.5,
    delta_e_full=0.28,
)
# {"quality_score": 3.0, "quality_model": "bottleneck_v1", ...}
```

**計算ロジック:**
```
L_P = 1 - propositions_hit_rate
L_struct = fail_max (None → 0.0)
L_R = delta_e_full
L_linear = α × L_P + β × L_struct + γ × L_R
L_op = max(L_struct, L_linear)    ← ボトルネック演算子
quality_score = clamp(5 - 4 × L_op, 1.0, 5.0)
```

**パラメータ（暫定値、n=48 で再校正予定）:**

| 定数 | 値 | 備考 |
|------|-----|------|
| `QUALITY_ALPHA` | 0.4 | 命題損失の重み |
| `QUALITY_BETA` | 0.0 | 線形項に L_struct 不要（max ボトルネックのみ） |
| `QUALITY_GAMMA` | 0.8 | ΔE の重み（主要寄与因子） |

**検証結果:** 全データ ρ=0.8292, LOO-CV ρ=0.8018 (Model A: ρ=0.4030)
**フォールバック:** fail_max=None → L_struct=0.0（ボトルネック不発動）

設計詳細: `analysis/semantic_loss/implementation_design_model_c.md`
セッションレポート: `analysis/semantic_loss/session_report_20260403_model_c.md`

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
| 命題マッチ: direct_recall | ≥ 0.15 | `detector.py` |
| 命題マッチ: full_recall | ≥ 0.35 | `detector.py` |
| 命題マッチ: min_overlap | ≥ 3 | `detector.py` |
| 演算子回収: direct_recall | ≥ 0.10 | `detector.py` |
| 演算子回収: full_recall | ≥ 0.25 | `detector.py` |
| 演算子回収: min_overlap | ≥ 2 | `detector.py` |
| cascade: θ_sbert | 0.50 | `cascade_matcher.py` |
| cascade: δ_gap | 0.04 (score > 0.70 時 0.02) | `cascade_matcher.py` |
| cascade: HIGH_SCORE_THRESHOLD | 0.70 | `cascade_matcher.py` |
| cascade: RELAXED_DELTA_GAP | 0.02 | `cascade_matcher.py` |
| cascade: c4 閾値 | f4_flag < 1.0 | `cascade_matcher.py` |
| quality: QUALITY_ALPHA | 0.4 | `detector.py` |
| quality: QUALITY_BETA | 0.0 | `detector.py` |
| quality: QUALITY_GAMMA | 0.8 | `detector.py` |
| ΔE: WEIGHT_S | 2 | `ugh_calculator.py` |
| ΔE: WEIGHT_C | 1 | `ugh_calculator.py` |
| S: WEIGHTS_F | f1=5, f2=25, f3=5, f4=5 | `ugh_calculator.py` |

## Baseline & HA20

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

### HA20 評価指標

| 指標 | 値 | 説明 |
|------|-----|------|
| 方向性一致率 | 19/20 (0.95) | human_score ≥ 4 → accept 期待、≤ 2 → not accept 期待 |
| Spearman ρ (system, Model A) | 0.4030 (p=0.078) | human_score vs hit_rate のみ |
| Spearman ρ (system, Model C') | 0.8292 (全データ) / 0.8018 (LOO-CV) | human_score vs quality_score (t=0.0, system_hit_rate) |
| Spearman ρ (ΔE, system C) | -0.7737 (p<0.001) | human_score vs ΔE (t=0.0統一, system_hit_rate) — デプロイ可能指標 |
| Spearman ρ (ΔE, human C) | -0.9266 (p<0.001) | human_score vs ΔE (human propositions_hit) — 参照上限 |
| Spearman ρ (reference) | 0.9090 (p<0.001) | human_score vs 人間アノテーター propositions_hit（内部一貫性指標） |

注記:
- ρ=0.9090 は人間アノテーター内部の一貫性を示す参照値
- ΔE (human C) ρ=-0.927 は参照上限。C に human propositions_hit を使うとターゲット情報が漏洩するため、デプロイ可能指標は ΔE (system C) ρ=-0.774
- Model C' (ρ=0.8292) は t=0.0 + system_hit_rate + cosine ΔE で校正
- system 命題照合の精度改善が ΔE 改善のボトルネック
- Model C' のパラメータは n=20 暫定値。n=48 で再校正予定

分析データ:
- Model C': `analysis/semantic_loss/ha20_merged_for_model_c.csv`
- ΔE 検証: `analysis/pipeline_a_correlation/`

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

CI通過 = lint clean + 全テストpass (256 collected, 3 skipped: fugashi 未インストール)

## Important Notes

- `ugh3-metrics-lib`は外部Git依存。CIではminimal backendで動作
- `.gitignore`に `*.db`, `*.sqlite`, `.env` が含まれる — DB・環境ファイルをコミットしない
- GoldenStoreは初期データ3件をハードコード (ugh_definition, por_definition, delta_e_definition)
- テスト時は `_empty_store()` ヘルパーで初期データをクリアして分離する
- `grv` はDBにJSON文字列として保存 (`json.dumps`)
- `por_fired` はSQLiteにINTEGER (0/1) で保存
- 演算子フレーム検出テストは `tests/test_operator_frame.py` (32件)
- `_SYNONYM_MAP` (110エントリ) と `OPERATOR_CATALOG` (4族) は detector.py 内の dict リテラルで管理
- cascade テスト (`tests/test_cascade_tier2.py`, `tests/test_cascade_tier3.py`) は SBert 未インストール環境で自動 skip
- 否定極性マーカー (`_NEGATION_POLARITY_FORMS`) は全トークン2文字以上の具体形で定義（bare 1文字トークン禁止）
- quality_score テストは `tests/test_quality_score.py` (5件: パラメータ定義, ボトルネック動作, フォールバック, hit_rate非変更, HA20回帰)
- `QUALITY_ALPHA`, `QUALITY_BETA`, `QUALITY_GAMMA` は暫定値（n=20 LOO-CV 検証済み）。n=48 アノテーション完了後に再校正する

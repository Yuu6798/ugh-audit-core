# ugh-audit-core

**UGH Audit Core** — AI回答の意味論的監査基盤

UGHer（無意識的重力仮説）の指標 **PoR / ΔE / grv** を用いて、
AIの回答が「意味的に誠実だったか」を定量的に評価・記録するフレームワーク。

---

## コンセプト

従来のAI評価（正確性・流暢さ・安全性）とは別軸の監査を提供する。

| 指標 | 測定内容 | 暴くもの |
|------|---------|---------|
| **PoR = (S, C)** | 問いの核心に対する回答の位置座標 | 「答えた」のか「それっぽいことを言った」かの違い |
| **ΔE** | PoR 座標上における理想回答からの距離 | バイアス・回避・過剰一般化 |
| **grv** | 回答内の語彙重力分布 | どの概念に引っ張られて回答が歪んだか |

### 計算式

```
PoR = (S, C)

S = 1 - Σ(w_k × f_k) / 40     構造完全性 [0,1]
    w: f1=5, f2=25, f3=5, f4=5

C = hits / n_propositions       命題カバレッジ [0,1]

ΔE = (2(1-S)² + (1-C)²) / 3    意味距離 [0,1]

quality_score = 5 - 4 × ΔE     品質スコア [1,5]
```

### verdict 判定（HA48 検証済み確定値）

| verdict | 条件 | 意味 |
|---------|------|------|
| **accept** | C≠None AND ΔE ≤ 0.10 | 意味的に十分な回答 |
| **rewrite** | C≠None AND 0.10 < ΔE ≤ 0.25 | 部分的な修正で改善可能 |
| **regenerate** | C≠None AND ΔE > 0.25 | 再生成が必要 |
| **degraded** | C=None OR ΔE=None | メタデータ不足で本計算不能 |

### 検証結果（HA48, n=48, v5 ベースライン 197/310 hits）

| 指標 | Spearman ρ | p値 | 備考 |
|------|-----------|-----|------|
| ΔE vs O (system C) | -0.5195 | 0.000154 | 現行デプロイ指標 |
| **L_sem vs O (Phase 4 校正)** | **-0.5563** | **<0.001** | **診断用分解指標、ΔE を上回る** |
| ΔE vs O (human C) | 0.8616 | <0.001 | 参照上限（ターゲット情報含む） |

注: scipy.stats.spearmanr（タイ補正あり）で算出。system C の命題照合精度が ΔE のボトルネック。参照上限 ρ=0.862 との差は検出パイプラインの精度改善で縮まる。

**L_sem (意味損失関数)**: 現行 ΔE を分解・拡張した診断用指標。7 項 (L_P, L_Q, L_R, L_A, L_G, L_F, L_X) の線形和で、どの側面が劣化したかを項別に読める。詳細は [`docs/semantic_loss.md`](docs/semantic_loss.md) 参照。

---

## アーキテクチャ

推論ゼロ・決定的パターンマッチのみで動作する3層パイプライン。

注: cascade（SBert）導入により推論ゼロの厳密な定義は再検討中。メインパイプライン（detect→calculate→decide）は推論ゼロだが、cascade_matcher は SBert embedding を使用する。

```
[質問 Q + メタデータ]  →  [AI回答 R]
    │
    ▼
┌──────────────────────────────────┐
│  detector.py  (検出層)           │
│  テキスト → Evidence             │
│  f1: 主題逸脱   f2: 用語捏造    │
│  f3: 演算子無処理 f4: 前提受容   │
│  + 命題カバレッジ                │
├──────────────────────────────────┤
│  ugh_calculator.py (電卓層)      │
│  Evidence → State                │
│  S(構造完全性) C(命題被覆率)     │
│  ΔE(意味距離) quality_score      │
├──────────────────────────────────┤
│  decider.py (判定層)             │
│  State + Evidence → Policy       │
│  accept / rewrite / regenerate   │
│  + repair_order (修復opcode列)   │
├──────────────────────────────────┤
│  cascade_matcher.py (回収補助)   │
│  Tier 2: SBert embedding候補生成 │
│  Tier 3: 多条件ANDフィルタ       │
│  → Z_RESCUED / miss              │
└──────────────────────────────────┘
```

### LLM メタデータ動的生成（自由質問対応）

102問の手動キュレーション済みメタデータがない自由質問に対して、
LLM で `question_meta` を動的生成し、パイプラインを実行する。

```
auto_generate_meta: true
    │
    ▼
┌────────────────────────┐
│  meta_generator.py     │  Claude API で
│  (question → meta)     │  命題を動的生成
├────────────────────────┤
│  meta_cache            │  同一質問は
│  (~/.ugh_audit/        │  キャッシュから返す
│   meta_cache/)         │  (LLM呼び出しゼロ)
└────────┬───────────────┘
         ▼
  既存パイプライン（変更なし）
```

- **metadata_source: "llm_generated"** で手動メタとの区別が明示される
- **opt-in**: `auto_generate_meta=true` を指定しない限り従来通り degraded

検証結果 (n=102): degraded 排除 100%, verdict 一致率 61.8%, ΔE 相関 ρ=0.378

設計詳細: `docs/orchestration_design.md`, `experiments/README.md`

#### 検出層の4指標

| 指標 | 検出内容 | データソース |
|------|---------|-------------|
| **f1_anchor** | 主題逸脱 — 質問キーワードが回答に不在 | `_extract_keywords` + 予約語aliases |
| **f2_unknown** | 用語捏造 — 予約語の禁止再解釈 | `registry/reserved_terms.yaml` |
| **f3_operator** | 演算子無処理 — 全称/排他/因果等の演算子に未対応 | `registry/operator_catalog.yaml` |
| **f4_premise** | 前提受容 — 埋め込み前提を無批判に受容 | `registry/premise_frames.yaml` |

#### 命題マッチング + 演算子フレーム検出

命題カバレッジ判定は漢字バイグラム + 類義語拡張で実施。
演算子フレーム検出レイヤーにより、演算子を含む命題の回収精度を向上させる。

| 演算子族 | 典型表現 | 効果 |
|---------|---------|------|
| **negation** | ではない / 未〜 / 不可能 | 極性反転 — 回答にも否定形を要求 |
| **deontic** | べき / すべきではない | 当為フラグ — 事実記述との区別 |
| **skeptical_modality** | かもしれない / とは限らない | 確信度低下 — 断定との区別 |
| **binary_frame** | ではなく / 二項対立 | 対立分割 — 両側の存在確認 |

通常マッチ (dr≥0.15, fr≥0.30, ov≥3) 失敗時、演算子が検出された命題は
緩和閾値 (dr≥0.10, fr≥0.25, ov≥2) + 概念近傍マーカー + 極性検証で再判定される。

#### 判定ロジック

| ΔE bin | C bin | 判定 |
|--------|-------|------|
| 1 | -- | accept |
| 2 | ≥ 2 | accept |
| 2 | 1 | rewrite |
| 3 | -- | rewrite |
| 4 | -- | regenerate |

#### 修復opcode

判定が `rewrite` / `regenerate` の場合、検出された問題に応じた修復命令列（repair_order）を生成。
opcodeは `opcodes/runtime_repair_opcodes.yaml` に定義（13種、コスト表付き）。

---

## インストール

```bash
# 基本（テスト + サーバー依存）
pip install -e ".[dev]"

# サーバーデプロイ（REST API + MCP）
pip install -e ".[server]"

# 分析スクリプト (scipy + matplotlib)
pip install -e ".[analysis]"

# 実験基盤 (Claude/GPT オーケストレーション)
pip install -e ".[experiment]"
```

---

## クイックスタート

### CLI（Audit Engine）

```bash
python audit.py --question-id q001 --response "AIは..." --data data/question_sets/ugh-audit-100q-v3-1.json.txtl.txt --pretty
```

### REST API

```bash
uvicorn ugh_audit.server:app --host 0.0.0.0 --port 8000
```

```python
import httpx

# 手動メタ付き（従来通り）
resp = httpx.post("http://localhost:8000/api/audit", json={
    "question": "PoRが高ければAI回答は誠実だと言えるか？",
    "response": "PoRは...",
    "question_meta": {
        "core_propositions": ["PoRは共鳴度であり誠実性の十分条件ではない"],
        "trap_type": "metric_omnipotence"
    }
})

# 自由質問（LLM メタ自動生成）
resp = httpx.post("http://localhost:8000/api/audit", json={
    "question": "AIは本当に創造性を持てるのか？",
    "response": "AIの創造性は...",
    "auto_generate_meta": True,  # opt-in
})
print(resp.json()["metadata_source"])  # "llm_generated"
```

---

## ディレクトリ構成

```
# Audit Engine（構造的意味監査パイプライン）
audit.py              # パイプライン統合 (detect → calculate → decide)
detector.py           # 検出層 — テキスト → Evidence
ugh_calculator.py     # 電卓層 — Evidence → State (S, C, ΔE, quality_score)
decider.py            # 判定層 — State + Evidence → Policy
cascade_matcher.py    # 回収補助 — SBert Tier 2 + 多条件 Tier 3
grv_calculator.py     # 因果構造損失 grv — 3項式 (drift/dispersion/collapse)
mode_signal.py        # 応答モード適合度信号 response_mode_signal
semantic_loss.py      # 意味損失関数 L_sem — 診断用分解指標
batch_audit_102.py    # 102問一括監査スクリプト
registry/             # YAML辞書（予約語・演算子・前提フレーム）
opcodes/              # 修復opcode定義

# UGH Audit Layer（REST/MCP サーバー + 永続化）
ugh_audit/
├── collector/        # 監査+保存パイプライン
├── storage/          # SQLite永続化
├── reference/        # リファレンスセット管理
├── report/           # テキスト/CSVレポート生成
├── engine/           # Phase 2 エンジン (calculator, decision, runtime)
├── metadata_generator.py  # メタデータ欠損検出 + LLM 生成リクエスト構築
├── server.py         # REST API + MCP 統合サーバー
└── mcp_server.py     # MCP スタンドアロンサーバー

# 実験基盤（LLM オーケストレーション）
experiments/
├── meta_generator.py          # Claude API → question_meta 動的生成
├── meta_cache.py              # question_meta ファイルキャッシュ
├── response_source.py         # Codex MCP / GPT-4o → 回答生成
├── orchestrator.py            # 統合オーケストレーション + 改善ループ
├── validate_against_102.py    # 手動メタ vs LLM メタ比較検証
└── prompts/                   # プロンプトテンプレート

examples/
tests/
docs/
```

---

## Public API

```python
from ugh_audit import (
    AuditDB,               # SQLite保存
    AuditCollector,        # パイプライン (audit + save)
    SessionCollector,      # セッション単位収集
    GoldenStore,           # リファレンス管理
    GoldenEntry,           # リファレンスエントリ
    generate_text_report,  # テキストレポート
    generate_csv,          # CSVエクスポート
)
```

---

## MCP サーバー（ChatGPT Connectors 対応）

ChatGPT から `audit_answer` ツールを呼び出せる MCP (Model Context Protocol) サーバーを内蔵。
`stateless_http` モードで動作するため、マルチワーカー / ロードバランサー環境でも安定稼働する。

### セットアップ

```bash
pip install -e ".[server]"
```

### MCP サーバー起動

```bash
# スタンドアロン (Streamable HTTP, port 8000)
python -m ugh_audit.mcp_server

# ポート指定
python -m ugh_audit.mcp_server --port 9000

# REST API + MCP 統合サーバー (FastAPI)
uvicorn ugh_audit.server:app --host 0.0.0.0 --port 8000
# → MCP: http://localhost:8000/mcp
# → REST: http://localhost:8000/api/audit, /api/history
```

### REST API エンドポイント

| メソッド | パス | 説明 |
|---------|------|------|
| POST | `/api/audit` | AI回答を意味監査 |
| GET | `/api/history` | 直近の監査履歴を取得 |
| POST | `/mcp` | MCP Streamable HTTP エンドポイント |
| GET | `/health` | ヘルスチェック (`{"status": "ok"}`) |

### ツール仕様

**audit_answer** -- AI回答を意味監査する

入力:
- `question` (string, 必須): ユーザーの質問
- `response` (string, 必須): AIの回答
- `reference` (string, 省略可): 期待される正解（省略時は GoldenStore から自動検索）
- `session_id` (string, 省略可): セッションID
- `question_meta` (dict, 省略可): 問題メタデータ（core_propositions 等）
- `auto_generate_meta` (bool, デフォルト false): question_meta 未提供時に LLM で動的生成

出力:
```json
{
  "schema_version": "2.0.0",
  "S": 1.0,
  "C": 1.0,
  "delta_e": 0.0,
  "quality_score": 5.0,
  "verdict": "accept",
  "hit_rate": "3/3",
  "structural_gate": {
    "f1": 0.0, "f2": 0.0, "f3": 0.0, "f4": 0.0,
    "gate_verdict": "pass",
    "primary_fail": "none"
  },
  "saved_id": 1,
  "mode": "computed",
  "metadata_source": "inline",
  "computed_components": ["C", "S", "delta_e", "f1", "f2", "f3", "f4", "quality_score"],
  "missing_components": [],
  "errors": [],
  "degraded_reason": []
}
```

**metadata_source の値:**

| 値 | 意味 |
|---|---|
| `inline` | リクエストに question_meta が含まれていた |
| `llm_generated` | LLM (Claude API) が動的生成した |
| `computed_ai_draft` | LLM 生成メタ + soft_rescue で部分回収 |
| `fallback` | LLM 不使用のヒューリスティック結果（degraded 強制） |
| `none` | question_meta なし、auto_generate_meta も off |

### 環境変数

| 変数名 | 説明 | デフォルト |
|--------|------|-----------|
| `UGH_AUDIT_DB` | SQLite DB ファイルパス | `~/.ugh_audit/audit.db` |
| `ANTHROPIC_API_KEY` | Claude API キー（LLM meta 生成用） | なし |
| `OPENAI_API_KEY` | OpenAI API キー（実験基盤の GPT 回答生成用） | なし |
| `UGH_META_CACHE_DIR` | meta キャッシュディレクトリ | `~/.ugh_audit/meta_cache/` |

読み取り専用コンテナやサーバーレス環境では `UGH_AUDIT_DB=/tmp/audit.db` のように書き込み可能なパスを指定する。
`ANTHROPIC_API_KEY` は `auto_generate_meta=true` 時のみ必要。`OPENAI_API_KEY` は `experiments/` の実行時のみ必要。

---

## 確定パラメータ一覧

| パラメータ | 確定値 | 場所 |
|-----------|--------|------|
| S の重み | f1:5, f2:25, f3:5, f4:5 | `ugh_calculator.py` |
| ΔE の重み | w_s=2, w_c=1 | `ugh_calculator.py` |
| quality_score | 5 - 4 × ΔE | `ugh_calculator.py` |
| synonym_dict | 110 キー | `detector.py` |
| 命題マッチ: fr 閾値 | 0.30 | `detector.py` |
| 命題マッチ: direct_recall | ≥ 0.15 | `detector.py` |
| 命題マッチ: overlap | ≥ 3 | `detector.py` |
| 演算子回収: direct_recall | ≥ 0.10 | `detector.py` |
| 演算子回収: full_recall | ≥ 0.25 | `detector.py` |
| 演算子回収: overlap | ≥ 2 | `detector.py` |
| cascade: θ_sbert | 0.50 | `cascade_matcher.py` |
| cascade: SBert モデル | paraphrase-multilingual-MiniLM-L12-v2 | `cascade_matcher.py` |
| verdict: accept | C≠None AND ΔE ≤ 0.10 | `server.py` / `mcp_server.py` |
| verdict: rewrite | C≠None AND 0.10 < ΔE ≤ 0.25 | 同上 |
| verdict: regenerate | C≠None AND ΔE > 0.25 | 同上 |
| verdict: degraded | C=None OR ΔE=None | 同上 |

---

## フェーズロードマップ

- **Phase 1**: スコアリング基盤 + ログ蓄積
- **Phase 2（現在）**: Audit Engine — 構造的意味監査パイプライン（detector / calculator / decider）
- **Phase 3**: referenceセット設計（Human-golden / Cross-model / Self-baseline）
- **Phase 4**: Phase Map可視化 + パターン分析

### grv (因果構造損失) — v1.4 実装済み

`grv = clamp(w_d × drift + w_s × dispersion + w_c × collapse_v2)`

確定重み: w_d=0.70, w_s=0.05, w_c=0.25 (HA48 ρ=-0.357)。
SBert 依存。詳細: [`docs/grv_design.md`](docs/grv_design.md)

### response_mode_signal — v1 実装済み

質問が期待する応答形式 (`mode_affordance`) に対する回答の適合度を測る非破壊信号。
6 modes: definitional / analytical / evaluative / comparative / critical / exploratory。
cue-list ベースの決定的 scorer。verdict に影響しない。
詳細: [`docs/mode_affordance.md`](docs/mode_affordance.md)

---

## 設計ドキュメント

| コンポーネント | ドキュメント |
|---|---|
| 検出層 (演算子フレーム + Relaxed Tier1) | [`docs/detector_design.md`](docs/detector_design.md) |
| Cascade Matcher (SBert Tier 2/3) | [`docs/cascade_design.md`](docs/cascade_design.md) |
| GoldenStore リファレンス検索 | [`docs/golden_store.md`](docs/golden_store.md) |
| 計算式 (PoR / ΔE / verdict) | [`docs/formulas.md`](docs/formulas.md) |
| 意味損失関数 L_sem | [`docs/semantic_loss.md`](docs/semantic_loss.md) |
| grv 因果構造損失 | [`docs/grv_design.md`](docs/grv_design.md) |
| mode_affordance / response_mode_signal | [`docs/mode_affordance.md`](docs/mode_affordance.md) |
| メタデータパイプライン | [`docs/metadata_pipeline.md`](docs/metadata_pipeline.md) |
| REST API + MCP サーバー | [`docs/server_api.md`](docs/server_api.md) |
| 検証結果 (HA48 / HA20) | [`docs/validation.md`](docs/validation.md) |
| Self-Audit 実験 | [`docs/self_audit_experiment.md`](docs/self_audit_experiment.md) |
| SVP-RPE 統合実装プラン（関連プロジェクト） | [`docs/svp_rpe_implementation_plan.md`](docs/svp_rpe_implementation_plan.md) |

---

## 理論背景

- [無意識的重力仮説（UGHer）](https://note.com/kamo6798/n/n5aeea478d12e)
- [RPE入門](https://note.com/kamo6798/n/n99cbb5307e13)
- [SVPとRPEの実践メモ](https://note.com/kamo6798/n/nb45c716a2c61)
- [ugh3-metrics-lib](https://github.com/Yuu6798/ugh3-metrics-lib)

---

## License

MIT License (C) 2025 Yuu6798

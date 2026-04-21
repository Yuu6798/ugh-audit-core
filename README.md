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

### 検証結果（HA48, n=48, current pipeline snapshot 2026-04-21）

**評価目的: 主評価** — システム自動算出 C と人手参照 C の相関を比較し、検出パイプラインの上限性能を評価する。

| 指標 | Spearman ρ | p値 | 備考 |
|------|-----------|-----|------|
| ΔE vs O (system C) | -0.4817 | 0.000527 | ΔE baseline (current) |
| L_sem vs O (Phase 4) | -0.5563 | <0.001 | L_P+L_F 2項最適化 (Phase 5 snapshot) |
| **L_sem vs O (Phase 5)** | **-0.6020** | **<0.001** | **L_P+L_F+L_G 3項 (grv 統合, Phase 5 snapshot)** |
| ΔE vs O (human C) | 0.8616 | <0.001 | 参照上限（ターゲット情報含む） |

注: scipy.stats.spearmanr（タイ補正あり）で算出。system C の命題照合精度が ΔE のボトルネック。参照上限 ρ=0.862 との差は検出パイプラインの精度改善で縮まる。

**測定精度履歴:** ΔE vs O (system C) は Apr 6 snapshot で ρ=-0.5195、検出層精度改善 (PR #95 等) を反映した current snapshot で ρ=-0.4817。両版とも旧 CI の範囲内で統計的に重なるが、主数字は current 値を採用する。詳細: [`docs/validation.md`](docs/validation.md) §「HA48 検証結果 → 測定精度履歴」。

**95% 信頼区間 (Fisher z):** 主指標 HA48 ΔE (system C, current) は **ρ=-0.4817, 95% CI [-0.6736, -0.2289]**。CI 下端は運用閾値 ρ=-0.5 を保証しない（点推定も 0.5 を僅かに下回る）。**n=48 は小標本**、アノテーションは **single annotator** による制約があり、IRR 未測定。全指標の CI と Limitations 詳細は [`docs/validation.md`](docs/validation.md) §「信頼区間」「Limitations」。

**主指標政策:** go/no-go は **ΔE を主指標**とする。**L_sem は診断用**（どの項が悪いかの debug 情報）で、LOO-CV shrinkage=0.128 を検出したため runtime 重みは保守的に縮小済み。詳細は [`docs/validation.md#主指標政策-primary-metric-policy`](docs/validation.md#主指標政策-primary-metric-policy) 参照。

**L_sem (意味損失関数)**: 現行 ΔE を分解・拡張した診断用指標。7 項 (L_P, L_Q, L_R, L_A, L_G, L_F, L_X) の線形和で、どの側面が劣化したかを項別に読める。Phase 5 で grv (L_G) を統合し ρ=-0.6020 に到達。詳細は [`docs/semantic_loss.md`](docs/semantic_loss.md) 参照。

### Phase E verdict_advisory 校正結果 (HA63, n=63)

**評価目的: Phase E ship 判定** — `mode_conditioned_grv` (Phase C) の
`anchor_alignment` / `collapse_risk` 信号を verdict 層に downgrade 方向で
統合した際、primary verdict (ΔE ベース) に対する副次 verdict (advisory)
が ρ 改善を示すかを検証する。

| 指標 | 値 | 備考 |
|------|-----|------|
| サンプル構成 | HA48 + accept40 batch1 (n=63, accept subset n=40) | |
| `rho_primary_full` (ΔE ベース) | 0.4408 | 比較基準 |
| **`rho_advisory_full` (mcg 統合後)** | **0.5225** | **Δρ=+0.082 改善、ship 基準クリア** |
| `fire_rate` (advisory downgrade 発火率) | 0.225 | 第一目標 10–25% に合致 |
| leak check `pearson_r(C, anchor_alignment)` | 0.3749 | 独立性確保 |
| 採用閾値 | `τ_collapse_high=0.28, τ_anchor_low=0.80` | grid search n=63 |

Ship 判定基準: `rho_advisory_full >= rho_primary_full - 0.02` かつ
`fire_rate ≤ 0.30`。両条件満たし 2026-04-18 に ship。primary verdict は
非破壊で、advisory は `accept → rewrite` downgrade のみを許可する
保守運用。詳細: [`docs/phase_e_verdict_integration.md`](docs/phase_e_verdict_integration.md)。

---

## アーキテクチャ

本システムは 2 層構造で動作する。

### 決定性の適用範囲

| 層 | 依存 | 決定性 | 役割 |
|---|---|---|---|
| **core pipeline** (detect → calculate → decide) | tfidf + YAML 辞書のみ | 決定的（同じ入力なら同じ出力）| Evidence から S, C, ΔE, verdict を算出（計算式の本体） |
| **cascade layer** (cascade_matcher) | SBert embedding | 確率的 | tier 1 (tfidf) で miss した命題の optional 回収補強 |

- **計算式と判定規則は core pipeline にある**。cascade 有効時は detect 段で
  命題 hit が追加され、core に渡る C 入力が上方補正されることがある
  （hit の追加のみ、miss への降格はない）。
- **cascade layer は optional**。SBert 未インストール / モデルロード失敗時は
  `cascade_matcher.get_shared_model()` が None を返し、pipeline は tier 1
  のみで完走する（fallback は silent、verdict ロジックに影響なし）。
- 論文・査読で「推論ゼロ」と呼ぶ範囲は core pipeline を指す。cascade は
  「決定的パイプラインに対する確率的回収補強」として区別する。

詳細: [`docs/cascade_design.md#core-vs-cascade`](docs/cascade_design.md#core-vs-cascade)

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

検証結果 (n=102, **副次評価**): LLM 動的メタ生成時の degraded 排除と verdict 一致率を測定する。HA48 主評価とは目的が異なり、相関値の絶対水準ではなく degraded 解消と verdict 整合の両立を見る。
- degraded 排除: 100%
- verdict 一致率: 61.8%
- ΔE 相関: ρ=0.378

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

Python 3.10 以降を前提とする。PEP 668 環境（Ubuntu 22.04 以降の system
Python, Homebrew Python など）では仮想環境での install を推奨する。

```bash
# 仮想環境を作成・有効化
python -m venv .venv
source .venv/bin/activate           # Linux / macOS
# .venv\Scripts\activate            # Windows PowerShell

# 基本（テスト + サーバー依存）
pip install -e ".[dev]"

# サーバーデプロイ（REST API + MCP）
pip install -e ".[server]"

# 分析スクリプト (scipy + matplotlib)
pip install -e ".[analysis]"

# 実験基盤 (Claude/GPT オーケストレーション)
pip install -e ".[experiment]"
```

仮想環境を使わない場合は `pip install --user` または `--break-system-packages`
が必要になる環境がある点に注意。

---

## クイックスタート

### CLI（Audit Engine）

```bash
python audit.py --question-id q001 --response "AIは..." --data data/question_sets/ugh-audit-100q-v3-1.jsonl --pretty
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
mode_grv.py           # mode_conditioned_grv v2 — モード条件付き grv 解釈ベクトル
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
├── engine/           # Phase 2 エンジン (calculator, decision, runtime, metapatch)
├── metadata_generator.py  # メタデータ欠損検出 + LLM 生成リクエスト構築
├── metadata_policy.py     # AI草案メタデータの昇格ポリシー
├── soft_rescue.py    # AI草案メタデータ向け soft-hit rescue (C=0 部分回収)
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
| `UGH_META_CACHE_DIR` | LLM meta キャッシュディレクトリ | `~/.ugh_audit/meta_cache/` |
| `UGH_AUDIT_CACHE_DIR` | 埋め込みキャッシュディレクトリ | `~/.ugh_audit/` |
| `UGH_AUDIT_EMBED_CACHE_DISABLE` | `1/true/yes` で埋め込みキャッシュ無効化 | 無効化しない |
| `UGH_AUDIT_EMBED_CACHE_MAX` | 埋め込みキャッシュのエントリ上限（hard cap） | 10000 |
| `HF_HUB_OFFLINE` | `1` で HuggingFace Hub オフラインモード（ローカルキャッシュのみ使用） | なし |
| `TRANSFORMERS_OFFLINE` | `1` で transformers オフラインモード | なし |

読み取り専用コンテナやサーバーレス環境では `UGH_AUDIT_DB=/tmp/audit.db` /
`UGH_AUDIT_CACHE_DIR=/tmp/ugh_cache` のように書き込み可能なパスを指定する。
`ANTHROPIC_API_KEY` は `auto_generate_meta=true` 時のみ必要。
`OPENAI_API_KEY` は `experiments/` の実行時のみ必要。
`HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1` はモデルダウンロードを
伴う calibration / cascade 処理をオフライン実行するときに設定する。

### トラブルシューティング

#### SBert モデルダウンロードが進まない / `leak_check n=0` / `anchor_alignment` が全行 None

**典型的な原因**: proxy 環境変数が SBert (sentence-transformers) の
HuggingFace モデルダウンロードを阻害している。

**診断**:

```bash
env | grep -iE "^(http|https)_proxy"
```

`HTTP_PROXY` / `HTTPS_PROXY` / `http_proxy` / `https_proxy` のいずれかに
到達不能なアドレス（例: `http://127.0.0.1:9`）が設定されている場合、
モデルダウンロード失敗で cascade layer が silent fallback し、
`anchor_alignment=None` が大量発生しやすくなる。結果として C の上方補正が
効かず、スコアが保守的になる。

**復旧手順**:

```bash
# 1. proxy 環境変数を解除
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy

# 2. ローカル HuggingFace キャッシュが存在することを確認
ls ~/.cache/huggingface/hub/ 2>/dev/null | head -5

# 3. オフライン運用モードで実行
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
python examples/basic_audit.py
```

ローカルキャッシュに目的のモデル
（`paraphrase-multilingual-MiniLM-L12-v2`）が存在しない場合は、一度 proxy
なしで online ダウンロードを通してから上記 offline モードに切り替える。

#### 診断ルール（早期の環境疑い）

以下のいずれかを観測した場合、cascade が silent degrade している可能性が
高い。計算ロジックの問題として深掘りする前に環境を疑う:

- calibration / 検証スクリプトで `leak_check n=0`
- HA48 / HA63 評価で `anchor_alignment` が全行 `None`
- `cascade_matcher.get_shared_model()` が常に `None` を返している
  （SBert 未インストール、モデルロード失敗、再試行上限到達など）

`UGH_AUDIT_EMBED_CACHE_DISABLE` は埋め込みキャッシュの無効化フラグであり、
shared model のロード可否には影響しない。

cascade が無効化された状態でも core pipeline（detect tier 1 + calculator + decider）は完走するため、出力自体はエラーにならず verdict は出る。
ただし C が上方修正されないので scoring が保守的になる。

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

- **Phase 1**: スコアリング基盤 + ログ蓄積 — **実装済み**
- **Phase 2**: Audit Engine (detector / calculator / decider) — **実装済み**
- **Phase 3**: reference セット設計 (GoldenStore) — **実装済み**
- **Phase 4**: Phase Map 可視化 + パターン分析 (`ugh_audit/report/phase_map.py`) — **実装済み**
- **Phase 5**: L_sem (意味損失関数) + grv 統合校正 — **実装済み** (HA48 ρ=-0.6020)
- **Phase B**: `mode_affordance` v1 (response_mode_signal) — **実装済み**
- **Phase C**: `mode_conditioned_grv` v2 (4 成分解釈ベクトル) — **実装済み** (HA48 anchor_alignment ρ=+0.41)
- **Phase E**: `verdict_advisory` (mcg → downgrade) — **ship 済み** (n=63 校正、詳細 §検証結果 HA63)
- **Phase D**: support_signal 要否判断 — 設計議論中 (Phase E で暫定達成)

フェーズは並行系 (A-E) と時系列系 (1-5) の 2 軸で進行してきた。Audit Engine
本体 (Phase 2) の上に、診断指標 (Phase 5 L_sem)、モード信号
(Phase B/C)、判定層統合 (Phase E) が順次積み上げられた。

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
| LLM オーケストレーション | [`docs/orchestration_design.md`](docs/orchestration_design.md) |
| mode_affordance / response_mode_signal | [`docs/mode_affordance.md`](docs/mode_affordance.md), [`addendum`](docs/mode_affordance_v1_addendum.md) |
| メタデータパイプライン | [`docs/metadata_pipeline.md`](docs/metadata_pipeline.md) |
| REST API + MCP サーバー | [`docs/server_api.md`](docs/server_api.md) |
| 検証結果 (HA48 / HA20) | [`docs/validation.md`](docs/validation.md) |
| Self-Audit 実験 | [`docs/self_audit_experiment.md`](docs/self_audit_experiment.md) |
| Phase E verdict_advisory 統合 | [`docs/phase_e_verdict_integration.md`](docs/phase_e_verdict_integration.md) |
| HA-accept40 アノテーションプロトコル | [`docs/annotation_protocol.md`](docs/annotation_protocol.md) |
| 閾値一覧と導出根拠の索引 (tunable な主要閾値のみ、対象外は scope 節で明示) | [`docs/thresholds.md`](docs/thresholds.md) |
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

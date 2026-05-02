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
S = 1 - Σ(w_k × f_k) / 40     構造完全性 [0,1]  w: f1=5, f2=25, f3=5, f4=5
C = hits / n_propositions       命題カバレッジ [0,1]
ΔE = (2(1-S)² + (1-C)²) / 3    意味距離 [0,1]
quality_score = 5 - 4 × ΔE     品質スコア [1,5]
```

詳細: [`docs/formulas.md`](docs/formulas.md)

### verdict 判定（HA48 検証済み確定値）

| verdict | 条件 | 意味 |
|---------|------|------|
| **accept** | C≠None AND ΔE ≤ 0.10 | 意味的に十分な回答 |
| **rewrite** | C≠None AND 0.10 < ΔE ≤ 0.25 | 部分的な修正で改善可能 |
| **regenerate** | C≠None AND ΔE > 0.25 | 再生成が必要 |
| **degraded** | C=None OR ΔE=None | メタデータ不足で本計算不能 |

### 検証結果（HA48, n=48, current pipeline snapshot 2026-04-21）

| 指標 | Spearman ρ | 95% CI (Fisher z) | 備考 |
|------|-----------|-----------|------|
| ΔE vs O (system C) | **-0.4817** | [-0.6736, -0.2289] | ΔE baseline (current, 主指標) |
| L_sem vs O (Phase 5) | **-0.5477** | [-0.7198, -0.3121] | 3項 (L_P+L_F+L_G, 診断用) |
| ΔE vs O (human C) | +0.8616 | [+0.7647, +0.9204] | 参照上限 |

**主指標政策:** go/no-go は **ΔE を主指標**とする。L_sem は診断用。
**Limitations:** n=48 は小標本、**single annotator**、IRR 未測定。
全指標 CI + Limitations 詳細: [`docs/validation.md`](docs/validation.md)。

**ベースライン比較 (HA20 / HA48):** UGHer (ΔE) は BLEU / BERTScore / SBert
cos 全てを点推定で上回る。HA20 で UGHer ρ=0.770 vs BERTScore 0.556、
HA48 で UGHer ρ=0.482 vs BERTScore 0.331。詳細:
[`docs/validation.md#ベースライン比較`](docs/validation.md#ベースライン比較)。

### Phase 8 verdict_advisory (HA63, n=63 校正、ship 済み)

`mode_conditioned_grv` の `anchor_alignment` / `collapse_risk` を verdict 層に
downgrade 方向で反映。primary verdict は不変、`accept → rewrite` の advisory
のみ許可。採用閾値 `τ_collapse_high=0.28, τ_anchor_low=0.80`、
`rho_advisory_full=0.5225` (primary 0.4408 から +0.082)、
`fire_rate=0.225`。詳細: [`docs/phase_e_verdict_integration.md`](docs/phase_e_verdict_integration.md)。

---

## アーキテクチャ

本システムは 2 層構造で動作する。

### 決定性の適用範囲

| 層 | 依存 | 決定性 | 役割 |
|---|---|---|---|
| **core pipeline** (detect → calculate → decide) | tfidf + YAML 辞書のみ | 決定的 | S, C, ΔE, verdict を算出 |
| **cascade layer** (cascade_matcher) | SBert embedding | 確率的 | tier 1 miss の optional 回収補強 (hit 追加のみ、降格なし) |

- **計算式と判定規則は core pipeline にある**。cascade は C を上方補正するのみ
- **cascade layer は optional**。SBert 未導入 / モデルロード失敗時は silent fallback
- 論文・査読で「推論ゼロ」と呼ぶ範囲は core pipeline を指す

詳細: [`docs/cascade_design.md#core-vs-cascade`](docs/cascade_design.md#core-vs-cascade)

```
[質問 Q + メタデータ]  →  [AI回答 R]
    │
    ▼
┌──────────────────────────────────┐
│  detector.py  (検出層)           │ テキスト → Evidence
│  f1-f4 + 命題カバレッジ          │
├──────────────────────────────────┤
│  ugh_calculator.py (電卓層)      │ Evidence → State (S, C, ΔE)
├──────────────────────────────────┤
│  decider.py (判定層)             │ accept / rewrite / regenerate
│                                  │ + repair_order (修復opcode列)
├──────────────────────────────────┤
│  cascade_matcher.py (回収補助)   │ Tier 2/3 SBert (optional)
└──────────────────────────────────┘
```

### LLM メタデータ動的生成（自由質問対応）

手動キュレーション済みメタデータがない自由質問に対し、`auto_generate_meta=true` で
LLM (Claude API) が `question_meta` を動的生成する (opt-in)。`metadata_source="llm_generated"`
で区別され、`state.C` が埋まれば `mode="computed_ai_draft"` (vs キュレーション済みの
`computed`) として扱う。検証結果 (n=102, 副次評価): degraded 排除 100%、
verdict 一致率 61.8%、ΔE 相関 ρ=0.378。詳細: [`docs/metadata_pipeline.md`](docs/metadata_pipeline.md),
[`docs/orchestration_design.md`](docs/orchestration_design.md)。

### 検出層の4指標 + 命題マッチング + 修復 opcode

f1-f4 (主題逸脱 / 用語捏造 / 演算子無処理 / 前提受容) を漢字バイグラム + 類義語拡張 +
演算子フレーム検出で算出。判定ロジック詳細: [`docs/detector_design.md`](docs/detector_design.md),
[`docs/formulas.md`](docs/formulas.md)。

repair opcode は `rewrite` / `regenerate` 時に生成される修復 recipe。13 種を
`opcodes/runtime_repair_opcodes.yaml` に定義。

> **opcode 評価状況:** 各 opcode の apply 有効性 (適用後 O 改善量) の評価は
> **本リポジトリの scope 外、別論文の射程**。詳細: [`docs/opcode_evaluation_plan.md`](docs/opcode_evaluation_plan.md)

---

## インストール

Python 3.10+ を前提とする。仮想環境を推奨:

```bash
python -m venv .venv
source .venv/bin/activate           # Linux / macOS
# .venv\Scripts\activate            # Windows PowerShell

pip install -e ".[dev]"        # 基本（テスト + サーバー依存）
pip install -e ".[server]"     # サーバーデプロイ (REST API + MCP)
pip install -e ".[analysis]"   # 分析スクリプト (scipy + matplotlib)
pip install -e ".[experiment]" # 実験基盤 (Claude/GPT オーケストレーション)
```

PEP 668 環境では仮想環境での install 必須。詳細は
[`docs/troubleshooting.md`](docs/troubleshooting.md) 参照。

---

## クイックスタート

### CLI（Audit Engine）

```bash
python audit.py --question-id q001 --response "AIは..." \
    --data data/question_sets/ugh-audit-100q-v3-1.jsonl --pretty
```

### REST API

```bash
uvicorn ugh_audit.server:app --host 0.0.0.0 --port 8000
```

```python
import httpx

# 手動メタ付き
resp = httpx.post("http://localhost:8000/api/audit", json={
    "question": "PoRが高ければAI回答は誠実だと言えるか？",
    "response": "PoRは...",
    "question_meta": {
        "core_propositions": ["PoRは共鳴度であり誠実性の十分条件ではない"],
        "trap_type": "metric_omnipotence"
    }
})

# 自由質問（LLM メタ自動生成、opt-in）
resp = httpx.post("http://localhost:8000/api/audit", json={
    "question": "AIは本当に創造性を持てるのか？",
    "response": "AIの創造性は...",
    "auto_generate_meta": True,
})
```

REST / MCP API 仕様の全詳細: [`docs/server_api.md`](docs/server_api.md)。

---

## ディレクトリ構成

```
# Audit Engine（構造的意味監査パイプライン）
audit.py              # パイプライン統合 (detect → calculate → decide)
detector.py           # 検出層 — テキスト → Evidence
ugh_calculator.py     # 電卓層 — Evidence → State (S, C, ΔE, quality_score)
decider.py            # 判定層 — State + Evidence → Policy
cascade_matcher.py    # 回収補助 — SBert Tier 2/3 (optional)
grv_calculator.py     # 因果構造損失 grv (drift/dispersion/collapse)
mode_signal.py        # 応答モード適合度信号 response_mode_signal
mode_grv.py           # mode_conditioned_grv v2 (Phase 7/8)
semantic_loss.py      # 意味損失関数 L_sem (診断用)
batch_audit_102.py    # 102問一括監査
registry/             # YAML辞書（予約語・演算子・前提フレーム）
opcodes/              # 修復opcode定義

# UGH Audit Layer（REST/MCP サーバー + 永続化）
ugh_audit/{server,mcp_server}.py         # REST API + MCP
ugh_audit/{collector,storage,reference,report,engine}/
ugh_audit/{metadata_generator,metadata_policy,soft_rescue}.py

# 実験基盤（LLM オーケストレーション）
experiments/{meta_generator,meta_cache,response_source,orchestrator,validate_against_102}.py

examples/ tests/ docs/ analysis/
```

---

## Public API

```python
from ugh_audit import (
    AuditDB,
    GoldenStore, GoldenEntry,
    generate_text_report, generate_csv,
)
```

プログラムから監査を行う場合は REST `POST /api/audit` または MCP ツール
`audit_answer` を利用する (詳細は [`docs/server_api.md`](docs/server_api.md))。

> **Deprecated (v0.4, removal v0.5)**: `AuditCollector` / `SessionCollector` は
> `question_meta` を受け取らないため常に `verdict="degraded"` を返す欠陥 API。
> import すると `DeprecationWarning` が発生する。v0.5 で削除予定。

---

## MCP サーバー（ChatGPT Connectors 対応）

ChatGPT から `audit_answer` ツールを呼び出せる MCP (Model Context Protocol)
サーバーを内蔵。`stateless_http` モードで動作。

```bash
pip install -e ".[server]"
python -m ugh_audit.mcp_server                 # スタンドアロン (port 8000)
uvicorn ugh_audit.server:app --port 8000       # REST + MCP 統合
# → MCP:  http://localhost:8000/mcp
# → REST: http://localhost:8000/api/audit, /api/history
```

API 仕様 (tool schema / エンドポイント / 環境変数) 全詳細:
[`docs/server_api.md`](docs/server_api.md)。
トラブルシューティング: [`docs/troubleshooting.md`](docs/troubleshooting.md)。

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
| 検証結果 (HA48 / HA20 + CI + Limitations) | [`docs/validation.md`](docs/validation.md) |
| Self-Audit 実験 | [`docs/self_audit_experiment.md`](docs/self_audit_experiment.md) |
| Phase 8 verdict_advisory 統合 | [`docs/phase_e_verdict_integration.md`](docs/phase_e_verdict_integration.md) |
| HA-accept40 アノテーションプロトコル | [`docs/annotation_protocol.md`](docs/annotation_protocol.md) |
| 閾値一覧と導出根拠の索引 | [`docs/thresholds.md`](docs/thresholds.md) |
| フェーズロードマップ + サブシステム紹介 | [`docs/roadmap.md`](docs/roadmap.md) |
| トラブルシューティング | [`docs/troubleshooting.md`](docs/troubleshooting.md) |
| SVP-RPE 統合実装プラン（関連プロジェクト） | [`docs/svp_rpe_implementation_plan.md`](docs/svp_rpe_implementation_plan.md) |
| 修復 opcode 評価プロトコル (plan, scope 外) | [`docs/opcode_evaluation_plan.md`](docs/opcode_evaluation_plan.md) |

---

## 理論背景

- [無意識的重力仮説（UGHer）](https://note.com/kamo6798/n/n5aeea478d12e)
- [RPE入門](https://note.com/kamo6798/n/n99cbb5307e13)
- [SVPとRPEの実践メモ](https://note.com/kamo6798/n/nb45c716a2c61)
- [ugh3-metrics-lib](https://github.com/Yuu6798/ugh3-metrics-lib)

---

## License

MIT License (C) 2025 Yuu6798

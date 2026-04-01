# ugh-audit-core

**UGH Audit Core** — AI回答の意味論的監査基盤

UGHer（無意識的重力仮説）の3指標 **PoR / ΔE / grv** を用いて、
AIの回答が「意味的に誠実だったか」を定量的に評価・記録するフレームワーク。

---

## コンセプト

従来のAI評価（正確性・流暢さ・安全性）とは別軸の監査を提供する。

| 指標 | 測定内容 | 暴くもの |
|------|---------|---------|
| **PoR** | 質問 ↔ 回答の意味的共鳴度 | 「答えた」のか「それっぽいことを言った」かの違い |
| **ΔE** | 期待回答 ↔ 実回答の意味ズレ量 | バイアス・回避・過剰一般化 |
| **grv** | 回答内の語彙重力分布 | どの概念に引っ張られて回答が歪んだか |

---

## アーキテクチャ

### Audit Engine（構造的意味監査パイプライン）

推論ゼロ・決定的パターンマッチのみで動作する3層パイプライン。

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
│  ΔE(意味距離)  ビン分類          │
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

通常マッチ (dr≥0.15, fr≥0.35, ov≥3) 失敗時、演算子が検出された命題は
緩和閾値 (dr≥0.10, fr≥0.25, ov≥2) + 概念近傍マーカー + 極性検証で再判定される。

#### 判定ロジック

| ΔE bin | C bin | 判定 |
|--------|-------|------|
| 1 | — | accept |
| 2 | ≥ 2 | accept |
| 2 | 1 | rewrite |
| 3 | — | rewrite |
| 4 | — | regenerate |

#### 修復opcode

判定が `rewrite` / `regenerate` の場合、検出された問題に応じた修復命令列（repair_order）を生成。
opcodeは `opcodes/runtime_repair_opcodes.yaml` に定義（13種、コスト表付き）。

### UGH Audit Layer（PoR/ΔE/grv スコアリング）

```
[質問 Q + AI回答 R + Reference]
    │
    ▼
┌──────────────────────────────────┐
│  scorer/ugh_scorer.py            │
│  PoR / ΔE / grv 計算            │
│  3層フォールバック               │
└──────────────────────────────────┘
    │
    ▼
[SQLite 蓄積] → [Phase Map レポート]
```

---

## インストール

```bash
# 基本（minimal backend — numpy のみ、テスト用）
pip install -e ".[dev]"

# フル機能（sentence-transformers + 日本語形態素解析）
pip install -e ".[full]"

# サーバーデプロイ（REST API + MCP + スコアリングバックエンド）
pip install -e ".[server]"

# 日本語対応のみ追加
pip install -e ".[ja]"

# ugh3-metrics-lib native backend
pip install -e ".[ugh3]"
```

依存：`ugh3-metrics-lib`（PoR/ΔE/grv計算エンジン）

---

## クイックスタート

```python
from ugh_audit import UGHScorer, AuditDB

scorer = UGHScorer()
db = AuditDB()

result = scorer.score(
    question="AIは意味を持てるか？",
    response="AIは意味を処理することができますが、人間のような主観的体験は持ちません。",
    reference="AIは意味を『持つ』のではなく意味位相空間で『共振』する動的プロセスです。"
)

db.save(result)
print(result)
# AuditResult(PoR=0.84, delta_e=0.09, grv={'意味': 0.41, '処理': 0.28, ...}, fired=True)
```

---

## ディレクトリ構成

```
# Audit Engine（構造的意味監査）
audit.py              # パイプライン統合 (detect → calculate → decide)
detector.py           # 検出層 — テキスト → Evidence
ugh_calculator.py     # 電卓層 — Evidence → State
decider.py            # 判定層 — State + Evidence → Policy
cascade_matcher.py    # 回収補助 — SBert Tier 2 + 多条件 Tier 3
registry/             # YAML辞書（予約語・演算子・前提フレーム）
opcodes/              # 修復opcode定義

# UGH Audit Layer（PoR/ΔE/grv スコアリング）
ugh_audit/
├── scorer/           # UGH指標スコアリング（3層フォールバック）
├── storage/          # SQLite永続化
├── reference/        # referenceセット管理（golden store）
├── collector/        # ログ収集ユーティリティ
├── report/           # Phase Mapレポート生成
├── server.py         # REST API + MCP 統合サーバー (FastAPI)
└── mcp_server.py     # MCP スタンドアロンサーバー

examples/
tests/
```

---

## MCP サーバー（ChatGPT Connectors 対応）

ChatGPT から `audit_answer` ツールを呼び出せる MCP (Model Context Protocol) サーバーを内蔵。
`stateless_http` モードで動作するため、マルチワーカー / ロードバランサー環境でも安定稼働する。

### セットアップ

```bash
# サーバー + スコアリングバックエンド一括インストール
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
| POST | `/api/audit` | AI回答を意味監査（question, response, reference?, session_id?） |
| GET | `/api/history` | 直近の監査履歴を取得 |
| POST | `/mcp` | MCP Streamable HTTP エンドポイント |
| GET | `/health` | ヘルスチェック (`{"status": "ok"}`) |

### 外部公開

```bash
# ngrok で公開
ngrok http 8000

# → https://<xxx>.ngrok-free.app/mcp が MCP URL になる
```

### ChatGPT への登録

1. ChatGPT → Settings → Connectors → Add Connector
2. MCP URL を入力: `https://<your-host>/mcp`
3. 保存後、会話中に `audit_answer` ツールが利用可能になる

### ツール仕様

**audit_answer** — AI回答を意味監査する

入力:
- `question` (string, 必須): ユーザーの質問
- `response` (string, 必須): AIの回答
- `reference` (string, 省略可): 期待される正解（省略時は GoldenStore から自動検索）
- `session_id` (string, 省略可): セッションID（同一会話の複数ターンを紐付ける。省略時は自動生成）

出力:
- `por`: 意味的共鳴度 (0–1)
- `delta_e`: 意味ズレ量 (0–1)
- `grv`: 語彙重力分布
- `verdict`: 判定 (同一意味圏 / 軽微なズレ / 意味乖離)
- `saved_id`: DB保存時の行ID

### 環境変数

| 変数名 | 説明 | デフォルト |
|--------|------|-----------|
| `UGH_AUDIT_DB` | SQLite DB ファイルパス | `~/.ugh_audit/audit.db` |

読み取り専用コンテナやサーバーレス環境では `UGH_AUDIT_DB=/tmp/audit.db` のように書き込み可能なパスを指定する。

---

## フェーズロードマップ

- **Phase 1**: スコアリング基盤 + ログ蓄積
- **Phase 2（現在）**: Audit Engine — 構造的意味監査パイプライン（detector / calculator / decider）
- **Phase 3**: referenceセット設計（Human-golden / Cross-model / Self-baseline）
- **Phase 4**: Phase Map可視化 + パターン分析

---

## 理論背景

- [無意識的重力仮説（UGHer）](https://note.com/kamo6798/n/n5aeea478d12e)
- [RPE入門](https://note.com/kamo6798/n/n99cbb5307e13)
- [SVPとRPEの実践メモ](https://note.com/kamo6798/n/nb45c716a2c61)
- [ugh3-metrics-lib](https://github.com/Yuu6798/ugh3-metrics-lib)

---

## License

MIT License © 2025 Yuu6798

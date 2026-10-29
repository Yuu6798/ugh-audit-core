# REST API + MCP サーバー設計

`ugh_audit/server.py` は REST API と MCP サーバーを統合した FastAPI
アプリケーション。`python -m ugh_audit.mcp_server` も同じ認証付きアプリを起動する。

既定は招待制Bearer認証。キー発行・所有者分離・上限・配備条件は
[`invite_beta.md`](invite_beta.md) を参照。OAuthは未実装。

## 既存公開URLの設定記録 (Railway)

以下は既存環境の設定記録。本書の schema 4 仕様が公開環境に反映され、API疎通・
DB移行まで成功したかは別途確認を要する。

```
https://ugh-audit-core-production.up.railway.app
```

| パス | 用途 |
|------|------|
| `/health` | ヘルスチェック |
| `/docs` | Swagger UI（API ドキュメント、ブラウザで操作可能） |
| `/api/audit` | POST: 意味監査 |
| `/api/history` | GET: 監査履歴 |
| `/mcp` | MCP エンドポイント（Bearer設定対応クライアントから接続） |

## ローカル起動方法

先に招待台帳と環境変数を設定する。認証設定がなければ503となる。

```bash
# REST API + MCP 統合サーバー
uvicorn ugh_audit.server:app --host 127.0.0.1 --port 8000

# 同じ認証付き統合アプリ
python -m ugh_audit.mcp_server --port 8000
```

## エンドポイント

| メソッド | パス | 説明 |
|---------|------|------|
| POST | `/api/audit` | AI 回答を意味監査 |
| GET | `/api/audit/{id}` | ID 指定で監査結果を 1 件取得 |
| GET | `/api/history` | 直近の監査履歴 |
| GET | `/api/session/{session_id}` | セッション単位の集計サマリー |
| GET | `/api/drift` | 要件充足率・判定の履歴（旧数値は診断） |
| POST | `/mcp` | MCP Streamable HTTP |
| GET | `/health` | ヘルスチェック (`{"status": "ok"}`) |

## API 出力契約 (schema_version: 4.0.0)

対象は明示要件の **記述充足**。要件の正しさ、現実の実行、回答全体の品質・真実性は
判定しない。`decision_scope="instruction_coverage_v2"`、
`metrics_scope="semantic_coverage_only"`。人が用意した
`question_meta.core_propositions`（互換入力は `original_core_propositions`）を使用する。
AI 生成の要件は草案であり、常に `requires_review=true`。
`assessment_context` は照合に用いた要件メタデータを示す。

| 要件別状態 | 最終 verdict | C / hit_rate |
|---|---|---|
| 1件以上の要件がすべて fulfilled | accept | 1.0 / 全件比率 |
| unknown なし、missing あり | rewrite | fulfilled / 全要件数 |
| unknown あり、要件なし・不正、照合不能 | degraded | null |

`missing` は absence または contradiction。欠落と保留が同時にある場合も最終判定は
`degraded`。`requirement_verdict` は missing があれば `incomplete`、全件充足なら
`complete`、それ以外は `undetermined`。この契約では最終 `regenerate` を生成しない。

上位の `S`・`delta_e`・`quality_score` は常に null。旧値・旧 verdict・旧 hit_sources
は `legacy_metrics` に残す。上位 `structural_gate.binding=false`、構造値・旧 advisory は
充足判定を上下させない。`verdict_advisory` は最終 verdict と一致し、`advisory_flags=[]`。

### 判定確定の例（主要フィールドの抜粋）

以下は仕様説明用の抜粋であり、完全な検証済みレスポンスではない。
実際の `semantic_matching` には全要件の本文・状態・引用・理由・モデル・原応答・
プロンプトハッシュ等が含まれ、受信側も検証する。

```json
{
  "schema_version": "4.0.0",
  "decision_scope": "instruction_coverage_v2",
  "metrics_scope": "semantic_coverage_only",
  "S": null,
  "C": 0.5,
  "delta_e": null,
  "quality_score": null,
  "verdict": "rewrite",
  "requirement_verdict": "incomplete",
  "requires_review": true,
  "hit_rate": "1/2",
  "hit_sources": {"source": "llm_semantic", "fulfilled": 1, "missing": 1, "unknown": 0, "total": 2},
  "structural_gate": {"f1": 0.0, "f2": 0.5, "f3": 0.0, "f4": 0.0, "gate_verdict": "warn", "primary_fail": "f2", "binding": false},
  "mode": "computed",
  "metadata_source": "inline",
  "is_reliable": true,
  "computed_components": ["semantic_matching", "C"],
  "missing_components": ["S", "delta_e", "quality_score"]
}
```

`is_reliable=true` は全要件の照合が確定したことを表す。欠落が確定した rewrite にも
true となり、真実性や承認を意味しない。照合が確定した AI 草案メタデータなら mode は
`computed_ai_draft` であり、accept でも `requires_review=true`。

### メタデータの由来と mode

`metadata_source` は要件メタデータの由来であり、照合の確定状態を表す `mode` とは別の値。

| metadata_source | 意味 |
|---|---|
| `inline` | 呼び出し元が渡したメタデータ |
| `llm_generated` | opt-in の自動生成で不足要件を補った AI 草案 |
| `fallback` | メタ生成器が `_is_fallback` を付けた未検証の代替メタデータ |
| `none` | メタデータなし |

`auto_generate_meta=true` は要件がない・不正な場合の生成を許可する。
生成結果が `fallback` の場合、最終 verdict と mode は `degraded`、
`degraded_reason` に `metadata_fallback_unverified` を含み、`C` / `hit_rate` は null、
`is_reliable=false`、`requires_review=true` となる。代替要件に本文があっても意味照合の
承認には使わず、新規の通常履歴には保存しない (`saved_id=null`)。

### degraded と制限

認証情報不足、API障害、時間切れ、不正JSON、架空引用、モデル不一致等を充足にしない。
保留が1件でもあれば C / hit_rate は null、`is_reliable=false`、`requires_review=true`。
要件を分母から除いて合格にしない。要件準備後に25秒の照合予算を開始し、1要件は残り時間
以下・最大10秒。任意の埋め込み・旧診断より先に実行する。上限は16要件、回答32,000文字、
1要件2,000文字。詳細は [`semantic_matching_v2.md`](semantic_matching_v2.md)。

### 任意の旧診断

`legacy_metrics`、`structural_gate`、grv、response_mode_signal、mode_conditioned_grv は
非拘束の診断である。SBert 未導入・モデル起動失敗でこれらが取得できなくても、確定した
要件照合結果を取り消さない。旧数値の式・閾値は [`formulas.md`](formulas.md) に保存する。
それらを加えると単純な LLM 監査より有用になるという主張は、この API 契約に含まれない。

### schema 3 以前からの破壊的変更

schema 3 は旧数値判定に意味ゲートを追加する方式だった。schema 4 は要件照合自体を
最終判定とし、上位の旧品質数値を廃止する。旧 C / ΔE / gate を承認条件に使う
クライアントは更新する。remote proxy も契約・要件・引用・モデル・原応答を再検証し、
旧サーバーの未検証 accept を degraded として扱う。

### GET /api/audit/{id}

ID 指定で 1 件取得。存在しない場合は `404`。

### GET /api/session/{session_id}

`total` は保存件数、`verified_total` は現行契約で照合を再検証できた件数、
`unverified_total` はそれ以外。verified は欠落確定も含み、合格件数ではない。
上位の `avg_S` / `avg_delta_e` / `min_delta_e` / `max_delta_e` /
`avg_quality_score` は null。旧数値の集約は `legacy_summary` に分離する。

### GET /api/drift?limit=100

作成日時順の要件充足率・判定と `source_schema_version` を返す。現在の上位
S / ΔE / quality は null。旧品質の時系列として解釈しない。

### DB 保存・履歴ガード

新規 degraded 結果は通常の computed 履歴に保存せず `saved_id=null`。
確定結果は要件メタデータ、回答、意味照合の全根拠、契約・版を併せて保存する。
`get` / `history` / `session` / `drift` も同じガードを通す。

旧版の記録、文脈不足、本文や要件と整合しない照合結果は、現在の表示を degraded に
する。保存済みの原値は `legacy_metrics` へ残し、原レコードを書き換えない。
`source_schema_version` で原版を区別する。旧 accept の再取得を現行の合格にしない。

## 設計方針

- MCP は `stateless_http=True` で動作（マルチワーカー/LB 対応）
- `session_id` を REST/MCP 両方でオプショナルに受け付け、会話単位の分析に対応

## audit_runs テーブル追加カラム

永続化層で保存される追加カラム:

| カラム | 型 | 説明 |
|--------|-----|------|
| `metadata_source` | TEXT | `inline` / `llm_generated` / `fallback` / `none`（新規 degraded は通常履歴へ保存しない） |
| `generated_meta` | TEXT | LLM 生成メタの JSON（llm_generated 時のみ） |
| `hit_sources` | TEXT | 要件別 fulfilled / missing / unknown の JSON |
| `semantic_matching` / `question_meta` | TEXT | 全照合根拠・要件文脈の JSON |
| `legacy_metrics` | TEXT | 旧指標の非拘束診断 JSON |
| `schema_version` / `decision_scope` / `metrics_scope` | TEXT | 保存時の版・固定契約・数値範囲 |

## MCP ツール一覧

| ツール名 | 説明 |
|----------|------|
| `audit_answer` | AI 回答を意味監査する |
| `get_audit` | ID 指定で監査結果を 1 件取得 |
| `get_history` | 直近 N 件の監査履歴を取得 |
| `get_session_summary` | セッション単位の集計サマリー |
| `get_drift_history` | 要件充足率・判定の履歴 |

## CLI

```bash
# ID 指定で 1 件取得
python -m ugh_audit.cli get 20

# 直近の監査履歴
python -m ugh_audit.cli history --limit 5

# セッション集計
python -m ugh_audit.cli session <session_id>

# 要件充足率・判定の履歴
python -m ugh_audit.cli drift --limit 50
```

`UGH_AUDIT_DB` 環境変数で DB パスを指定可能。未指定時は `~/.ugh_audit/audit.db`。

## 既存デプロイ構成の記録 (Railway)

| 項目 | 値 |
|------|-----|
| プロバイダ | [Railway](https://railway.app) |
| プロジェクト | independent-courage |
| リージョン | us-west2 |
| ビルド | Dockerfile (python:3.11-slim + PyTorch CPU版) |
| 永続ボリューム | `/data` (ugh-audit-core-data) |
| DB パス | `/data/audit.db` |
| キャッシュ | `/data/embedding_cache.npz`, `/data/meta_cache/` |
| ヘルスチェック | `GET /health` |

### 環境変数 (Railway Variables)

| 変数 | 設定値 | 備考 |
|------|--------|------|
| `ANTHROPIC_API_KEY` | サーバー側の秘密設定 | 必須の意味照合と opt-in メタ生成用。入力メタデータから読まない |
| `UGH_AUDIT_DB` | `/data/audit.db` | Dockerfile で定義 |
| `UGH_AUDIT_CACHE_DIR` | `/data` | Dockerfile で定義 |
| `UGH_META_CACHE_DIR` | `/data/meta_cache` | Dockerfile で定義 |
| `PORT` | (Railway が自動設定) | uvicorn が参照 |

### 既存構成のデプロイ手順（この作業では未実施）

1. main ブランチに push → Railway が自動検出
2. Dockerfile でビルド (~3分)
3. ヘルスチェック (`/health`) 通過 → デプロイ完了
4. 永続ボリュームにより再デプロイしてもDBは保持

## 関連ファイル

- `ugh_audit/server.py` — REST + MCP 統合サーバー
- `ugh_audit/mcp_server.py` — MCP スタンドアロン
- `ugh_audit/cli.py` — DB 参照 CLI
- `ugh_audit/collector/audit_collector.py` — audit + save パイプライン
- `ugh_audit/storage/audit_db.py` — SQLite 永続化
- `Dockerfile` — Railway デプロイ用
- `railway.toml` — Railway 設定 (ヘルスチェック等)
- `.dockerignore` — イメージ軽量化
- 現行判定契約: [`semantic_matching_v2.md`](semantic_matching_v2.md)
- 旧数値の定義: [`formulas.md`](formulas.md)

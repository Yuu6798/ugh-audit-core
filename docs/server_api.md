# REST API + MCP サーバー設計

`ugh_audit/server.py` は REST API と MCP サーバーを統合した FastAPI
アプリケーション。`ugh_audit/mcp_server.py` は MCP スタンドアロン版。

## 公開URL (Railway)

本番環境は Railway にデプロイ済み。PC を切っても常時稼働。

```
https://ugh-audit-core-production.up.railway.app
```

| パス | 用途 |
|------|------|
| `/health` | ヘルスチェック |
| `/docs` | Swagger UI（API ドキュメント、ブラウザで操作可能） |
| `/api/audit` | POST: 意味監査 |
| `/api/history` | GET: 監査履歴 |
| `/mcp` | MCP エンドポイント（ChatGPT Connectors 等から接続） |

## ローカル起動方法

```bash
# REST API + MCP 統合サーバー
uvicorn ugh_audit.server:app --host 0.0.0.0 --port 8000

# MCP スタンドアロン
python -m ugh_audit.mcp_server --port 8000
```

## エンドポイント

| メソッド | パス | 説明 |
|---------|------|------|
| POST | `/api/audit` | AI 回答を意味監査 |
| GET | `/api/audit/{id}` | ID 指定で監査結果を 1 件取得 |
| GET | `/api/history` | 直近の監査履歴 |
| GET | `/api/session/{session_id}` | セッション単位の集計サマリー |
| GET | `/api/drift` | ΔE 時系列データ |
| POST | `/mcp` | MCP Streamable HTTP |
| GET | `/health` | ヘルスチェック (`{"status": "ok"}`) |

## API 出力フォーマット (schema_version: 2.0.0)

### computed モード（本計算完了時）

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
  "degraded_reason": [],
  "mode_affordance": {
    "primary": "critical",
    "secondary": ["analytical"],
    "closure": "qualified",
    "action_required": false
  },
  "response_mode_signal": {
    "status": "available",
    "primary_mode": "critical",
    "primary_score": 1.0,
    "secondary_scores": {"analytical": 0.5},
    "closure_expected": "qualified",
    "closure_score": 1.0,
    "action_required": false,
    "action_score": null,
    "overall_score": 0.8889,
    "matched_moves": ["inspect_premise", "reframe_if_needed"],
    "missing_moves": ["identify_mechanism_or_condition"],
    "evidence": ["primary(critical): matched inspect_premise, reframe_if_needed"],
    "signal_version": "v1.0"
  },
  "grv": null
}
```

### degraded モード（メタデータ不足時）

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
  "degraded_reason": ["question_meta_missing", "detection_skipped"],
  "mode_affordance": null,
  "response_mode_signal": {"status": "not_available"},
  "grv": null
}
```

### mode_affordance / response_mode_signal

`mode_affordance` は質問が期待する応答形式。`response_mode_signal` は回答の適合度信号。
詳細: [`mode_affordance.md`](mode_affordance.md)

- canonical reviewed (102問) にある question_id では自動的に `status: "available"` が返る
- metadata 不在時は `status: "not_available"` (既存監査は継続)
- verdict / S / C / delta_e / quality_score には影響しない非破壊信号

### gate_verdict の値

| gate_verdict | 条件 | 意味 |
|---|---|---|
| pass | fail_max == 0.0 AND f4 ≠ None | 構造完全 |
| warn | 0.0 < fail_max < 1.0 AND f4 ≠ None | 部分的な構造欠陥 |
| fail | fail_max ≥ 1.0 | 構造的に破綻 |
| incomplete | f4 == None | f4 未計算 |

### is_reliable フラグ

消費者（LLM 等）が結果を信頼してよいかを示す bool。

```
is_reliable = mode == "computed"
              AND verdict in {"accept", "rewrite", "regenerate"}
              AND gate_verdict != "fail"
```

`false` になるケース: degraded モード、gate_verdict=fail（構造的破綻）。

### GET /api/audit/{id}

ID 指定で 1 件取得。存在しない場合は `404`。

### GET /api/session/{session_id}

```json
{
  "session_id": "abc-123",
  "total": 5,
  "avg_delta_e": 0.1234,
  "min_delta_e": 0.0,
  "max_delta_e": 0.35,
  "avg_quality_score": 4.5
}
```

### GET /api/drift?limit=100

ΔE 時系列を `created_at ASC` で返す。品質推移の可視化に使用。

```json
[
  {"created_at": "2026-04-08T21:29:37+00:00", "S": 0.9375, "C": 0.667, "delta_e": 0.0396, "quality_score": 4.8414, "verdict": "accept"},
  ...
]
```

### DB 保存ポリシー

degraded 結果は DB に保存しない (`saved_id=null`)。
ベースライン汚染を防止するため。

## 設計方針

- MCP は `stateless_http=True` で動作（マルチワーカー/LB 対応）
- `session_id` を REST/MCP 両方でオプショナルに受け付け、会話単位の分析に対応

## audit_runs テーブル追加カラム

永続化層で保存される追加カラム:

| カラム | 型 | 説明 |
|--------|-----|------|
| `metadata_source` | TEXT | `inline` / `llm_generated` / `none` |
| `generated_meta` | TEXT | LLM 生成メタの JSON（llm_generated 時のみ） |
| `hit_sources` | TEXT | 命題ごとの判定結果 JSON（`{"0": "tfidf", "1": "miss"}`） |

## MCP ツール一覧

| ツール名 | 説明 |
|----------|------|
| `audit_answer` | AI 回答を意味監査する |
| `get_audit` | ID 指定で監査結果を 1 件取得 |
| `get_history` | 直近 N 件の監査履歴を取得 |
| `get_session_summary` | セッション単位の集計サマリー |
| `get_drift_history` | ΔE 時系列データ |

## CLI

```bash
# ID 指定で 1 件取得
python -m ugh_audit.cli get 20

# 直近の監査履歴
python -m ugh_audit.cli history --limit 5

# セッション集計
python -m ugh_audit.cli session <session_id>

# ΔE 時系列
python -m ugh_audit.cli drift --limit 50
```

`UGH_AUDIT_DB` 環境変数で DB パスを指定可能。未指定時は `~/.ugh_audit/audit.db`。

## デプロイ構成 (Railway)

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
| `ANTHROPIC_API_KEY` | (設定済み) | auto_generate_meta 用 |
| `UGH_AUDIT_DB` | `/data/audit.db` | Dockerfile で定義 |
| `UGH_AUDIT_CACHE_DIR` | `/data` | Dockerfile で定義 |
| `UGH_META_CACHE_DIR` | `/data/meta_cache` | Dockerfile で定義 |
| `PORT` | (Railway が自動設定) | uvicorn が参照 |

### デプロイの流れ

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
- verdict 判定の詳細: [`formulas.md`](formulas.md)

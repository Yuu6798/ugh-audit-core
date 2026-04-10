# REST API + MCP サーバー設計

`ugh_audit/server.py` は REST API と MCP サーバーを統合した FastAPI
アプリケーション。`ugh_audit/mcp_server.py` は MCP スタンドアロン版。

## 起動方法

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
| GET | `/api/history` | 直近の監査履歴 |
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
  "degraded_reason": []
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
  "degraded_reason": ["question_meta_missing", "detection_skipped"]
}
```

### gate_verdict の値

| gate_verdict | 条件 | 意味 |
|---|---|---|
| pass | fail_max == 0.0 AND f4 ≠ None | 構造完全 |
| warn | 0.0 < fail_max < 1.0 AND f4 ≠ None | 部分的な構造欠陥 |
| fail | fail_max ≥ 1.0 | 構造的に破綻 |
| incomplete | f4 == None | f4 未計算 |

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

## 関連ファイル

- `ugh_audit/server.py` — REST + MCP 統合サーバー
- `ugh_audit/mcp_server.py` — MCP スタンドアロン
- `ugh_audit/collector/audit_collector.py` — audit + save パイプライン
- `ugh_audit/storage/audit_db.py` — SQLite 永続化
- verdict 判定の詳細: [`formulas.md`](formulas.md)

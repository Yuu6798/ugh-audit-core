# トラブルシューティング

ugh-audit-core を運用する際に遭遇しやすい症状と対処。

## SBert モデルダウンロードが進まない / `leak_check n=0` / `anchor_alignment` が全行 None

**典型的な原因:** proxy 環境変数が SBert (sentence-transformers) の
HuggingFace モデルダウンロードを阻害している。

**診断:**

```bash
env | grep -iE "^(http|https)_proxy"
```

`HTTP_PROXY` / `HTTPS_PROXY` / `http_proxy` / `https_proxy` のいずれかに
到達不能なアドレス（例: `http://127.0.0.1:9`）が設定されている場合、
モデルダウンロード失敗で cascade layer が silent fallback し、
`anchor_alignment=None` が大量発生しやすくなる。結果として C の上方補正が
効かず、スコアが保守的になる。

**復旧手順:**

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

## 診断ルール（早期の環境疑い）

以下のいずれかを観測した場合、cascade が silent degrade している可能性が
高い。計算ロジックの問題として深掘りする前に環境を疑う:

- calibration / 検証スクリプトで `leak_check n=0`
- HA48 / HA63 評価で `anchor_alignment` が全行 `None`
- `cascade_matcher.get_shared_model()` が常に `None` を返している
  （SBert 未インストール、モデルロード失敗、再試行上限到達など）

`UGH_AUDIT_EMBED_CACHE_DISABLE` は埋め込みキャッシュの無効化フラグであり、
shared model のロード可否には影響しない。

cascade が無効化された状態でも core pipeline（detect tier 1 + calculator +
decider）は完走するため、出力自体はエラーにならず verdict は出る。
ただし C が上方修正されないので scoring が保守的になる。

2 層分離の詳細: [`cascade_design.md#core-vs-cascade`](cascade_design.md#core-vs-cascade)。

## `verdict == "degraded"` が常に返る

**典型的な原因:** `question_meta` が未提供 or `core_propositions` が空。

**診断:**

```bash
# response を見る
# NOTE: -H 'Content-Type: application/json' が必須。
# 省略すると curl は application/x-www-form-urlencoded で送信し、
# FastAPI の AuditRequest バリデーションが 422 を返して診断目的を果たせない
curl -X POST http://localhost:8000/api/audit \
  -H 'Content-Type: application/json' \
  -d '{"question":"...","response":"..."}' \
  | jq '.errors, .degraded_reason'
```

- `question_meta_missing`: question_meta を渡す、または `auto_generate_meta=true` を指定
- `core_propositions_missing`: 生成 meta に core_propositions が含まれていない
- `auto_generate_fallback`: LLM 呼び出しが失敗し heuristic fallback が発火

LLM 生成を使うなら `ANTHROPIC_API_KEY` 環境変数が必要。
詳細: [`metadata_pipeline.md`](metadata_pipeline.md)

## 読み取り専用コンテナ / サーバーレスで起動に失敗する

デフォルトの DB / cache path (`~/.ugh_audit/`) が書き込み不可の場合、
以下の環境変数で tmp へ振る:

```bash
export UGH_AUDIT_DB=/tmp/audit.db
export UGH_AUDIT_CACHE_DIR=/tmp/ugh_cache
```

環境変数の全リスト: [`server_api.md`](server_api.md) §環境変数。

## HA48 regression test が CI で fail する

**典型的な原因:** pipeline の意図した変更で baseline が drift している。

対処:

1. ローカルで `python analysis/calibrate_phase_e_thresholds.py` 相当の
   再校正を走らせ、snapshot を更新するか判断
2. 意図した変更なら `analysis/ha48_regression_check.csv` を refresh し、
   **同じ PR に** CSV 更新コミットを添える（[`validation.md`](validation.md)
   §「測定精度履歴」に version 差分を記録）
3. 意図しない drift なら原因 PR を特定して revert

詳細: [`tests/test_ha48_regression.py`](../tests/test_ha48_regression.py) docstring。

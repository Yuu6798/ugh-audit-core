# トラブルシューティング

schema 4 は明示要件の記述充足を判定する。上位 `S`・`delta_e`・`quality_score` が
null なのは仕様。`C` は全件解決時の fulfilled/全要件数で、保留があれば null。
旧数値は `legacy_metrics` に分離する。契約は
[`semantic_matching_v2.md`](semantic_matching_v2.md) を参照。

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
`anchor_alignment=None` が大量発生しやすくなる。旧診断の C の回収値に影響するが、
上位の意味照合 C や最終 verdict の根拠には使わない。

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

cascade の無効化や旧診断の失敗は、確認済みの要件充足を取り消さない。
意味照合は要件準備後から別の25秒予算で、任意の埋め込みより先に実行する。

2 層分離の詳細: [`cascade_design.md#core-vs-cascade`](cascade_design.md#core-vs-cascade)。

## `verdict == "degraded"` が常に返る

**典型的な原因:** 要件が未提供・不正、意味照合器が使えない、API障害・期限超過、
引用や応答形式の検証失敗。`semantic_matching` の要件別 reason も確認する。

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

- `requirements_invalid_or_missing`: 有効な `core_propositions` を明示する
- `semantic_checker_unavailable`: サーバー側の `ANTHROPIC_API_KEY` と判定器設定を確認する
- `deadline_exceeded` / `semantic_checker_error`: 通信・認証・残り時間を確認する
- `invalid_semantic_result`: 引用・モデル・JSON等が検証を通っていない。充足として再利用しない
- `metadata_fallback_unverified`: 代替生成メタデータは未確認のため承認しない

意味照合にも `ANTHROPIC_API_KEY` が必要。AI 草案生成を使う場合は要件自体のレビューが
必要で、全件 fulfilled でも `requires_review=true`。古い数値 accept へフォールバックしない。
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

1. 同じ回答・要件で再実行し、実装変更による差分と意図を確認する。
   回帰テストを通すためだけに再校正や人手評価 `O` の変更を行わない
2. 意図した機械出力の変更なら旧 CSV を完全保存し、現行
   `analysis/ha48_regression_check.csv` と版の記録・テストを更新する。
   [`ha48_snapshot_versions.md`](ha48_snapshot_versions.md) のように差分と根拠を残す。
   既知データの再計算を、新しい独立評価や人手検証として報告しない
3. 意図しない drift なら原因 PR を特定して revert

詳細: [`tests/test_ha48_regression.py`](../tests/test_ha48_regression.py) docstring。

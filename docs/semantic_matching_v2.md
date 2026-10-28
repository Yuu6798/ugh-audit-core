# 必須要件の意味照合と最終acceptの条件（schema 3.0）

`ugh_audit.pipeline.run_audit` を共有経路として、REST、ローカルMCP、Pythonの
`audit.audit` とCLIにv2の意味照合を統合する。公開済み環境はこの変更の対象外。

## 最終判定

要件ごとに `fulfilled` / `missing` / `unknown` を保存する。要件がゼロ・不正、
認証情報不足、API障害、時間切れ、不正JSON、架空引用、異なるモデルの応答は
確認済みの充足として扱わない。受信データに書かれた判定値・無効化フラグは読まない。

| 要件照合 | requirement_verdict | 最終判定 |
|---|---|---|
| 全件fulfilled | complete | 従来の判定を維持。拒否をacceptへ格上げしない |
| missingあり、unknownなし | incomplete | acceptをrewriteへ変更。既存のregenerate/degradedは維持 |
| unknownあり | missingもあればincomplete、それ以外undetermined | degraded、is_reliable=false |
| 要件なし・照合不能 | undetermined | degraded、is_reliable=false |

構造ゲートも `f1`〜`f4` が有限の0〜1で、ラベルと整合している必要がある。
未計算・不正値・不整合ならdegraded、failならacceptを許可しない。
従来結果の `is_reliable` がTrueでない場合も、acceptにしない。
`verdict_advisory`、soft rescue、remote proxy、保存・履歴の再取得によって
acceptが復活しないよう、共通の最終ガードを通す。

## 新しいフィールド

- `semantic_matching`: 要件ID、原文、結果、引用、理由、使用量、モデル、
  プロンプトSHA256、`coverage`、各判定数、missing/unknown IDs。
- `requirement_verdict`: complete / incomplete / undetermined。
- `requires_review`: 充足を確認できない、または最終判定がaccept以外ならtrue。
- `legacy_metrics`: ガード前の数値・判定。公開上の最終acceptの根拠にはしない。

S/C/ΔE/qualityの計算式とdetectorは変更しない。照合が全て判定できた場合も、
上位のCとhit_sourcesは従来の決定的処理の診断値である。LLM充足率は
`semantic_matching.coverage` へ分ける。保留があれば上位C/ΔE/quality/hit_rateを
nullにし、旧値はlegacy_metricsへ残す。要件の分母を減らして保留を隠さない。

## モデル・認証・制限

評価済みv2と同じ `claude-sonnet-4-6`、SYSTEM、4項目のJSON schema、
temperature=0、max_tokens=1024を使用する。モードは `instruction_coverage_v2`。
APIへ送るのは固定モード・単一要件・回答で、質問の他の要件を混入しない。

サーバー側の `ANTHROPIC_API_KEY` を設定する。特定のローカルenvファイルは
本体へ埋め込まない。標準ライブラリのHTTPSを使い、TLS検証、固定の公式ホスト、
リダイレクト禁止、再試行なし。v2照合にAnthropic SDKは不要。
キーがなくても従来の未確認acceptへフォールバックしない。

1回答の要件上限16、回答32,000文字、1要件2,000文字。
上限超過は自動で一部を捨てず、確認不能として扱う。
パイプライン開始から意味照合に25秒の予算を設け、1要件の通信には残り時間以下・
最大10秒を渡す。期限を超えた残りの要件はunknownとする。OSのDNS処理や
呼び出し元が注入した同期処理を強制終了する仕組みではない。

`run_audit(..., semantic_judge=...)` は内部の依存注入用。
REST/MCP入力には結果や判定器を指定する欄を設けない。Pythonの`audit()`は
既定で環境から判定器を作り、明示的な`semantic_judge=None`も照合不能となる。

## 保存と互換性

DBは新しい意味照合フィールドを追加保存する。従来どおりdegradedの新規監査は
通常のcomputed履歴へ保存しない。未確認の古い履歴を取得した場合は、現在の判定を
degradedとし、保存済みの原値をlegacy_metricsに表示する。原レコードは書き換えない。
現在のsession平均から未確認値を除き、従来の平均はlegacy_summaryへ分ける。

remote proxyは意味照合のID・本文・判定数・引用・モデル・原応答・終了状態と
構造値を再検証する。旧サーバーが意味照合なしに返すacceptはdegradedとなる。
そのためproxyと接続先はこの契約をそろえて更新する必要がある。

## 検証と限界

`python -m pytest -q --tb=short` と `ruff check .` を実行する。
pytestは外部DNSを遮断し、内部の明示的な判定器fixtureと、既知192要件の
実モデル応答のリプレイで、ネットワークなしに判定契約を検証する。
リプレイは新たな独立性能評価ではない。数値のHA20回帰は従来の純粋な
detect/calculate/decide経路を対象とし、公開APIのacceptとは区別する。

v2の性能根拠は短い日本語の業務指示・制約の記載監査である。完成したメール等の
成果物が禁止事項に違反していないかを検査する課題へ、そのまま一般化しない。
正解要件の妥当性、LLMの判定誤り、すべての脆弱性の解消を保証するものでもない。

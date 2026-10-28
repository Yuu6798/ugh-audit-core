# 明示要件の記述充足契約（schema 4.0.0）

`ugh_audit.pipeline.run_audit` を共有経路として、REST、ローカルMCP、Pythonの
`audit.audit` とCLIにv2の意味照合を統合する。公開済み環境はこの変更の対象外。

判定対象は、人が定義した単一の指示・制約が回答文に伝えられているかである。
要件自体の正しさ、現実の業務実行、一般的な回答品質や真実性を認定しない。
禁止指示を伝える課題と、完成した成果物が禁止事項に違反しないかという課題を区別する。
`decision_scope="instruction_coverage_v2"`、`metrics_scope="semantic_coverage_only"`。

## 最終判定

要件ごとに `fulfilled` / `missing` / `unknown` を保存する。要件がゼロ・不正、
認証情報不足、API障害、時間切れ、不正JSON、架空引用、異なるモデルの応答は
確認済みの充足として扱わない。受信データに書かれた判定値・無効化フラグは読まない。

| 要件照合 | requirement_verdict | 最終判定 |
|---|---|---|
| 有効な要件が1件以上あり全件fulfilled | complete | accept |
| missingあり、unknownなし | incomplete | rewrite |
| unknownあり | missingもあればincomplete、それ以外undetermined | degraded、is_reliable=false |
| 要件なし・照合不能 | undetermined | degraded、is_reliable=false |

旧構造ゲートは `binding=false`。旧数値・構造の拒否や診断の欠落は、確認済みの
要件充足を取り消さない。逆に、旧 accept・advisory・soft rescue は欠落や保留を
充足へ変えない。上位 `verdict_advisory` は最終 verdict と一致する。
この契約で `regenerate` は生成しない。`requirement_verdict` は missing があれば
`incomplete` だが、unknown もある場合の最終 verdict は `degraded` を優先する。

## 新しいフィールド

- `semantic_matching`: 要件ID、原文、結果、引用、理由、使用量、モデル、
  プロンプトSHA256、`coverage`、各判定数、missing/unknown IDs。
- `requirement_verdict`: complete / incomplete / undetermined。
- `requires_review`: 保留・欠落・AI草案メタデータならtrue。
- `legacy_metrics`: 旧診断の数値・判定。最終判定の根拠にはしない。

上位 `S`・`delta_e`・`quality_score` は常に null。全要件が解決している場合だけ
`C=fulfilled/全要件数`、`hit_rate` はその比率、`hit_sources` は要件別の状態となる。
1件でも保留なら `C`・`hit_rate` は null。要件の分母を減らして保留を隠さない。
`is_reliable` は照合が確定したことを示し、回答の真実性の保証ではない。
AI草案の `computed_ai_draft` は、全件 fulfilled でも `requires_review=true`。

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
要件準備後から意味照合に25秒の予算を設け、任意の埋め込み・旧診断より先に実行する。
旧診断の起動遅延で意味照合の予算を消費しない。1要件の通信には残り時間以下・
最大10秒を渡す。期限を超えた残りの要件はunknownとする。OSのDNS処理や
呼び出し元が注入した同期処理を強制終了する仕組みではない。

`run_audit(..., semantic_judge=...)` は内部の依存注入用。
REST/MCP入力には結果や判定器を指定する欄を設けない。Pythonの`audit()`は
既定で環境から判定器を作り、明示的な`semantic_judge=None`も照合不能となる。

## 保存と互換性

schema 3 の「旧数値判定に拒否ガードを追加する」方式からの破壊的変更である。
旧数値を上位の承認条件として読むクライアントは更新する必要がある。

DBは新しい意味照合フィールドを追加保存する。従来どおりdegradedの新規監査は
通常のcomputed履歴へ保存しない。未確認の古い履歴を取得した場合は、現在の判定を
degradedとし、保存済みの原値をlegacy_metricsに表示する。原レコードは書き換えない。
履歴を現在の要件・回答・契約・根拠と照合できない場合は degraded とする。
`source_schema_version` は保存時の版を示す。session の上位 S/ΔE/quality 平均は null、
旧数値の平均は `legacy_summary` へ分ける。`verified_total` は欠落確定も含む
照合検証済み件数であり、合格件数ではない。

remote proxyは意味照合のID・本文・判定数・引用・モデル・原応答・終了状態と
固定契約を再検証する。構造値は非拘束の診断として扱う。
旧サーバーが意味照合なしに返すacceptはdegradedとなる。
そのためproxyと接続先はこの契約をそろえて更新する必要がある。

Python/CLI の修復順序は `rewrite` の確認済み欠落ごとに `ADD_PROPOSITION` を置き、
`STOP_REWRITE` で終える。旧構造値や品質値から修復を追加しない。unknown が残る
`degraded` では、欠落が同時に記録されていても自動修復を提案しない。

## 検証と限界

`python -m pytest -q --tb=short` と `ruff check .` を実行する。
pytestは外部DNSを遮断し、内部の明示的な判定器fixtureと、既知192要件の
実モデル応答のリプレイで、ネットワークなしに判定契約を検証する。
リプレイは新たな独立性能評価ではない。数値のHA20回帰は従来の純粋な
detect/calculate/decide経路を対象とし、公開APIのacceptとは区別する。

v2の性能根拠の対象は短い日本語の業務指示・制約の記載監査である。完成したメール等の
成果物が禁止事項に違反していないかを検査する課題へ、そのまま一般化しない。
正解要件の妥当性、LLMの判定誤り、すべての脆弱性の解消を保証するものでもない。
旧 UGH 診断の追加価値や、単純な LLM 監査に対する優位性をここからは主張しない。

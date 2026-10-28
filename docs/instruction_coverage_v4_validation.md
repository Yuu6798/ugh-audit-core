# schema 4: 変更と検証の範囲

明示された業務指示を正しく伝えた回答が、旧UGHの表層フラグ・数値で拒否される
問題を修正する。合否は `instruction_coverage_v2` の検証済み要件照合に統一する。
旧数値の妥当性やLLM単体への精度の上乗せを認定する変更ではない。

## 互換性

要件が1件以上あり全件fulfilledならaccept、保留なしでmissingがあればrewrite、
unknown・無効な証拠・文脈・照合障害があればdegraded。
上位Cは解決済み要件の `fulfilled / total`、保留時はnull。S・delta_e・quality_scoreは
常にnull、旧値は `legacy_metrics`、構造ゲートは `binding=false`。
hit_rateは数値ではなく `"2/3"` のような分数文字列で、保留時はnull。

利用側はnullとscopeを扱い、旧ΔE・旧advisoryを承認条件にしない。
AI生成要件のcompleteでもrequires_review=true。旧履歴を現在のacceptへ昇格させず、
原レコードと診断値を保持する。要件抽出の網羅性、事実の正しさ、一般品質、現実の
作業実施はこのacceptの対象外。
詳細は[判定契約](semantic_matching_v2.md)と[API](server_api.md)を参照。

## 保存したモデル評価

[原応答・Gold・条件・検算器](../analysis/instruction_coverage_v4/README.md)を保存した。
モデル評価時のsourceは `3e0a2897ccd1dd2e16b90032d0cec70c299251a2`。

| 検証 | 結果 |
|---|---|
| 評価当時の全体回帰 | 965 passed、失敗・スキップ0 |
| 既知256回答・896要件の再生 | 誤accept 0、誤拒否99→1、UGH追加拒否98件を除去 |
| 固定後の新規合成80回答・280要件、実API | 誤accept/誤拒否0、要件280/280、欠落種別40/40 |

既知256回答は開発・診断用の再生で、新たな独立評価には数えない。
新規GoldもLLM作成・別担当の盲検照合で、新たな人手評価ではない。
新規B比較は同じ280照合行の集約で、独立した一括LLM監査を再実行していない。
旧UGH数値の全用途への否定や、未知の全業務での無誤りへ一般化しない。

## PR準備中のセルフレビューと追加修正

モデル評価後に次を修正し、それぞれに再現テストを追加した。
この後続HEADを新しい未見80回答で評価したとは主張しない。
後続修正の全体回帰は **1,053 passed、失敗・エラー・スキップ0**。
実SBertを使用し、記録は保存証拠の `regression/pr-head.xml` に分離した。

- 複数workerによるSQLite初期化のread→write競合を、schema読取前の
  `BEGIN IMMEDIATE`で直列化。既存writerの解放待機も確認する。
- 深い任意メタデータがdeepcopyでHTTP500を起こす経路を有界コピーに変更。
  正常な要件は保持し、canonical要件自体が不正なら保留とする。
- Provider外側JSONの重複キー・非有限定数を拒否。
- v4 remote envelopeのmode/verdict/reliability/Cとproof集計の不整合を拒否。
  POSTだけでなくGET履歴でも適用し、不適格な結果が再取得で昇格しないようにする。
- 証拠行に明示された非200のHTTP状態を、既知の充足として受理しない。
  正当なunknown結果では、他の既知行・根拠・unknown_sourceを保持する。

旧診断の誤警告は合否に使わず、入力・通信・公開契約の不適格とは区別する。
自動要件抽出を含む全工程の比較精度は今回の評価対象外。

## CIと再現

```console
python -m pytest -q --tb=short
ruff check .
python analysis/instruction_coverage_v4/verify_artifacts.py
```

高速CIはPython 3.10/3.11/3.12。追加の `test-sbert` はCPU版torchとSBert依存を導入し、
固定revisionと重みSHA256を検証して実推論する。モデル取得はpytest前に行い、
pytest中は外部DNSを遮断・HF offlineに設定し、スキップ0を要求する。
APIへの接続は回帰に使わず、保存モデル応答と明示的なテスト用判定器を使う。
GitHub ActionsのJUnitをartifactに保存する。mainへのマージは既存Railway自動デプロイに
つながるため、このPR準備では実行しない。

# question_sets

Phase C / UGH Audit で使う質問セットを保存するディレクトリ。

- `ugh-audit-100q-v3-1.jsonl`
  - 102行のJSONL系テキスト
  - 各問に `reference`, `reference_core`, `core_propositions`,
    `acceptable_variants`, `disqualifying_shortcuts` を含む
  - `requires_manual_review`: 質問作成者が付与する外部フラグ。
    自動判定だけでは不十分と判断された問に `true` を設定。
    drafter では severity 計算には影響せず、tier を最低 review に引き上げる

- `q_metadata_structural_draft.jsonl`
  - `scripts/q_metadata_drafter.py` の出力（102行JSONL）
  - 各問に 4要素構造メタデータ + review_tier を付与
  - **review_tier** の意味:
    - `pass` — 自動承認可能。構造的リスクなし
    - `warn` — 目視推奨だが低リスク（ソフトトリアージ）
    - `review` — 人間による確認が必須（ハードトリアージ）
  - 基準値の経緯:
    v1 初期版では review 相当が 71/102、v1 最終版では severity 拡張により
    93/102 まで増加。v2 で review_tier 三段階を導入し review=32 に削減。
    今後のチューニングでは不要な warn の削減が主な改善方向

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Breaking: response schema 4.0.0** — 明示された業務指示の記載充足を合否とする。
  検証済みの全件充足は `accept`、既知の欠落は `rewrite`、保留・照合障害は
  `degraded`。上位 `C` は意味充足率、`S`・`delta_e`・`quality_score` は null。
  旧指標は非拘束の `legacy_metrics` に保持する。
- REST・MCP・Python/CLI・履歴の文脈/引用/モデル再検証、公開envelopeの整合性検査、
  SQLiteの原子的・並行安全なnull移行、任意診断と必須意味照合の隔離を追加。
- PR CIで固定SBertモデルの実推論とスキップ0の全体回帰を実行する。
  [検証と移行上の制限](docs/instruction_coverage_v4_validation.md)、
  [保存した評価原応答と検算器](analysis/instruction_coverage_v4/README.md) を参照。

### Deprecated

- **`AuditCollector` / `SessionCollector`** (`ugh_audit.collector`) — 現行パイプラインでは
  `question_meta` を受け取らないため常に `verdict="degraded"` を返し、実監査に到達
  できない。import 時に `DeprecationWarning` が発生するようになった。**v0.5 で削除予定**。
  - 移行先: REST `POST /api/audit` (`ugh_audit.server:app`) または MCP ツール
    `audit_answer` (`ugh_audit.mcp_server`) — いずれも `question_meta` を受け取り
    実監査を実行する。詳細は [`docs/server_api.md`](docs/server_api.md)。

## [0.3.0]

- 監査エンジン (PoR / ΔE / grv) + REST/MCP サーバー + 永続化層の公開。
- 詳細は `.claude/memory/_index.md` のセッションサマリーを参照。

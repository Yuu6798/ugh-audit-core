# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

"""
ugh_audit/cli.py — 監査DB参照CLI

Usage:
    python -m ugh_audit.cli get <id>
    python -m ugh_audit.cli history [--limit N]
    python -m ugh_audit.cli session <session_id>
    python -m ugh_audit.cli drift [--limit N]
"""
from __future__ import annotations

import argparse
import json
import sys

from .storage.audit_db import AuditDB


def _get_db() -> AuditDB:
    import os
    from pathlib import Path

    db_path = os.environ.get("UGH_AUDIT_DB")
    return AuditDB(db_path=Path(db_path) if db_path else None)


def _print_json(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_get(args: argparse.Namespace) -> None:
    db = _get_db()
    row = db.get_by_id(args.id)
    if row is None:
        print(f"Error: audit_id {args.id} not found", file=sys.stderr)
        sys.exit(1)
    _print_json(row)


def cmd_history(args: argparse.Namespace) -> None:
    db = _get_db()
    rows = db.list_recent(limit=args.limit)
    _print_json(rows)


def cmd_session(args: argparse.Namespace) -> None:
    db = _get_db()
    summary = db.session_summary(args.session_id)
    _print_json(summary)


def cmd_drift(args: argparse.Namespace) -> None:
    db = _get_db()
    rows = db.drift_history(limit=args.limit)
    _print_json(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ugh_audit.cli",
        description="UGH Audit DB 参照ツール",
    )
    sub = parser.add_subparsers(dest="command")

    p_get = sub.add_parser("get", help="ID指定で1件取得")
    p_get.add_argument("id", type=int, help="監査結果のID")

    p_hist = sub.add_parser("history", help="直近の監査履歴")
    p_hist.add_argument("--limit", type=int, default=20, help="取得件数")

    p_sess = sub.add_parser("session", help="セッション集計")
    p_sess.add_argument("session_id", help="セッションID")

    p_drift = sub.add_parser("drift", help="ΔE時系列")
    p_drift.add_argument("--limit", type=int, default=100, help="取得件数")

    args = parser.parse_args()

    commands = {
        "get": cmd_get,
        "history": cmd_history,
        "session": cmd_session,
        "drift": cmd_drift,
    }

    if args.command not in commands:
        parser.print_help()
        sys.exit(1)

    commands[args.command](args)


if __name__ == "__main__":
    main()

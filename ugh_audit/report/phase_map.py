"""Reports of verified requirement coverage, with legacy metrics kept separate."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import io
import math
from typing import List

from ..storage.audit_db import guard_history_row


def _rows(history: List[dict]) -> List[dict]:
    """Use the same history guard as REST/DB; old accepts are not current accepts."""
    return [guard_history_row(row if isinstance(row, dict) else {}) for row in history]


def _number(value: object) -> float | None:
    if type(value) in (int, float):
        try:
            return value if math.isfinite(value) else None
        except OverflowError:
            return None
    return None


def _format_number(value: object) -> str:
    number = _number(value)
    return f"{number:.3f}" if number is not None else "未確定"


def generate_text_report(history: List[dict]) -> str:
    """Render current coverage without substituting zero for unresolved values."""
    if not history:
        return "データなし"

    rows = _rows(history)
    lines = ["=" * 60]
    lines.append("UGH Audit Requirement Coverage Report")
    lines.append(f"生成: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append(f"総計: {len(rows)} 件")
    lines.append("=" * 60)
    lines.append("C は指示要件の記載充足率。事実性や回答品質の保証ではありません。")
    lines.append("保留・根拠不足の行は未確定。旧数値は非拘束の診断です。")

    verdict_counts: dict = {}
    for row in rows:
        verdict = row["verdict"]
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1

    resolved = sum(row["verdict"] != "degraded" for row in rows)
    lines.append("\n集計サマリー")
    lines.append(f"  照合確定: {resolved} / {len(rows)} 件（欠落確定も含む）")
    lines.append(f"  保留・未検証: {len(rows) - resolved} 件")
    lines.append("  未確定行を除いた平均充足率は計算しません。")
    lines.append("\n  現行 verdict 分布:")
    for verdict, count in sorted(verdict_counts.items()):
        lines.append(f"    {verdict:12s} {count} 件")

    lines.append("\n入力順の末尾20件（要件記載充足）")
    lines.append(f"  {'時刻':20s} {'C':>6} {'verdict':12s} {'要レビュー':8s} {'元schema'}")
    for row in rows[-20:]:
        timestamp = str(row.get("created_at") or "未記録")
        timestamp = timestamp.replace("\n", " ").replace("\r", " ")[:19]
        source = str(row.get("source_schema_version") or "未記録")
        source = source.replace("\n", " ").replace("\r", " ")
        review = "必要" if row["requires_review"] else "不要"
        lines.append(
            f"  {timestamp:20s} {_format_number(row['C']):>6} "
            f"{row['verdict']:12s} {review:8s} {source}"
        )

    lines.append("=" * 60)
    return "\n".join(lines)


def _csv_cell(value: object) -> str | int | float:
    """Use explicit nulls and neutralize formula-like string cells in spreadsheets."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if type(value) in (int, float):
        return value if _number(value) is not None else "null"
    text = str(value)
    if (text.lstrip(" \t\r\n\v\f\ufeff").startswith(("=", "+", "-", "@"))
            or text.startswith(("\t", "\r", "\n"))):
        return "'" + text
    return text


def generate_csv(history: List[dict]) -> str:
    """Export guarded schema 4 rows; legacy columns are nonbinding diagnostics.

    Missing values are the literal ``null``. Added scope/provenance columns make
    the meanings explicit; formula-like string cells receive an apostrophe.
    """
    fields = ["created_at", "S", "C", "delta_e", "quality_score", "verdict",
              "schema_version", "source_schema_version", "decision_scope", "metrics_scope",
              "requirement_verdict", "requires_review", "is_reliable", "hit_rate"]
    legacy_fields = ["S", "C", "delta_e", "quality_score", "verdict"]
    output = io.StringIO(newline="")
    # Python 3.10 quotes characters present in the line terminator. Including
    # both CR and LF keeps embedded bare CR inside one cell on every version.
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(fields + ["legacy_" + name for name in legacy_fields])
    for row in _rows(history):
        legacy = row.get("legacy_metrics") or {}
        values = [row.get(name) for name in fields]
        values += [legacy.get(name) if name == "verdict" else _number(legacy.get(name))
                   for name in legacy_fields]
        writer.writerow([_csv_cell(value) for value in values])
    return output.getvalue()

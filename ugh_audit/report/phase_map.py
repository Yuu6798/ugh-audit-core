"""
ugh_audit/report/phase_map.py
Phase Map レポート生成 — ΔE / quality_score の時系列可視化
"""
from __future__ import annotations

from datetime import datetime
from typing import List


def generate_text_report(history: List[dict]) -> str:
    """テキスト形式のPhase Mapレポートを生成"""
    if not history:
        return "データなし"

    lines = ["=" * 60]
    lines.append("UGH Audit Phase Map Report")
    lines.append(f"生成: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"総計: {len(history)} 件")
    lines.append("=" * 60)

    avg_de = sum(r.get("delta_e", 0) for r in history) / len(history)
    avg_qs = sum(r.get("quality_score", 0) for r in history) / len(history)

    verdict_counts: dict = {}
    for r in history:
        v = r.get("verdict", "unknown")
        verdict_counts[v] = verdict_counts.get(v, 0) + 1

    lines.append("\n集計サマリー")
    lines.append(f"  平均 ΔE:            {avg_de:.3f}")
    lines.append(f"  平均 quality_score: {avg_qs:.3f}")
    lines.append("\n  verdict 分布:")
    for v, count in sorted(verdict_counts.items(), key=lambda x: -x[1]):
        bar = "#" * count
        lines.append(f"    {v:12s} {bar} ({count}件)")

    lines.append("\n時系列 (ΔE推移)")
    lines.append(f"  {'時刻':20s} {'S':>6} {'C':>6} {'ΔE':>6} {'QS':>6} {'verdict'}")
    lines.append(f"  {'-'*20} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*12}")
    for r in history[-20:]:
        ts = r.get("created_at", "")[:19]
        s = r.get("S", 0)
        c = r.get("C", 0)
        de = r.get("delta_e", 0)
        qs = r.get("quality_score", 0)
        verdict = r.get("verdict", "unknown")
        lines.append(f"  {ts:20s} {s:6.3f} {c:6.3f} {de:6.3f} {qs:6.3f} {verdict}")

    lines.append("=" * 60)
    return "\n".join(lines)


def generate_csv(history: List[dict]) -> str:
    """CSV形式でエクスポート"""
    headers = ["created_at", "S", "C", "delta_e", "quality_score", "verdict"]
    lines = [",".join(headers)]
    for r in history:
        row = [
            r.get("created_at", ""),
            str(r.get("S", 0)),
            str(r.get("C", 0)),
            str(r.get("delta_e", 0)),
            str(r.get("quality_score", 0)),
            r.get("verdict", ""),
        ]
        lines.append(",".join(row))
    return "\n".join(lines)

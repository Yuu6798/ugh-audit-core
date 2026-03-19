"""
ugh_audit/report/phase_map.py
Phase Map レポート生成 — ΔE / PoR の時系列可視化
"""
from __future__ import annotations
from typing import List
from datetime import datetime


def generate_text_report(history: List[dict]) -> str:
    """テキスト形式のPhase Mapレポートを生成"""
    if not history:
        return "データなし"

    lines = ["=" * 60]
    lines.append("UGH Audit Phase Map Report")
    lines.append(f"生成: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"総計: {len(history)} 件")
    lines.append("=" * 60)

    # 集計
    fired = sum(1 for r in history if r.get("por_fired"))
    avg_por = sum(r.get("por", 0) for r in history) / len(history)
    avg_de = sum(r.get("delta_e", 0) for r in history) / len(history)

    drift_counts = {}
    for r in history:
        d = r.get("meaning_drift", "不明")
        drift_counts[d] = drift_counts.get(d, 0) + 1

    lines.append("\n📊 集計サマリー")
    lines.append(f"  平均 PoR:    {avg_por:.3f}  (発火率: {fired}/{len(history)})")
    lines.append(f"  平均 ΔE:     {avg_de:.3f}")
    lines.append("\n  意味ズレ分布:")
    for drift, count in sorted(drift_counts.items(), key=lambda x: -x[1]):
        bar = "█" * count
        lines.append(f"    {drift:10s} {bar} ({count}件)")

    lines.append("\n📈 時系列 (ΔE推移)")
    lines.append(f"  {'時刻':20s} {'PoR':>6} {'ΔE':>6} {'発火':>4} {'ドリフト'}")
    lines.append(f"  {'-'*20} {'-'*6} {'-'*6} {'-'*4} {'-'*10}")
    for r in history[-20:]:  # 直近20件
        ts = r.get("created_at", "")[:19]
        por = r.get("por", 0)
        de = r.get("delta_e", 0)
        fired_mark = "🔥" if r.get("por_fired") else "○"
        drift = r.get("meaning_drift", "不明")
        lines.append(f"  {ts:20s} {por:6.3f} {de:6.3f} {fired_mark:>4} {drift}")

    lines.append("=" * 60)
    return "\n".join(lines)


def generate_csv(history: List[dict]) -> str:
    """CSV形式でエクスポート"""
    headers = ["created_at", "session_id", "model_id", "por", "por_fired",
               "delta_e", "meaning_drift"]
    lines = [",".join(headers)]
    for r in history:
        row = [
            r.get("created_at", ""),
            r.get("session_id", ""),
            r.get("model_id", ""),
            str(r.get("por", 0)),
            str(r.get("por_fired", 0)),
            str(r.get("delta_e", 0)),
            r.get("meaning_drift", ""),
        ]
        lines.append(",".join(row))
    return "\n".join(lines)

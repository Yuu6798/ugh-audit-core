#!/usr/bin/env python3
"""
export_phase_c.py — Phase C スコア結果をCSV + HTMLレポートに出力

Usage:
    python3 scripts/export_phase_c.py \\
        --input ~/.ugh_audit/phase_c_v0/phase_c_scored.jsonl \\
        --version v0 \\
        --outdir ~/.ugh_audit/phase_c_v0/
"""

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path


def export_csv(records: list[dict], output_path: Path) -> None:
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "id", "category", "role", "difficulty", "temperature",
            "question", "response", "reference", "reference_core",
            "trap_type", "por", "delta_e", "por_fired",
            "meaning_drift", "dominant_gravity",
        ])
        for r in records:
            s = r["scores"]
            writer.writerow([
                r["id"], r["category"], r["role"], r["difficulty"], r["temperature"],
                r["question"], r["response"], r["reference"], r.get("reference_core", ""),
                r.get("trap_type", ""),
                s["por"], s["delta_e"], s["por_fired"],
                s["meaning_drift"], s["dominant_gravity"] or "",
            ])
    print(f"CSV: {output_path}")


def export_html(records: list[dict], output_path: Path, version: str) -> None:
    r0 = [r for r in records if r["temperature"] == 0.0]
    if not r0:
        print("temp=0.0 のレコードがありません")
        return

    pors = [r["scores"]["por"] for r in r0]
    des = [r["scores"]["delta_e"] for r in r0]
    fired = sum(1 for r in r0 if r["scores"]["por_fired"])

    cat_scores: dict = defaultdict(list)
    for r in r0:
        cat_scores[r["category"]].append(r["scores"]["por"])

    cat_rows = ""
    for cat, scores in sorted(cat_scores.items(), key=lambda x: -sum(x[1]) / len(x[1])):
        avg = sum(scores) / len(scores)
        bar = int(avg * 200)
        color = "#4caf50" if avg >= 0.82 else "#ff9800" if avg >= 0.75 else "#f44336"
        cat_rows += (
            f'<tr><td>{cat}</td><td>{avg:.3f}</td>'
            f'<td><div style="width:{bar}px;height:16px;background:{color};border-radius:3px"></div></td></tr>'
        )

    table_rows = ""
    for r in sorted(r0, key=lambda x: x["scores"]["por"]):
        s = r["scores"]
        por_color = "#4caf50" if s["por_fired"] else "#ff9800" if s["por"] >= 0.75 else "#f44336"
        de_color = "#f44336" if s["delta_e"] > 0.5 else "#ff9800"
        table_rows += f"""
    <tr>
      <td><strong>{r['id']}</strong></td>
      <td><span class="tag">{r['category']}</span></td>
      <td>{r['difficulty']}</td>
      <td style="color:{por_color};font-weight:bold">{s['por']:.3f}</td>
      <td style="color:{de_color}">{s['delta_e']:.3f}</td>
      <td>{s['dominant_gravity'] or '-'}</td>
      <td style="max-width:300px;font-size:12px">{r['question'][:80]}...</td>
    </tr>"""

    backend = r0[0].get("scoring_meta", {}).get("backend", "unknown")
    ref_field = r0[0].get("scoring_meta", {}).get("reference_field", "unknown")

    html = f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8">
<title>UGH Audit Phase C {version} — GPT-4o スコアリング結果</title>
<style>
body{{font-family:'Helvetica Neue',Arial,sans-serif;margin:0;padding:20px;background:#f5f5f5;color:#333}}
h1{{color:#2c3e50;border-bottom:3px solid #3498db;padding-bottom:10px}}
h2{{color:#34495e;margin-top:30px}}
.summary{{display:flex;gap:20px;flex-wrap:wrap;margin:20px 0}}
.card{{background:white;border-radius:8px;padding:20px;min-width:160px;box-shadow:0 2px 6px rgba(0,0,0,0.1);text-align:center}}
.card .val{{font-size:2em;font-weight:bold;color:#3498db}}
.card .label{{color:#666;font-size:0.9em;margin-top:4px}}
table{{width:100%;border-collapse:collapse;background:white;border-radius:8px;overflow:hidden;box-shadow:0 2px 6px rgba(0,0,0,0.1)}}
th{{background:#2c3e50;color:white;padding:10px 12px;text-align:left;font-size:13px}}
td{{padding:8px 12px;border-bottom:1px solid #eee;font-size:13px;vertical-align:top}}
tr:hover{{background:#f9f9f9}}
.tag{{background:#3498db;color:white;padding:2px 8px;border-radius:12px;font-size:11px}}
.note{{background:#fff3cd;border-left:4px solid #ffc107;padding:12px;margin:20px 0;border-radius:0 4px 4px 0}}
.meta{{color:#888;font-size:12px;margin-bottom:20px}}
</style>
</head><body>
<h1>🔬 UGH Audit Phase C {version} — GPT-4o スコアリング結果</h1>
<p class="meta">
  モデル: gpt-4o ／ 問題数: {len(r0)}問 ／ 総レコード: {len(records)}件（×3温度）<br>
  backend: {backend} ／ reference_field: {ref_field}
</p>

<div class="summary">
  <div class="card"><div class="val">{sum(pors)/len(pors):.3f}</div><div class="label">PoR 平均</div></div>
  <div class="card"><div class="val">{fired}/{len(r0)}</div><div class="label">PoR発火（≥0.82）</div></div>
  <div class="card"><div class="val">{fired/len(r0)*100:.0f}%</div><div class="label">発火率</div></div>
  <div class="card"><div class="val">{sum(des)/len(des):.3f}</div><div class="label">ΔE 平均</div></div>
</div>

<h2>📊 カテゴリ別 PoR平均</h2>
<table>
<tr><th>カテゴリ</th><th>PoR平均</th><th>視覚化</th></tr>
{cat_rows}
</table>

<h2>📋 全問スコア一覧（PoR昇順）</h2>
<table>
<tr><th>ID</th><th>カテゴリ</th><th>難易度</th><th>PoR</th><th>ΔE</th><th>grv上位語</th><th>質問</th></tr>
{table_rows}
</table>

<p style="color:#aaa;font-size:12px;margin-top:30px">
  Generated by ugh-audit-core {version} / UGH理論 Phase C データ収集
</p>
</body></html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"HTML: {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase C 結果エクスポート")
    parser.add_argument("--input", required=True, help="スコア済みJSONLファイルパス")
    parser.add_argument("--version", default="v0", help="バージョン識別子（v0/v1等）")
    parser.add_argument("--outdir", required=True, help="出力ディレクトリ")
    args = parser.parse_args()

    records = []
    with open(args.input) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    print(f"入力: {len(records)}件")
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    export_csv(records, outdir / f"phase_c_results_{args.version}.csv")
    export_html(records, outdir / f"phase_c_report_{args.version}.html", args.version)
    print("完了")


if __name__ == "__main__":
    main()
